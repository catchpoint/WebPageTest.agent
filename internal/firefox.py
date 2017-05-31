# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for webdriver browsers"""
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
import glob
import gzip
import logging
import os
import Queue
import re
import shutil
import subprocess
import threading
import time
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
        self.log_pos = {}
        self.page = {}
        self.requests = {}
        self.last_activity = monotonic.monotonic()

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
                self.process_command(command)
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

    def process_message(self, message):
        """Process a message from the extension"""
        logging.debug(message)
        if self.recording:
            self.last_activity = monotonic.monotonic()
            try:
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
        task['start_time'] = datetime.utcnow()

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        self.recording = False
        DesktopBrowser.on_stop_recording(self, task)
        if self.connected:
            if self.job['pngss']:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.png')
                self.grab_screenshot(screen_shot, png=True)
            else:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
                self.grab_screenshot(screen_shot, png=False, resize=600)
        # Copy the log files
        if self.moz_log is not None:
            task['moz_log'] = os.path.join(task['dir'], task['prefix'] + '_moz.log')
            files = sorted(glob.glob(self.moz_log + '*'))
            for path in files:
                dest = os.path.join(task['dir'],
                                    task['prefix'] + '_' + os.path.basename(path) + '.gz')
                start_pos = self.log_pos[path] if path in self.log_pos else 0
                with open(path, 'rb') as f_in:
                    f_in.seek(start_pos)
                    with gzip.open(dest, 'wb', 7) as f_out:
                        shutil.copyfileobj(f_in, f_out)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        DesktopBrowser.on_start_processing(self, task)
        # Parse the moz log for the accurate request timings
        request_timings = []
        if 'moz_log' in task:
            from internal.support.firefox_log_parser import FirefoxLogParser
            parser = FirefoxLogParser()
            request_timings = parser.process_logs(task['moz_log'], task['start_time'])
        # Build the request and page data
        self.process_requests(request_timings, task)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DesktopBrowser.wait_for_processing(self, task)

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.marionette.navigate(command['target'])
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
            self.marionette.execute_js_script(command['target'])
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
            data = self.marionette.screenshot(format='binary')
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
        pass


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
        self.message_server.handle_message({'path': self.path.lstrip('/'), 'body': None})

    def do_HEAD(self):
        """HTTP HEAD"""
        self._set_headers()
        self.message_server.handle_message({'path': self.path.lstrip('/'), 'body': None})

    def do_POST(self):
        """HTTP POST"""
        try:
            content_len = int(self.headers.getheader('content-length', 0))
            body = json.loads(self.rfile.read(content_len)) if content_len > 0 else None
            self._set_headers()
            self.message_server.handle_message({'path': self.path.lstrip('/'), 'body': body})
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
