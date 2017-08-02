# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for webdriver browsers"""
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta
import glob
import gzip
import logging
import os
import platform
import Queue
import re
import shutil
import subprocess
import threading
import time
import urlparse
import monotonic
import ujson as json
from .desktop_browser import DesktopBrowser

""" Orange page that changes itself to white on navigation
<html>
<head>
<style>
body {background-color: white; margin: 0;}
#o {width:100%; height: 100%; background-color: #DE640D;}
</style>
<script>
window.addEventListener('beforeunload', function() {
  var o = document.getElementById('o')
  o.parentNode.removeChild(o);
});
</script>
</head>
<body><div id='o'></div></body>
</html>
"""
START_PAGE = 'data:text/html,%3Chtml%3E%0D%0A%3Chead%3E%0D%0A%3Cstyle%3E%0D%0Abody%20%7B'\
             'background-color%3A%20white%3B%20margin%3A%200%3B%7D%0D%0A%23o%20%7Bwidth'\
             '%3A100%25%3B%20height%3A%20100%25%3B%20background-color%3A%20%23DE640D%3B'\
             '%7D%0D%0A%3C%2Fstyle%3E%0D%0A%3Cscript%3E%0D%0Awindow.addEventListener%28%27'\
             'beforeunload%27%2C%20function%28%29%20%7B%0D%0A%20%20var%20o%20%3D%20'\
             'document.getElementById%28%27o%27%29%0D%0A%20%20o.parentNode.removeChild'\
             '%28o%29%3B%0D%0A%7D%29%3B%0D%0A%3C%2Fscript%3E%0D%0A%3C%2Fhead%3E%0D%0A%3Cbody%3E%3C'\
             'div%20id%3D%27o%27%3E%3C%2Fdiv%3E%3C%2Fbody%3E%0D%0A%3C%2Fhtml%3E'

class Firefox(DesktopBrowser):
    """Firefox"""

    def __init__(self, path, options, job):
        DesktopBrowser.__init__(self, path, options, job)
        self.job = job
        self.task = None
        self.options = options
        self.path = path
        self.event_name = None
        self.moz_log = None
        self.marionette = None
        self.addons = None
        self.extension_id = None
        self.extension = None
        self.nav_error = None
        self.page_loaded = None
        self.recording = False
        self.connected = False
        self.start_offset = None
        self.log_pos = {}
        self.page = {}
        self.requests = {}
        self.last_activity = monotonic.monotonic()
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        self.moz_log = os.path.join(task['dir'], 'moz.log')
        self.log_pos = {}
        self.page = {}
        self.requests = {}
        os.environ["MOZ_LOG_FILE"] = self.moz_log
        os.environ["MOZ_LOG"] = 'timestamp,sync,nsHttp:5,nsSocketTransport:5'\
                                'nsHostResolver:5,pipnss:5'
        DesktopBrowser.prepare(self, job, task)
        profile_template = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                        'support', 'Firefox', 'profile')
        if not task['cached'] and os.path.isdir(profile_template):
            try:
                if os.path.isdir(task['profile']):
                    shutil.rmtree(task['profile'])
                shutil.copytree(profile_template, task['profile'])
            except Exception:
                pass

    def launch(self, _job, task):
        """Launch the browser"""
        self.connected = False
        self.extension = MessageServer()
        self.extension.start()
        from marionette_driver.marionette import Marionette
        from marionette_driver.addons import Addons
        args = ['-profile', '"{0}"'.format(task['profile']),
                '-no-remote',
                '-marionette',
                'about:blank']
        if self.path.find(' ') > -1:
            command_line = '"{0}"'.format(self.path)
        else:
            command_line = self.path
        command_line += ' ' + ' '.join(args)
        DesktopBrowser.launch_browser(self, command_line)
        self.marionette = Marionette('localhost', port=2828)
        self.marionette.start_session(timeout=self.task['time_limit'])
        logging.debug('Installing extension')
        self.addons = Addons(self.marionette)
        extension_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                      'support', 'Firefox', 'extension')
        self.extension_id = self.addons.install(extension_path, temp=True)
        logging.debug('Resizing browser to %dx%d', task['width'], task['height'])
        self.marionette.set_window_position(x=0, y=0)
        self.marionette.set_window_size(height=task['height'], width=task['width'])
        self.marionette.navigate(START_PAGE)
        time.sleep(0.5)
        self.wait_for_extension()
        if self.connected:
            DesktopBrowser.wait_for_idle(self)

    def stop(self, job, task):
        """Kill the browser"""
        if self.extension_id is not None and self.addons is not None:
            self.addons.uninstall(self.extension_id)
            self.extension_id = None
            self.addons = None
        if self.marionette is not None:
            self.marionette.close()
            self.marionette = None
        DesktopBrowser.stop(self, job, task)
        self.extension.stop()
        self.extension = None
        # Make SURE the firefox processes are gone
        if platform.system() == "Linux":
            subprocess.call(['killall', '-9', 'firefox'])
            subprocess.call(['killall', '-9', 'firefox-trunk'])
        os.environ["MOZ_LOG_FILE"] = ''
        os.environ["MOZ_LOG"] = ''
        # delete the raw log files
        if self.moz_log is not None:
            files = sorted(glob.glob(self.moz_log + '*'))
            for path in files:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def run_task(self, task):
        """Run an individual test"""
        if self.marionette is not None and self.connected:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic.monotonic() + task['time_limit']
            task['current_step'] = 1
            recording = False
            while len(task['script']) and task['error'] is None and \
                    monotonic.monotonic() < end_time:
                self.prepare_task(task)
                command = task['script'].pop(0)
                if not recording and command['record']:
                    recording = True
                    self.on_start_recording(task)
                try:
                    self.process_command(command)
                except Exception:
                    logging.exception("Exception running task")
                if command['record']:
                    self.wait_for_page_load()
                    if not task['combine_steps'] or not len(task['script']):
                        self.on_stop_recording(task)
                        recording = False
                        self.on_start_processing(task)
                        self.wait_for_processing(task)
                        if task['log_data']:
                            # Move on to the next step
                            task['current_step'] += 1
                            self.event_name = None
                    task['navigated'] = True
            # Always navigate to about:blank after finishing in case the tab is
            # remembered across sessions
            self.marionette.navigate('about:blank')
            self.task = None

    def wait_for_extension(self):
        """Wait for the extension to send the started message"""
        if self.extension is not None:
            end_time = monotonic.monotonic()  + 30
            while monotonic.monotonic() < end_time:
                try:
                    self.extension.get_message(1)
                    logging.debug('Extension started')
                    self.connected = True
                    break
                except Exception:
                    pass

    def wait_for_page_load(self):
        """Wait for the onload event from the extension"""
        if self.extension is not None and self.connected:
            start_time = monotonic.monotonic()
            end_time = start_time + self.task['time_limit']
            done = False
            while not done:
                try:
                    self.process_message(self.extension.get_message(1))
                except Exception:
                    pass
                now = monotonic.monotonic()
                elapsed_test = now - start_time
                if self.nav_error is not None:
                    done = True
                    if self.page_loaded is None:
                        self.task['error'] = self.nav_error
                elif now >= end_time:
                    done = True
                    # only consider it an error if we didn't get a page load event
                    if self.page_loaded is None:
                        self.task['error'] = "Page Load Timeout"
                elif 'time' not in self.job or elapsed_test > self.job['time']:
                    elapsed_activity = now - self.last_activity
                    elapsed_page_load = now - self.page_loaded if self.page_loaded else 0
                    if elapsed_page_load >= 1 and elapsed_activity >= self.task['activity_time']:
                        done = True
                    elif self.task['error'] is not None:
                        done = True

    def execute_js(self, script):
        """Run javascipt (stub for overriding"""
        ret = None
        if self.marionette is not None:
            ret = self.marionette.execute_script('return ' + script, script_timeout=30)
        return ret

    def run_js_file(self, file_name):
        """Execute one of our js scripts"""
        ret = None
        script = None
        script_file_path = os.path.join(self.script_dir, file_name)
        if os.path.isfile(script_file_path):
            with open(script_file_path, 'rb') as script_file:
                script = script_file.read()
        if script is not None:
            ret = self.marionette.execute_script('return ' + script, script_timeout=30)
            if ret is not None:
                logging.debug(ret)
        return ret

    def collect_browser_metrics(self, task):
        """Collect all of the in-page browser metrics that we need"""
        logging.debug("Collecting user timing metrics")
        user_timing = self.run_js_file('user_timing.js')
        if user_timing is not None:
            path = os.path.join(task['dir'], task['prefix'] + '_timed_events.json.gz')
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json.dumps(user_timing))
        logging.debug("Collecting page-level metrics")
        page_data = self.run_js_file('page_data.js')
        if page_data is not None:
            task['page_data'].update(page_data)
        if 'customMetrics' in self.job:
            custom_metrics = {}
            for name in self.job['customMetrics']:
                logging.debug("Collecting custom metric %s", name)
                script = 'var wptCustomMetric = function() {' +\
                         self.job['customMetrics'][name] +\
                         '};try{return wptCustomMetric();}catch(e){};'
                custom_metrics[name] = self.marionette.execute_script(script, script_timeout=30)
                if custom_metrics[name] is not None:
                    logging.debug(custom_metrics[name])
            path = os.path.join(task['dir'], task['prefix'] + '_metrics.json.gz')
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json.dumps(custom_metrics))

    def process_message(self, message):
        """Process a message from the extension"""
        logging.debug(message)
        if self.recording:
            self.last_activity = monotonic.monotonic()
            try:
                # Make all of the timestamps relative to the test start to match the log events
                if 'timeStamp' in message['body']:
                    timestamp = message['body']['timeStamp']
                    seconds = int(timestamp / 1000)
                    milliseconds = timestamp - (seconds * 1000)
                    event_time = datetime.utcfromtimestamp(seconds)
                    event_time += timedelta(milliseconds=milliseconds)
                    elapsed = event_time - self.task['start_time']
                    message['body']['timeStamp'] = elapsed.total_seconds()
                cat, msg = message['path'].split('.', 1)
                if cat == 'webNavigation':
                    self.process_web_navigation(msg, message['body'])
                elif cat == 'webRequest':
                    self.process_web_request(msg, message['body'])
            except Exception:
                pass

    def process_web_navigation(self, message, evt):
        """Handle webNavigation.*"""
        if evt is not None:
            if message == 'onBeforeNavigate':
                if 'frameId' in evt and evt['frameId'] == 0:
                    self.page_loaded = None
                    logging.debug("Starting navigation")
                    if 'timeStamp' in evt and 'start' not in self.page:
                        self.page['start'] = evt['timeStamp']
            elif message == 'onCommitted':
                if 'timeStamp' in evt and 'frameId' in evt and evt['frameId'] == 0 \
                        and 'committed' not in self.page:
                    self.page['committed'] = evt['timeStamp']
            elif message == 'onDOMContentLoaded':
                if 'timeStamp' in evt and 'frameId' in evt and evt['frameId'] == 0:
                    self.page['DOMContentLoaded'] = evt['timeStamp']
            elif message == 'onCompleted':
                if 'frameId' in evt and evt['frameId'] == 0:
                    self.page_loaded = monotonic.monotonic()
                    logging.debug("Page loaded")
                    if 'timeStamp' in evt:
                        self.page['loaded'] = evt['timeStamp']
            elif message == 'onErrorOccurred':
                if 'frameId' in evt and evt['frameId'] == 0:
                    self.page_loaded = monotonic.monotonic()
                    logging.debug("Page load failed")
                    if 'error' in evt:
                        self.nav_error = evt['error']
                    else:
                        self.nav_error = 'Navigation failed'

    def process_web_request(self, message, evt):
        """Handle webNavigation.*"""
        if evt is not None and 'requestId' in evt and 'timeStamp' in evt:
            if evt['requestId'] not in self.requests:
                self.requests[evt['requestId']] = {'id': evt['requestId'],
                                                   'from_net': True}
            request = self.requests[evt['requestId']]
            if 'url' in evt and 'url' not in request:
                request['url'] = evt['url']
            if 'method' in evt and 'method' not in request:
                request['method'] = evt['method']
            if 'type' in evt and 'type' not in request:
                request['type'] = evt['type']
            if 'ip' in evt and 'ip' not in request:
                request['ip'] = evt['ip']
            if 'fromCache' in evt and evt['fromCache']:
                request['from_net'] = False
            if 'statusLine' in evt:
                request['status_line'] = evt['statusLine']
            if 'statusCode' in evt:
                request['status'] = evt['statusCode']
            if 'requestHeaders' in evt and 'request_headers' not in request:
                request['request_headers'] = list(evt['requestHeaders'])
            if 'responseHeaders' in evt and 'response_headers' not in request:
                request['response_headers'] = list(evt['responseHeaders'])

            if message == 'onBeforeRequest':
                request['created'] = evt['timeStamp']
            elif message == 'onSendHeaders':
                request['start'] = evt['timeStamp']
            elif message == 'onBeforeRedirect':
                if 'first_byte' not in request:
                    request['first_byte'] = evt['timeStamp']
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
            elif message == 'onHeadersReceived':
                if 'first_byte' not in request:
                    request['first_byte'] = evt['timeStamp']
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
            elif message == 'onResponseStarted':
                if 'first_byte' not in request:
                    request['first_byte'] = evt['timeStamp']
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
            elif message == 'onCompleted':
                if 'first_byte' not in request:
                    request['first_byte'] = evt['timeStamp']
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
            elif message == 'onErrorOccurred':
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
                if 'error' in evt:
                    request['error'] = evt['error']
                if 'status' not in request:
                    request['status'] = 12999

    def prepare_task(self, task):
        """Format the file prefixes for multi-step testing"""
        task['page_data'] = {}
        if task['current_step'] == 1:
            task['prefix'] = task['task_prefix']
            task['video_subdirectory'] = task['task_video_prefix']
        else:
            task['prefix'] = '{0}_{1:d}'.format(task['task_prefix'], task['current_step'])
            task['video_subdirectory'] = '{0}_{1:d}'.format(task['task_video_prefix'],
                                                            task['current_step'])
        if task['video_subdirectory'] not in task['video_directories']:
            task['video_directories'].append(task['video_subdirectory'])
        if self.event_name is not None:
            task['step_name'] = self.event_name
        else:
            task['step_name'] = 'Step_{0:d}'.format(task['current_step'])

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        # Mark the start point in the various log files
        self.log_pos = {}
        if self.moz_log is not None:
            files = sorted(glob.glob(self.moz_log + '*'))
            for path in files:
                self.log_pos[path] = os.path.getsize(path)
        self.recording = True
        now = monotonic.monotonic()
        if not self.task['stop_at_onload']:
            self.last_activity = now
        if self.page_loaded is not None:
            self.page_loaded = now
        DesktopBrowser.on_start_recording(self, task)
        logging.debug('Starting measurement')
        task['start_time'] = datetime.utcnow()

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        self.recording = False
        DesktopBrowser.on_stop_recording(self, task)
        if self.connected:
            if self.job['pngScreenShot']:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.png')
                self.grab_screenshot(screen_shot, png=True)
            else:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
                self.grab_screenshot(screen_shot, png=False, resize=600)
        # Collect end of test data from the browser
        self.collect_browser_metrics(task)
        # Copy the log files
        if self.moz_log is not None:
            task['moz_log'] = os.path.join(task['dir'], task['prefix'] + '_moz.log')
            files = sorted(glob.glob(self.moz_log + '*'))
            for path in files:
                try:
                    base_name = os.path.basename(path)
                    dest = os.path.join(task['dir'],
                                        task['prefix'] + '_' + base_name + '.gz')
                    start_pos = self.log_pos[path] if path in self.log_pos else 0
                    end_pos = os.path.getsize(path)
                    if end_pos > start_pos:
                        length = end_pos - start_pos
                        logging.debug('Preparing moz log %s (%d bytes from %d)',
                                      base_name, length, start_pos)
                        with open(path, 'rb') as f_in:
                            f_in.seek(start_pos)
                            with gzip.open(dest, 'wb', 7) as f_out:
                                while length > 0:
                                    read_bytes = min(length, 1024 * 1024)
                                    buff = f_in.read(read_bytes)
                                    read_bytes = len(buff)
                                    f_out.write(buff)
                                    length -= read_bytes
                except Exception:
                    pass

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        DesktopBrowser.on_start_processing(self, task)
        # Parse the moz log for the accurate request timings
        request_timings = []
        if 'moz_log' in task:
            from internal.support.firefox_log_parser import FirefoxLogParser
            parser = FirefoxLogParser()
            start_time = task['start_time'].strftime('%Y-%m-%d %H:%M:%S.%f')
            logging.debug('Parsing moz logs relative to %s start time', start_time)
            request_timings = parser.process_logs(task['moz_log'], start_time)
            if len(request_timings) and task['current_step'] == 1:
                self.adjust_timings(request_timings)
            files = sorted(glob.glob(task['moz_log'] + '*'))
            for path in files:
                try:
                    os.remove(path)
                except Exception:
                    pass
        # Build the request and page data
        self.process_requests(request_timings, task)

    def adjust_timings(self, requests):
        """Adjust the request timings to start at zero for the earliest timestamp"""
        timestamps = ['dns_start', 'dns_end', 'connect_start', 'connect_end',
                      'ssl_start', 'ssl_end', 'start', 'first_byte', 'end']
        earliest = None
        for request in requests:
            for entry in timestamps:
                if entry in request:
                    if earliest is None or request[entry] < earliest:
                        earliest = request[entry]
        if earliest is not None and earliest > 0:
            self.start_offset = earliest
            for request in requests:
                for entry in timestamps:
                    if entry in request:
                        request[entry] -= earliest

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DesktopBrowser.wait_for_processing(self, task)

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            url = str(command['target']).replace('"', '\"')
            script = 'window.location="{0}";'.format(url)
            self.marionette.execute_script(script)
        elif command['command'] == 'logdata':
            self.task['combine_steps'] = False
            if int(re.search(r'\d+', str(command['target'])).group()):
                logging.debug("Data logging enabled")
                self.task['log_data'] = True
            else:
                logging.debug("Data logging disabled")
                self.task['log_data'] = False
        elif command['command'] == 'combinesteps':
            self.task['log_data'] = True
            self.task['combine_steps'] = True
        elif command['command'] == 'seteventname':
            self.event_name = command['target']
        elif command['command'] == 'exec':
            self.marionette.execute_script(command['target'])
        elif command['command'] == 'sleep':
            delay = min(60, max(0, int(re.search(r'\d+', str(command['target'])).group())))
            if delay > 0:
                time.sleep(delay)
        elif command['command'] == 'setabm':
            self.task['stop_at_onload'] = \
                bool('target' in command and int(re.search(r'\d+',
                                                           str(command['target'])).group()) == 0)
        elif command['command'] == 'setactivitytimeout':
            if 'target' in command:
                self.task['activity_time'] = \
                    max(0, min(30, int(re.search(r'\d+', str(command['target'])).group())))
        elif command['command'] == 'setuseragent':
            self.task['user_agent_string'] = command['target']
        elif command['command'] == 'setcookie':
            if 'target' in command and 'value' in command:
                url = command['target'].strip()
                cookie = command['value']
                pos = cookie.find(';')
                if pos > 0:
                    cookie = cookie[:pos]
                pos = cookie.find('=')
                if pos > 0:
                    name = cookie[:pos].strip()
                    value = cookie[pos+1:].strip()
                    if len(name) and len(value) and len(url):
                        self.marionette.add_cookie({'url': url, 'name': name, 'value': value})

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.marionette is not None:
            self.marionette.navigate(url)

    def grab_screenshot(self, path, png=True, resize=0):
        """Save the screen shot (png or jpeg)"""
        if self.marionette is not None:
            data = self.marionette.screenshot(format='binary', full=False)
            if data is not None:
                resize_string = '' if not resize else '-resize {0:d}x{0:d} '.format(resize)
                if png:
                    with open(path, 'wb') as image_file:
                        image_file.write(data)
                    if len(resize_string):
                        cmd = 'mogrify -format png -define png:color-type=2 '\
                              '-depth 8 {0}"{1}"'.format(resize_string, path)
                        logging.debug(cmd)
                        subprocess.call(cmd, shell=True)
                else:
                    tmp_file = path + '.png'
                    with open(tmp_file, 'wb') as image_file:
                        image_file.write(data)
                    command = 'convert "{0}" {1}-quality {2:d} "{3}"'.format(
                        tmp_file, resize_string, self.job['iq'], path)
                    logging.debug(command)
                    subprocess.call(command, shell=True)
                    if os.path.isfile(tmp_file):
                        try:
                            os.remove(tmp_file)
                        except Exception:
                            pass

    def process_requests(self, request_timings, task):
        """Convert all of the request and page events into the format needed for WPT"""
        result = {}
        result['requests'] = self.merge_requests(request_timings)
        result['pageData'] = self.calculate_page_stats(result['requests'])
        devtools_file = os.path.join(task['dir'], task['prefix'] + '_devtools_requests.json.gz')
        with gzip.open(devtools_file, 'wb', 7) as f_out:
            json.dump(result, f_out)

    def get_empty_request(self, request_id, url):
        """Return and empty, initialized request"""
        parts = urlparse.urlsplit(url)
        request = {'type': 3,
                   'id': request_id,
                   'request_id': request_id,
                   'ip_addr': '',
                   'full_url': url,
                   'is_secure': 1 if parts.scheme == 'https' else 0,
                   'method': '',
                   'host': parts.netloc,
                   'url': parts.path,
                   'responseCode': -1,
                   'load_start': -1,
                   'load_ms': -1,
                   'ttfb_ms': -1,
                   'dns_start': -1,
                   'dns_end': -1,
                   'dns_ms': -1,
                   'connect_start': -1,
                   'connect_end': -1,
                   'connect_ms': -1,
                   'ssl_start': -1,
                   'ssl_end': -1,
                   'ssl_ms': -1,
                   'bytesIn': 0,
                   'bytesOut': 0,
                   'objectSize': 0,
                   'initiator': '',
                   'initiator_line': '',
                   'initiator_column': '',
                   'server_rtt': None,
                   'headers': {'request': [], 'response': []},
                   'score_cache': -1,
                   'score_cdn': -1,
                   'score_gzip': -1,
                   'score_cookies': -1,
                   'score_keep-alive': -1,
                   'score_minify': -1,
                   'score_combine': -1,
                   'score_compress': -1,
                   'score_etags': -1,
                   'gzip_total': None,
                   'gzip_save': None,
                   'minify_total': None,
                   'minify_save': None,
                   'image_total': None,
                   'image_save': None,
                   'cache_time': None,
                   'cdn_provider': None,
                   'server_count': None,
                   'socket': -1
                  }
        if len(parts.query):
            request['url'] += '?' + parts.query
        return request

    def get_header_value(self, headers, name):
        """Return the value for the given header"""
        value = ''
        name = name.lower()
        for header in headers:
            pos = header.find(':')
            if pos > 0:
                key = header[0:pos].lower()
                if key.startswith(name):
                    val = header[pos + 1:].strip()
                    if len(value):
                        value += '; '
                    value += val
        return value

    def merge_requests(self, request_timings):
        """Merge the requests from the extension and log files"""
        requests = []
        # Start with the requests reported from the extension
        for req_id in self.requests:
            req = self.requests[req_id]
            if req['from_net'] and 'start' in req and 'url' in req:
                request = self.get_empty_request(req['id'], req['url'])
                if 'ip' in req:
                    request['ip_addr'] = req['ip']
                if 'method' in req:
                    request['method'] = req['method']
                if 'status' in req:
                    request['responseCode'] = req['status']
                if 'type' in req:
                    request['requestType'] = req['type']
                if 'request_headers' in req:
                    for header in req['request_headers']:
                        if 'name' in header and 'value' in header:
                            header_text = '{0}: {1}'.format(header['name'], header['value'])
                            request['bytesOut'] += len(header_text) + 2
                            request['headers']['request'].append(header_text)
                if 'status_line' in req:
                    request['bytesIn'] += len(req['status_line']) + 2
                    request['headers']['response'].append(req['status_line'])
                if 'response_headers' in req:
                    for header in req['response_headers']:
                        if 'name' in header and 'value' in header:
                            header_text = '{0}: {1}'.format(header['name'], header['value'])
                            request['bytesIn'] += len(header_text) + 2
                            request['headers']['response'].append(header_text)
                if 'created' in req:
                    request['created'] = req['created']
                request['load_start'] = int(round(req['start'] * 1000.0))
                if 'first_byte' in req:
                    ttfb = int(round((req['first_byte'] - req['start']) * 1000.0))
                    request['ttfb_ms'] = max(0, ttfb)
                if 'end' in req:
                    load_time = int(round((req['end'] - req['start']) * 1000.0))
                    request['load_ms'] = max(0, load_time)
                size = self.get_header_value(request['headers']['response'], 'Content-Length')
                if len(size):
                    request['bytesIn'] += int(re.search(r'\d+', str(size)).group())
                requests.append(request)
        # Overwrite them with the same requests from the logs
        for request in requests:
            for req in request_timings:
                if 'claimed' not in req and 'url' in req and 'full_url' in request \
                        and 'start' in req and request['full_url'] == req['url']:
                    req['claimed'] = True
                    self.populate_request(request, req)
        # Add any events from the logs that weren't reported by the extension
        for req in request_timings:
            if 'claimed' not in req and 'url' in req and 'start' in req:
                request = self.get_empty_request(req['id'], req['url'])
                self.populate_request(request, req)
                requests.append(request)
        # parse values out of the headers
        for request in requests:
            request['expires'] = self.get_header_value(request['headers']['response'], 'Expires')
            request['cacheControl'] = self.get_header_value(request['headers']['response'],
                                                            'Cache-Control')
            request['contentType'] = self.get_header_value(request['headers']['response'],
                                                           'Content-Type')
            request['contentEncoding'] = self.get_header_value(request['headers']['response'],
                                                               'Content-Encoding')
            request['objectSize'] = self.get_header_value(request['headers']['response'],
                                                          'Content-Length')
        requests.sort(key=lambda x: x['load_start'])
        return requests

    def populate_request(self, request, log_request):
        """Populate a request object from the log request values"""
        request['load_start'] = int(log_request['start'] * 1000)
        if 'status' in log_request:
            request['responseCode'] = log_request['status']
        if 'dns_start' in log_request and log_request['dns_start'] >= 0:
            request['dns_start'] = int(log_request['dns_start'] * 1000)
        if 'dns_end' in log_request and log_request['dns_end'] >= 0:
            request['dns_end'] = int(round(log_request['dns_end'] * 1000.0))
        if 'connect_start' in log_request and log_request['connect_start'] >= 0:
            request['connect_start'] = int(log_request['connect_start'] * 1000)
        if 'connect_end' in log_request and log_request['connect_end'] >= 0:
            request['connect_end'] = int(round(log_request['connect_end'] * 1000.0))
        if 'connection' in log_request:
            request['socket'] = log_request['connection']
        request['load_start'] = int(round(log_request['start'] * 1000.0))
        if 'first_byte' in log_request:
            request['ttfb_ms'] = int(round((log_request['first_byte'] - \
                                            log_request['start']) * 1000.0))
        if 'end' in log_request:
            request['load_ms'] = int(round((log_request['end'] - \
                                            log_request['start']) * 1000.0))
        if 'bytes_in' in log_request:
            request['bytesIn'] = log_request['bytes_in']
        if 'request_headers' in log_request:
            request['headers']['request'] = list(log_request['request_headers'])
        if 'response_headers' in log_request:
            request['headers']['response'] = list(log_request['response_headers'])

    def calculate_page_stats(self, requests):
        """Calculate the page-level stats"""
        page = {'loadTime': 0,
                'docTime': 0,
                'fullyLoaded': 0,
                'bytesOut': 0,
                'bytesOutDoc': 0,
                'bytesIn': 0,
                'bytesInDoc': 0,
                'requests': len(requests),
                'requestsDoc': 0,
                'responses_200': 0,
                'responses_404': 0,
                'responses_other': 0,
                'result': 0,
                'testStartOffset': 0,
                'cached': 1 if self.task['cached'] else 0,
                'optimization_checked': 0,
                'start_epoch': int((self.task['start_time'] - \
                                    datetime.utcfromtimestamp(0)).total_seconds())
               }
        if 'loaded' in self.page:
            page['loadTime'] = int(round(self.page['loaded'] * 1000.0))
            page['docTime'] = page['loadTime']
            page['loadEventStart'] = page['loadTime']
            page['loadEventEnd'] = page['loadTime']
        if 'DOMContentLoaded' in self.page:
            page['domContentLoadedEventStart'] = int(round(self.page['DOMContentLoaded'] * 1000.0))
            page['domContentLoadedEventEnd'] = page['domContentLoadedEventStart']

        main_request = None
        index = 0
        for request in requests:
            if request['load_ms'] >= 0:
                end_time = request['load_start'] + request['load_ms']
                if end_time > page['fullyLoaded']:
                    page['fullyLoaded'] = end_time
                if end_time <= page['loadTime']:
                    page['requestsDoc'] += 1
                    page['bytesInDoc'] += request['bytesIn']
                    page['bytesOutDoc'] += request['bytesOut']
            page['bytesIn'] += request['bytesIn']
            page['bytesOut'] += request['bytesOut']
            if request['responseCode'] == 200:
                page['responses_200'] += 1
            elif request['responseCode'] == 404:
                page['responses_404'] += 1
                page['result'] = 99999
            elif request['responseCode'] > -1:
                page['responses_other'] += 1
            if main_request is None and \
                    (request['responseCode'] == 200 or request['responseCode'] == 304):
                main_request = request['id']
                request['is_base_page'] = True
                page['final_base_page_request'] = index
                page['final_base_page_request_id'] = main_request
                page['final_url'] = request['full_url']
                if request['ttfb_ms'] >= 0:
                    page['TTFB'] = request['load_start'] + request['ttfb_ms']
                if request['ssl_end'] >= request['ssl_start'] and \
                        request['ssl_start'] >= 0:
                    page['basePageSSLTime'] = int(round(request['ssl_end'] - \
                                                        request['ssl_start']))
        if page['responses_200'] == 0 and len(requests):
            if 'responseCode' in requests[0]:
                page['result'] = requests[0]['responseCode']
            else:
                page['result'] = 12999
        return page

class HandleMessage(BaseHTTPRequestHandler):
    """Handle a single message from the extension"""
    protocol_version = 'HTTP/1.1'

    def __init__(self, server, *args):
        self.message_server = server
        try:
            BaseHTTPRequestHandler.__init__(self, *args)
        except Exception:
            pass

    def log_message(self, format, *args):
        return

    def _set_headers(self):
        """Basic response headers"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header("Content-length", 0)
        self.end_headers()

    # pylint: disable=C0103
    def do_GET(self):
        """HTTP GET"""
        self._set_headers()

    def do_HEAD(self):
        """HTTP HEAD"""
        self._set_headers()

    def do_POST(self):
        """HTTP POST"""
        try:
            content_len = int(self.headers.getheader('content-length', 0))
            messages = self.rfile.read(content_len) if content_len > 0 else None
            self._set_headers()
            for line in messages.splitlines():
                line = line.strip()
                if len(line):
                    message = json.loads(line)
                    if 'body' not in message:
                        message['body'] = None
                    self.message_server.handle_message(message)
        except Exception:
            pass
    # pylint: enable=C0103

def handler_template(server):
    """Stub that lets us pass parameters to BaseHTTPRequestHandler"""
    return lambda *args: HandleMessage(server, *args)

class MessageServer(object):
    """Local HTTP server for interacting with the extension"""
    def __init__(self):
        self.server = None
        self.must_exit = False
        self.thread = None
        self.messages = Queue.Queue()

    def get_message(self, timeout):
        """Get a single message from the queue"""
        message = self.messages.get(block=True, timeout=timeout)
        self.messages.task_done()
        return message

    def handle_message(self, message):
        """Add a received message to the queue"""
        self.messages.put(message)

    def start(self):
        """Start running the server in a background thread"""
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def stop(self):
        """Stop running the server"""
        logging.debug("Shutting down extension server")
        self.must_exit = True
        if self.server is not None:
            try:
                self.server.server_close()
            except Exception:
                pass
        if self.thread is not None:
            self.thread.join()
        logging.debug("Extension server stopped")

    def run(self):
        """Main server loop"""
        handler = handler_template(self)
        logging.debug('Starting extension server on port 8888')
        self.server = HTTPServer(('127.0.0.1', 8888), handler)
        self.server.timeout = 0.5
        try:
            while not self.must_exit:
                self.server.handle_request()
        except Exception:
            pass
        self.server = None
