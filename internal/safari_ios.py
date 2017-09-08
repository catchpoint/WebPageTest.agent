# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Support for Safari on iOS using iWptBrowser"""
from datetime import datetime
import gzip
import logging
import os
import platform
import Queue
import re
import subprocess
import time
import monotonic
import ujson as json
from ws4py.client.threadedclient import WebSocketClient

class iWptBrowser(object):
    """iOS"""

    def __init__(self, ios_device, options, job):
        self.job = job
        self.task = None
        self.options = options
        self.ios = ios_device
        self.event_name = None
        self.nav_error = None
        self.page_loaded = None
        self.recording = False
        self.connected = False
        self.browser_version = None
        self.messages = Queue.Queue()
        self.video_processing = None
        self.page = {}
        self.requests = {}
        self.last_activity = monotonic.monotonic()
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')
        self.websocket = None
        self.command_id = 0
        self.command_responses = {}
        self.pending_commands = []
        self.webinspector_proxy = None
        self.ios_utils_path = None
        plat = platform.system()
        if plat == "Darwin":
            self.ios_utils_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                               'support', 'ios', 'Darwin')
        elif plat == "Linux":
            if os.uname()[4].startswith('arm'):
                self.ios_utils_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                                   'support', 'ios', 'arm')
            else:
                self.ios_utils_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                                   'support', 'ios', 'linux64')

    def prepare(self, job, task):
        """Prepare the OS for the browser"""
        self.page = {}
        self.requests = {}
        self.ios.notification_queue = self.messages
        self.ios.stop_browser()
        if not task['cached']:
            self.clear_profile(task)

    def clear_profile(self, _):
        """Clear the browser profile"""
        self.ios.clear_cache()

    def flush_messages(self):
        """Flush all of the pending messages"""
        try:
            while True:
                self.messages.get_nowait()
                self.messages.task_done()
        except Exception:
            pass

    def launch(self, _job, task):
        """Launch the browser"""
        self.connected = False
        self.flush_messages()
        if self.ios_utils_path and self.ios.start_browser():
            # Start the webinspector proxy
            exe = os.path.join(self.ios_utils_path, 'ios_webkit_debug_proxy')
            args = [exe, '-F', '-u', self.ios.serial]
            logging.debug(' '.join(args))
            self.webinspector_proxy = subprocess.Popen(args)
            if self.webinspector_proxy:
                # Connect to the dev tools interface
                self.connected = self.connect()
        self.flush_messages()

    def connect(self, timeout=30):
        """Connect to the dev tools interface"""
        import requests
        ret = False
        end_time = monotonic.monotonic() + timeout
        while not ret and monotonic.monotonic() < end_time:
            try:
                response = requests.get("http://localhost:9222/json", timeout=timeout)
                if len(response.text):
                    tabs = response.json()
                    logging.debug("Dev Tools tabs: %s", json.dumps(tabs))
                    if len(tabs):
                        websocket_url = None
                        for index in xrange(len(tabs)):
                            if 'webSocketDebuggerUrl' in tabs[index]:
                                websocket_url = tabs[index]['webSocketDebuggerUrl']
                                break
                        if websocket_url is not None:
                            try:
                                self.websocket = DevToolsClient(websocket_url)
                                self.websocket.messages = self.messages
                                self.websocket.connect()
                                ret = True
                            except Exception as err:
                                logging.debug("Connect to dev tools websocket Error: %s",
                                              err.__str__())
                            if not ret:
                                # try connecting to 127.0.0.1 instead of localhost
                                try:
                                    websocket_url = websocket_url.replace('localhost', '127.0.0.1')
                                    self.websocket = DevToolsClient(websocket_url)
                                    self.websocket.messages = self.messages
                                    self.websocket.connect()
                                    ret = True
                                except Exception as err:
                                    logging.debug("Connect to dev tools websocket Error: %s",
                                                  err.__str__())
                        else:
                            time.sleep(0.5)
                    else:
                        time.sleep(0.5)
            except Exception as err:
                logging.debug("Connect to dev tools Error: %s", err.__str__())
                time.sleep(0.5)
        return ret        

    def stop(self, job, task):
        """Kill the browser"""
        if self.websocket:
            try:
                self.websocket.close()
            except Exception:
                pass
            self.websocket = None
        self.ios.stop_browser()

    def run_task(self, task):
        """Run an individual test"""
        if self.connected:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic.monotonic() + task['time_limit']
            task['current_step'] = 1
            recording = False
            while task['script'] and task['error'] is None and \
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
                        self.step_complete(task)
                        if task['log_data']:
                            # Move on to the next step
                            task['current_step'] += 1
                            self.event_name = None
                    task['navigated'] = True
            self.task = None

    def wait_for_extension(self):
        """Wait for the extension to send the started message"""
        if self.job['message_server'] is not None:
            end_time = monotonic.monotonic()  + 30
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
            while not done:
                try:
                    self.process_message(self.messages.get(timeout=1))
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
        if self.connected:
            ret = self.ios.execute_js(script)
        return ret

    def run_js_file(self, file_name):
        """Execute one of our js scripts"""
        ret = None
        if self.connected:
            script = None
            script_file_path = os.path.join(self.script_dir, file_name)
            if os.path.isfile(script_file_path):
                with open(script_file_path, 'rb') as script_file:
                    script = script_file.read()
            if script is not None:
                ret = self.ios.execute_js(script)
        return ret

    def collect_browser_metrics(self, task):
        """Collect all of the in-page browser metrics that we need"""
        logging.debug("Collecting user timing metrics")
        user_timing = self.run_js_file('user_timing.js')
        logging.debug(user_timing)
        if user_timing is not None:
            path = os.path.join(task['dir'], task['prefix'] + '_timed_events.json.gz')
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json.dumps(user_timing))
        logging.debug("Collecting page-level metrics")
        page_data = self.run_js_file('page_data.js')
        logging.debug(page_data)
        if page_data is not None:
            task['page_data'].update(page_data)
        if 'customMetrics' in self.job:
            custom_metrics = {}
            for name in self.job['customMetrics']:
                logging.debug("Collecting custom metric %s", name)
                script = '(function() {' +\
                         self.job['customMetrics'][name] +\
                         '})()'
                try:
                    custom_metrics[name] = self.ios.execute_js(script)
                    if custom_metrics[name] is not None:
                        logging.debug(custom_metrics[name])
                except Exception:
                    pass
            path = os.path.join(task['dir'], task['prefix'] + '_metrics.json.gz')
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json.dumps(custom_metrics))

    def process_message(self, message):
        """Process a message from the browser"""
        if self.recording:
            if 'msg' in message and message['msg'].startswith('page.'):
                self.last_activity = monotonic.monotonic()
                if message['msg'] == 'page.didFinish' or message['msg'] == 'page.loadingFinished':
                    self.page_loaded = monotonic.monotonic()
            elif 'method' in message:
                parts = message['method'].split('.')
                if len(parts) >= 2:
                    category = parts[0]
                    event = parts[1]
        if 'id' in message:
            response_id = int(re.search(r'\d+', str(message['id'])).group())
            if response_id in self.pending_commands:
                self.pending_commands.remove(response_id)
                self.command_responses[response_id] = message

    def prepare_task(self, task):
        """Format the file prefixes for multi-step testing"""
        self.page = {}
        self.requests = {}
        task['page_data'] = {'date': time.time()}
        task['run_start_time'] = monotonic.monotonic()
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
        self.flush_messages()
        self.send_command('Page.enable', {})
        self.send_command('Inspector.enable', {})
        self.send_command('Network.enable', {})
        self.send_command('Inspector.enable', {})
        if self.task['log_data']:
            self.send_command('Console.enable', {})
            if 'timeline' in self.job and self.job['timeline']:
                self.send_command('Timeline.start', {})
            self.ios.show_orange()
            task['video_file'] = os.path.join(task['dir'], task['prefix']) + '_video.mp4'
            self.ios.start_video()
            if self.browser_version is not None and 'browserVersion' not in task['page_data']:
                task['page_data']['browserVersion'] = self.browser_version
                task['page_data']['browser_version'] = self.browser_version
        self.recording = True
        now = monotonic.monotonic()
        if not self.task['stop_at_onload']:
            self.last_activity = now
        if self.page_loaded is not None:
            self.page_loaded = now
        logging.debug('Starting measurement')
        task['start_time'] = datetime.utcnow()

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        self.recording = False
        self.send_command('Page.disable', {})
        self.send_command('Inspector.disable', {})
        self.send_command('Network.disable', {})
        self.send_command('Inspector.disable', {})
        if self.task['log_data']:
            self.send_command('Console.disable', {})
            if 'timeline' in self.job and self.job['timeline']:
                self.send_command('Timeline.stop', {})
            if self.job['pngScreenShot']:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.png')
                self.grab_screenshot(screen_shot, png=True)
            else:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
                self.grab_screenshot(screen_shot, png=False, resize=600)
            # Grab the video and kick off processing async
            if 'video_file' in task:
                self.ios.stop_video(task['video_file'])
                video_path = os.path.join(task['dir'], task['video_subdirectory'])
                support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
                if task['current_step'] == 1:
                    filename = '{0:d}.{1:d}.histograms.json.gz'.format(task['run'], task['cached'])
                else:
                    filename = '{0:d}.{1:d}.{2:d}.histograms.json.gz'.format(task['run'],
                                                                             task['cached'],
                                                                             task['current_step'])
                histograms = os.path.join(task['dir'], filename)
                visualmetrics = os.path.join(support_path, "visualmetrics.py")
                args = ['python', visualmetrics, '-vvvv', '-i', task['video_file'],
                        '-d', video_path, '--force', '--quality', '{0:d}'.format(self.job['iq']),
                        '--viewport', '--orange', '--maxframes', '50', '--histogram', histograms]
                if 'renderVideo' in self.job and self.job['renderVideo']:
                    video_out = os.path.join(task['dir'], task['prefix']) + '_rendered_video.mp4'
                    args.extend(['--render', video_out])
                logging.debug(' '.join(args))
                self.video_processing = subprocess.Popen(args)
            # Collect end of test data from the browser
            self.collect_browser_metrics(task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        #TODO: Remove this when real processing is hooked up
        task['page_data']['result'] = 0
        task['page_data']['visualTest'] = 1
        self.process_requests(task)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        if self.video_processing is not None:
            logging.debug('Waiting for video processing to finish')
            self.video_processing.communicate()
            self.video_processing = None
            if not self.job['keepvideo']:
                try:
                    os.remove(task['video_file'])
                except Exception:
                    pass

    def step_complete(self, task):
        """Final step processing"""
        # Write out the accumulated page_data
        if task['log_data'] and task['page_data']:
            if 'browser' in self.job:
                task['page_data']['browser_name'] = self.job['browser']
            if 'step_name' in task:
                task['page_data']['eventName'] = task['step_name']
            if 'run_start_time' in task:
                task['page_data']['test_run_time_ms'] = \
                        int(round((monotonic.monotonic() - task['run_start_time']) * 1000.0))
            path = os.path.join(task['dir'], task['prefix'] + '_page_data.json.gz')
            json_page_data = json.dumps(task['page_data'])
            logging.debug('Page Data: %s', json_page_data)
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json_page_data)

    def send_command(self, method, params, wait=False, timeout=10):
        """Send a raw dev tools message and optionally wait for the response"""
        ret = None
        if self.websocket:
            self.command_id += 1
            command_id = int(self.command_id)
            if wait:
                self.pending_commands.append(command_id)
            msg = {'id': command_id, 'method': method, 'params': params}
            try:
                out = json.dumps(msg)
                logging.debug("Sending: %s", out)
                self.websocket.send(out)
                if wait:
                    end_time = monotonic.monotonic() + timeout
                    while ret is None and monotonic.monotonic() < end_time:
                        try:
                            msg = self.messages.get(timeout=1)
                            if msg:
                                logging.debug(msg)
                                self.process_message(msg)
                                if command_id in self.command_responses:
                                    ret = self.command_responses[command_id]
                                    del self.command_responses[command_id]
                        except Exception:
                            pass
            except Exception as err:
                logging.debug("Websocket send error: %s", err.__str__())
        return ret


    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.ios.navigate(command['target'])
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
            self.ios.execute_js(command['target'], remove_orange=self.recording)
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
        elif command['command'] == 'setcookie':
            pass

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.connected:
            self.ios.navigate(url)

    def grab_screenshot(self, path, png=True, resize=0):
        """Save the screen shot (png or jpeg)"""
        if self.connected:
            data = self.ios.screenshot()
            if data:
                resize_string = '' if not resize else '-resize {0:d}x{0:d} '.format(resize)
                if png:
                    with open(path, 'wb') as image_file:
                        image_file.write(data)
                    if resize_string:
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

    def process_requests(self, task):
        """Convert all of the request and page events into the format needed for WPT"""
        result = {}
        result['requests'] = []
        result['pageData'] = self.calculate_page_stats(result['requests'])
        devtools_file = os.path.join(task['dir'], task['prefix'] + '_devtools_requests.json.gz')
        with gzip.open(devtools_file, 'wb', 7) as f_out:
            json.dump(result, f_out)

    def calculate_page_stats(self, requests):
        """Calculate the page-level stats"""
        page = {'loadTime': 0,
                'docTime': 0,
                'fullyLoaded': 0,
                'bytesOut': 0,
                'bytesOutDoc': 0,
                'bytesIn': 0,
                'bytesInDoc': 0,
                'requests': 0,
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
        if 'loadEventStart' in self.task['page_data']:
            page['docTime'] = self.task['page_data']['loadEventStart']
        return page

class DevToolsClient(WebSocketClient):
    """DevTools Websocket client"""
    def __init__(self, url, protocols=None, extensions=None, heartbeat_freq=None,
                 ssl_options=None, headers=None):
        WebSocketClient.__init__(self, url, protocols, extensions, heartbeat_freq,
                                 ssl_options, headers)
        self.connected = False
        self.messages = None
        self.trace_file = None

    def opened(self):
        """Websocket interface - connection opened"""
        logging.debug("DevTools websocket connected")
        self.connected = True

    def closed(self, code, reason=None):
        """Websocket interface - connection closed"""
        logging.debug("DevTools websocket disconnected")
        self.connected = False

    def received_message(self, raw):
        """Websocket interface - message received"""
        try:
            if raw.is_text:
                message = raw.data.decode(raw.encoding) if raw.encoding is not None else raw.data
                logging.debug(message[:200])
                if message:
                    message = json.loads(message)
                    if message:
                        self.messages.put(message)
        except Exception:
            pass
