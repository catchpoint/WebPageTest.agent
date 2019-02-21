# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Support for Firefox"""
from datetime import datetime, timedelta
import glob
import gzip
import logging
import os
import platform
import re
import shutil
import subprocess
import time
import urlparse
import monotonic
import ujson as json
from .desktop_browser import DesktopBrowser


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
        self.possible_navigation_error = None
        self.nav_error = None
        self.page_loaded = None
        self.recording = False
        self.connected = False
        self.start_offset = None
        self.browser_version = None
        self.main_request_headers = None
        self.log_pos = {}
        self.log_level = 5
        if 'browser_info' in job and 'log_level' in job['browser_info']:
            self.log_level = job['browser_info']['log_level']
        self.page = {}
        self.requests = {}
        self.last_activity = monotonic.monotonic()
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')
        self.start_page = 'http://127.0.0.1:8888/orange.html'

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        self.moz_log = os.path.join(task['dir'], 'moz.log')
        self.log_pos = {}
        self.page = {}
        self.requests = {}
        self.main_request_headers = None
        os.environ["MOZ_LOG_FILE"] = self.moz_log
        moz_log_env = 'timestamp,nsHttp:{0:d},nsSocketTransport:{0:d}'\
                      'nsHostResolver:{0:d},pipnss:5'.format(self.log_level)
        os.environ["MOZ_LOG"] = moz_log_env
        logging.debug('MOZ_LOG = %s', moz_log_env)
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
        # Delete any unsent crash reports
        crash_dir = None
        if platform.system() == 'Windows':
            if 'APPDATA' in os.environ:
                crash_dir = os.path.join(os.environ['APPDATA'],
                                         'Mozilla', 'Firefox', 'Crash Reports')
        else:
            crash_dir = os.path.join(os.path.expanduser('~'),
                                     '.mozilla', 'firefox', 'Crash Reports')
        if crash_dir and os.path.isdir(crash_dir):
            logging.debug("Clearing crash reports in %s", crash_dir)
            try:
                shutil.rmtree(crash_dir)
            except Exception:
                pass
        # Prepare the config for the extension to query
        if self.job['message_server'] is not None:
            config = None
            names = ['block',
                     'block_domains',
                     'block_domains_except',
                     'headers',
                     'cookies',
                     'overrideHosts']
            for name in names:
                if name in task and task[name]:
                    if config is None:
                        config = {}
                    config[name] = task[name]
            self.job['message_server'].config = config

    def disable_fsync(self, command_line):
        """Use eatmydata if it is installed to disable fsync"""
        if platform.system() == 'Linux':
            try:
                cmd = ['eatmydata', 'date']
                logging.debug(' '.join(cmd))
                subprocess.check_call(cmd)
                command_line = 'eatmydata ' + command_line
            except Exception as err:
                pass
        return command_line

    def launch(self, job, task):
        """Launch the browser"""
        if self.job['message_server'] is not None:
            self.job['message_server'].flush_messages()
        self.connected = False
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
        command_line = self.disable_fsync(command_line)
        DesktopBrowser.launch_browser(self, command_line)
        try:
            self.marionette = Marionette('localhost', port=2828)
            capabilities = None
            if 'ignoreSSL' in job and job['ignoreSSL']:
                capabilities = {'acceptInsecureCerts': True}
            self.marionette.start_session(timeout=self.task['time_limit'], capabilities=capabilities)
            self.configure_prefs()
            logging.debug('Installing extension')
            self.addons = Addons(self.marionette)
            extension_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                          'support', 'Firefox', 'extension')
            self.extension_id = self.addons.install(extension_path, temp=True)
            logging.debug('Resizing browser to %dx%d', task['width'], task['height'])
            self.marionette.set_window_rect(x=0, y=0, height=task['height'], width=task['width'])
            if 'browserVersion' in self.marionette.session_capabilities:
                self.browser_version = self.marionette.session_capabilities['browserVersion']
            self.marionette.navigate(self.start_page)
            time.sleep(0.5)
            self.wait_for_extension()
            if self.connected:
                # Override the UA String if necessary
                ua_string = self.execute_js('navigator.userAgent;')
                modified = False
                if 'uastring' in self.job:
                    ua_string = self.job['uastring']
                    modified = True
                if ua_string is not None and 'AppendUA' in task:
                    ua_string += ' ' + task['AppendUA']
                    modified = True
                if modified:
                    logging.debug(ua_string)
                    self.marionette.set_pref('general.useragent.override', ua_string)
                # Location
                if 'lat' in self.job and 'lng' in self.job:
                    try:
                        lat = float(str(self.job['lat']))
                        lng = float(str(self.job['lng']))
                        location_uri = 'data:application/json,{{'\
                            '"status":"OK","accuracy":10.0,'\
                            '"location":{{"lat":{0:f},"lng":{1:f}}}'\
                            '}}'.format(lat, lng)
                        logging.debug('Setting location: %s', location_uri)
                        self.set_pref('geo.wifi.uri', location_uri)
                    except Exception:
                        pass
                # Figure out the native viewport size
                size = self.execute_js("[window.innerWidth, window.innerHeight]")
                logging.debug(size)
                if size is not None and len(size) == 2:
                    task['actual_viewport'] = {"width": size[0], "height": size[1]}
                    if 'adjust_viewport' in job and job['adjust_viewport']:
                        delta_x = max(task['width'] - size[0], 0)
                        delta_y = max(task['height'] - size[1], 0)
                        if delta_x or delta_y:
                            width = task['width'] + delta_x
                            height = task['height'] + delta_y
                            logging.debug('Resizing browser to %dx%d', width, height)
                            self.marionette.set_window_rect(x=0, y=0, height=height, width=width)
                # Wait for the browser startup to finish
                DesktopBrowser.wait_for_idle(self)
        except Exception as err:
            logging.exception("Error starting Firefox")
            task['error'] = 'Error starting Firefox: {0}'.format(err.__str__())

    def get_pref_value(self, value):
        """Convert a JSON pref value to Python"""
        str_match = re.match(r'^"(.*)"$', value)
        if value == 'true':
            value = True
        elif value == 'false':
            value = False
        elif re.match(r'^[\d]+$', value):
            value = int(value)
        elif str_match:
            value = str_match.group(1)
        else:
            value = None
        return value

    def configure_prefs(self):
        """Load the prefs file and configure them through Marionette"""
        prefs = {}
        prefs_file = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                  'support', 'Firefox', 'profile', 'prefs.js')
        with open(prefs_file) as f_in:
            for line in f_in:
                matches = re.search(r'user_pref\("([^"]+)",[\s]*([^\)]*)[\s]*\);', line)
                if matches:
                    key = matches.group(1).strip()
                    value = self.get_pref_value(matches.group(2).strip())
                    if value is not None:
                        prefs[key] = value
        if prefs:
            try:
                self.marionette.set_prefs(prefs, True)
            except Exception:
                pass

    def close_browser(self, job, task):
        """Terminate the browser but don't do all of the cleanup that stop does"""
        if self.extension_id is not None and self.addons is not None:
            try:
                self.addons.uninstall(self.extension_id)
            except Exception:
                pass
            self.extension_id = None
            self.addons = None
        if self.marionette is not None:
            try:
                self.marionette.close()
            except Exception:
                pass
            self.marionette = None
        DesktopBrowser.close_browser(self, job, task)
        # make SURE the Firefox processes are gone
        if platform.system() == "Linux":
            subprocess.call(['killall', '-9', 'firefox'])
            subprocess.call(['killall', '-9', 'firefox-trunk'])
        os.environ["MOZ_LOG_FILE"] = ''
        os.environ["MOZ_LOG"] = ''

    def stop(self, job, task):
        """Kill the browser"""
        self.close_browser(job, task)
        DesktopBrowser.stop(self, job, task)
        # delete the raw log files
        if self.moz_log is not None:
            files = sorted(glob.glob(self.moz_log + '*'))
            for path in files:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def run_lighthouse_test(self, task):
        """Stub for lighthouse test"""
        pass

    def run_task(self, task):
        """Run an individual test"""
        if self.marionette is not None and self.connected:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic.monotonic() + task['test_time_limit']
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
                        self.on_stop_capture(task)
                        self.on_stop_recording(task)
                        recording = False
                        self.on_start_processing(task)
                        self.wait_for_processing(task)
                        self.step_complete(task)
                        if task['log_data']:
                            # Move on to the next step
                            task['current_step'] += 1
                            self.event_name = None
                    task['navigated'] = True
            # Always navigate to about:blank after finishing in case the tab is
            # remembered across sessions
            try:
                self.marionette.navigate('about:blank')
            except Exception:
                logging.debug('Marionette exception navigating to about:blank after the test')
            self.task = None

    def wait_for_extension(self):
        """Wait for the extension to send the started message"""
        if self.job['message_server'] is not None:
            end_time = monotonic.monotonic() + 30
            while monotonic.monotonic() < end_time:
                try:
                    self.job['message_server'].get_message(1)
                    logging.debug('Extension started')
                    self.connected = True
                    break
                except Exception:
                    pass

    def wait_for_page_load(self):
        """Wait for the onload event from the extension"""
        if self.job['message_server'] is not None and self.connected:
            start_time = monotonic.monotonic()
            end_time = start_time + self.task['time_limit']
            done = False
            interval = 1
            while not done:
                if self.page_loaded is not None:
                    interval = 0.1
                try:
                    self.process_message(self.job['message_server'].get_message(interval))
                except Exception:
                    pass
                now = monotonic.monotonic()
                elapsed_test = now - start_time
                # Allow up to 5 seconds after a navigation for a re-navigation to happen
                # (bizarre sequence Firefox seems to do)
                if self.possible_navigation_error is not None:
                    elapsed_error = now - self.possible_navigation_error['time']
                    if elapsed_error > 5:
                        self.nav_error = self.possible_navigation_error['error']
                if self.nav_error is not None:
                    logging.debug('Navigation error')
                    done = True
                    if self.page_loaded is None:
                        logging.debug('Page not loaded')
                        self.task['error'] = self.nav_error
                        self.task['page_data']['result'] = 12999
                    else:
                        logging.debug('Page loaded')
                elif now >= end_time:
                    done = True
                    # only consider it an error if we didn't get a page load event
                    if self.page_loaded is None:
                        self.task['error'] = "Page Load Timeout"
                        self.task['page_data']['result'] = 99998
                elif 'time' not in self.job or elapsed_test > self.job['time']:
                    elapsed_activity = now - self.last_activity
                    elapsed_page_load = now - self.page_loaded if self.page_loaded else 0
                    if elapsed_page_load >= 1 and elapsed_activity >= self.task['activity_time']:
                        done = True
                    elif self.task['error'] is not None:
                        done = True

    def execute_js(self, script):
        """Run JavaScript"""
        ret = None
        if self.marionette is not None:
            try:
                ret = self.marionette.execute_script('return ' + script, script_timeout=30)
            except Exception:
                pass
        return ret

    def run_js_file(self, file_name):
        """Execute one of our JS scripts"""
        ret = None
        script = None
        script_file_path = os.path.join(self.script_dir, file_name)
        if os.path.isfile(script_file_path):
            with open(script_file_path, 'rb') as script_file:
                script = script_file.read()
        if script is not None:
            try:
                ret = self.marionette.execute_script('return ' + script, script_timeout=30)
            except Exception:
                pass
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
                try:
                    custom_metrics[name] = self.marionette.execute_script(script, script_timeout=30)
                    if custom_metrics[name] is not None:
                        logging.debug(custom_metrics[name])
                except Exception:
                    pass
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
                if 'timeStamp' not in message['body'] or message['body']['timeStamp'] > 0:
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
                    self.possible_navigation_error = None
                    logging.debug("Starting navigation")
                    if 'timeStamp' in evt and 'start' not in self.page:
                        self.page['start'] = evt['timeStamp']
            elif message == 'onCommitted':
                if 'timeStamp' in evt and 'frameId' in evt and evt['frameId'] == 0 \
                        and 'committed' not in self.page:
                    self.page['committed'] = evt['timeStamp']
                if 'injectScript' in self.job and self.marionette is not None:
                    logging.debug("Injecting script: \n%s", self.job['injectScript'])
                    try:
                        self.marionette.execute_script(self.job['injectScript'],
                                                       script_timeout=30)
                    except Exception:
                        pass
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
                    logging.debug("Possible navigation error")
                    err_msg = evt['error'] if 'error' in evt else 'Navigation failed'
                    self.possible_navigation_error = {
                        'time': monotonic.monotonic(),
                        'error': err_msg
                    }

    def process_web_request(self, message, evt):
        """Handle webNavigation.*"""
        if evt is not None and 'requestId' in evt and 'timeStamp' in evt:
            if evt['requestId'] not in self.requests:
                self.requests[evt['requestId']] = {'id': evt['requestId'],
                                                   'from_net': True}
            request = self.requests[evt['requestId']]
            if 'url' in evt and evt['url'] is not None and 'url' not in request:
                request['url'] = evt['url']
            if 'method' in evt and evt['method'] is not None and 'method' not in request:
                request['method'] = evt['method']
            if 'type' in evt and evt['type'] is not None and 'type' not in request:
                request['type'] = evt['type']
            if 'ip' in evt and evt['ip'] is not None and 'ip' not in request:
                request['ip'] = evt['ip']
            if 'fromCache' in evt and evt['fromCache']:
                request['from_net'] = False
            if 'statusLine' in evt and evt['statusLine'] is not None:
                request['status_line'] = evt['statusLine']
            if 'statusCode' in evt and evt['statusCode'] is not None:
                request['status'] = evt['statusCode']
            if 'requestHeaders' in evt and evt['requestHeaders'] is not None and \
                    'request_headers' not in request:
                request['request_headers'] = list(evt['requestHeaders'])
            if 'responseHeaders' in evt and evt['responseHeaders'] is not None and \
                    'response_headers' not in request:
                request['response_headers'] = list(evt['responseHeaders'])
                if self.main_request_headers is None:
                    self.main_request_headers = list(evt['responseHeaders'])

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
        # Clear the state
        self.page = {}
        self.requests = {}
        task['page_data'] = {'date': time.time()}
        task['page_result'] = None
        task['run_start_time'] = monotonic.monotonic()
        if self.browser_version is not None and 'browserVersion' not in task['page_data']:
            task['page_data']['browserVersion'] = self.browser_version
            task['page_data']['browser_version'] = self.browser_version
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

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        DesktopBrowser.on_stop_capture(self, task)
        if 'heroElementTimes' in self.job and self.job['heroElementTimes']:
            hero_elements = None
            custom_hero_selectors = {}
            if 'heroElements' in self.job:
                custom_hero_selectors = self.job['heroElements']
            logging.debug('Collecting hero element positions')
            with open(os.path.join(self.script_dir, 'hero_elements.js'), 'rb') as script_file:
                hero_elements_script = script_file.read()
            script = hero_elements_script + '(' + json.dumps(custom_hero_selectors) + ')'
            hero_elements = self.execute_js(script)
            if hero_elements is not None:
                path = os.path.join(task['dir'], task['prefix'] + '_hero_elements.json.gz')
                with gzip.open(path, 'wb', 7) as outfile:
                    outfile.write(json.dumps(hero_elements))

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
        # Collect the interactive periods
        interactive = self.execute_js('window.wrappedJSObject.wptagentGetInteractivePeriods();')
        if interactive is not None and len(interactive):
            interactive_file = os.path.join(task['dir'], task['prefix'] + '_interactive.json.gz')
            with gzip.open(interactive_file, 'wb', 7) as f_out:
                f_out.write(interactive)
        # Close the browser if we are done testing (helps flush logs)
        if not len(task['script']):
            self.close_browser(self.job, task)
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
            files = sorted(glob.glob(task['moz_log'] + '*'))
            for path in files:
                try:
                    os.remove(path)
                except Exception:
                    pass
        # Build the request and page data
        if len(request_timings) and task['current_step'] >= 1:
            self.adjust_timings(request_timings)
        self.process_requests(request_timings, task)

    def adjust_timings(self, requests):
        """Adjust the request timings to start at zero for the earliest timestamp"""
        timestamps = ['dns_start', 'dns_end', 'connect_start', 'connect_end',
                      'ssl_start', 'ssl_end', 'start', 'first_byte', 'end']
        earliest = None
        for request in requests:
            for entry in timestamps:
                if entry in request and request[entry] >= 0:
                    if earliest is None or request[entry] < earliest:
                        earliest = request[entry]
        logging.debug("Adjusting request timings by %0.3f seconds", earliest)
        if earliest is not None and earliest > 0:
            self.start_offset = earliest
            for request in requests:
                for entry in timestamps:
                    if entry in request and request[entry] >= earliest:
                        request[entry] -= earliest
                if 'chunks' in request:
                    for chunk in request['chunks']:
                        if 'ts' in chunk and chunk['ts'] >= earliest:
                            chunk['ts'] -= earliest

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DesktopBrowser.wait_for_processing(self, task)

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.task['page_data']['URL'] = command['target']
            url = str(command['target']).replace('"', '\"')
            script = 'window.location="{0}";'.format(url)
            script = self.prepare_script_for_record(script)
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
            script = command['target']
            if command['record']:
                script = self.prepare_script_for_record(script)
            self.marionette.execute_script(script)
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
                milliseconds = int(re.search(r'\d+', str(command['target'])).group())
                self.task['activity_time'] = max(0, min(30, float(milliseconds) / 1000.0))
        elif command['command'] == 'setuseragent':
            self.task['user_agent_string'] = command['target']
        elif command['command'] == 'firefoxpref':
            if 'target' in command and 'value' in command:
                self.set_pref(command['target'], command['value'])
        elif command['command'] == 'setlocation':
            try:
                if 'target' in command and command['target'].find(',') > 0:
                    accuracy = 0
                    if 'value' in command and re.match(r'\d+', command['value']):
                        accuracy = int(re.search(r'\d+', str(command['value'])).group())
                    parts = command['target'].split(',')
                    lat = float(parts[0])
                    lng = float(parts[1])
                    location_uri = 'data:application/json,{{'\
                        '"status":"OK","accuracy":{2:d},'\
                        '"location":{{"lat":{0:f},"lng":{1:f}}}'\
                        '}}'.format(lat, lng, accuracy)
                    logging.debug('Setting location: %s', location_uri)
                    self.set_pref('geo.wifi.uri', location_uri)
            except Exception:
                pass

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.marionette is not None:
            try:
                self.marionette.navigate(url)
            except Exception as err:
                logging.debug("Error navigating Firefox: %s", str(err))

    def set_pref(self, key, value_str):
        """Set an individual pref value"""
        value = self.get_pref_value(value_str.strip())
        if value is not None:
            try:
                logging.debug('Setting Pref "%s" to %s', key, value_str)
                self.marionette.set_pref(key, value)
            except Exception:
                pass

    def grab_screenshot(self, path, png=True, resize=0):
        """Save the screen shot (png or jpeg)"""
        if self.marionette is not None:
            try:
                data = self.marionette.screenshot(format='binary', full=False)
                if data is not None:
                    resize_string = '' if not resize else '-resize {0:d}x{0:d} '.format(resize)
                    if png:
                        with open(path, 'wb') as image_file:
                            image_file.write(data)
                        if len(resize_string):
                            cmd = '{0} -format png -define png:color-type=2 '\
                                '-depth 8 {1}"{2}"'.format(self.job['image_magick']['mogrify'],
                                                           resize_string, path)
                            logging.debug(cmd)
                            subprocess.call(cmd, shell=True)
                    else:
                        tmp_file = path + '.png'
                        with open(tmp_file, 'wb') as image_file:
                            image_file.write(data)
                        command = '{0} "{1}" {2}-quality {3:d} "{4}"'.format(
                            self.job['image_magick']['convert'],
                            tmp_file, resize_string, self.job['imageQuality'], path)
                        logging.debug(command)
                        subprocess.call(command, shell=True)
                        if os.path.isfile(tmp_file):
                            try:
                                os.remove(tmp_file)
                            except Exception:
                                pass
            except Exception as err:
                logging.debug('Exception grabbing screen shot: %s', str(err))

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
            try:
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
                                try:
                                    header_text = '{0}: {1}'.format(header['name'], header['value'])
                                    request['bytesIn'] += len(header_text) + 2
                                    request['headers']['response'].append(header_text)
                                except Exception:
                                    pass
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
            except Exception:
                pass
        # Overwrite them with the same requests from the logs
        for request in requests:
            for req in request_timings:
                try:
                    if 'claimed' not in req and 'url' in req and 'full_url' in request \
                            and 'start' in req and request['full_url'] == req['url']:
                        req['claimed'] = True
                        self.populate_request(request, req)
                except Exception:
                    pass
        # Add any events from the logs that weren't reported by the extension
        for req in request_timings:
            try:
                if 'claimed' not in req and 'url' in req and 'start' in req:
                    request = self.get_empty_request(req['id'], req['url'])
                    self.populate_request(request, req)
                    requests.append(request)
            except Exception:
                pass
        # parse values out of the headers
        for request in requests:
            try:
                value = self.get_header_value(request['headers']['response'], 'Expires')
                if value:
                    request['expires'] = value
                value = self.get_header_value(request['headers']['response'], 'Cache-Control')
                if value:
                    request['cacheControl'] = value
                value = self.get_header_value(request['headers']['response'], 'Content-Type')
                if value:
                    request['contentType'] = value
                value = self.get_header_value(request['headers']['response'], 'Content-Encoding')
                if value:
                    request['contentEncoding'] = value
                value = self.get_header_value(request['headers']['response'], 'Content-Length')
                if value:
                    request['objectSize'] = value
            except Exception:
                pass
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
        if 'ssl_start' in log_request and log_request['ssl_start'] >= 0:
            request['ssl_start'] = int(log_request['ssl_start'] * 1000)
        if 'ssl_end' in log_request and log_request['ssl_end'] >= 0:
            request['ssl_end'] = int(round(log_request['ssl_end'] * 1000.0))
        if 'connection' in log_request:
            request['socket'] = log_request['connection']
        request['load_start'] = int(round(log_request['start'] * 1000.0))
        if 'first_byte' in log_request:
            request['ttfb_ms'] = int(round((log_request['first_byte'] -
                                            log_request['start']) * 1000.0))
        if 'end' in log_request:
            request['load_ms'] = int(round((log_request['end'] -
                                            log_request['start']) * 1000.0))
        if 'bytes_in' in log_request:
            request['bytesIn'] = log_request['bytes_in']
        if 'chunks' in log_request and len(log_request['chunks']):
            request['chunks'] = []
            for chunk in log_request['chunks']:
                ts = chunk['ts'] * 1000.0
                request['chunks'].append({'ts': ts, 'bytes': chunk['bytes']})
        if 'request_headers' in log_request:
            request['headers']['request'] = list(log_request['request_headers'])
        if 'response_headers' in log_request:
            request['headers']['response'] = list(log_request['response_headers'])
        if 'http2_stream_id' in log_request:
            request['http2_stream_id'] = log_request['http2_stream_id']
            request['protocol'] = 'HTTP/2'
        if 'http2_stream_dependency' in log_request:
            request['http2_stream_dependency'] = log_request['http2_stream_dependency']
        if 'http2_stream_weight' in log_request:
            request['http2_stream_weight'] = log_request['http2_stream_weight']

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
                'start_epoch': int((self.task['start_time'] -
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
                    (request['responseCode'] == 200 or request['responseCode'] == 304) and \
                    ('contentType' not in request or
                     (request['contentType'] != 'application/ocsp-response' and
                      request['contentType'] != 'application/pkix-crl')):
                main_request = request['id']
                request['is_base_page'] = True
                page['final_base_page_request'] = index
                page['final_base_page_request_id'] = main_request
                page['final_url'] = request['full_url']
                if 'URL' not in self.task['page_data']:
                    self.task['page_data']['URL'] = page['final_url']
                if request['ttfb_ms'] >= 0:
                    page['TTFB'] = request['load_start'] + request['ttfb_ms']
                if request['ssl_end'] >= request['ssl_start'] and \
                        request['ssl_start'] >= 0:
                    page['basePageSSLTime'] = int(round(request['ssl_end'] -
                                                        request['ssl_start']))
        if page['responses_200'] == 0 and len(requests):
            if 'responseCode' in requests[0]:
                page['result'] = requests[0]['responseCode']
            else:
                page['result'] = 12999
        self.task['page_result'] = page['result']
        return page
