# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Support for Safari on iOS using iWptBrowser"""
import base64
from datetime import datetime
import gzip
import logging
import os
import platform
import Queue
import re
import subprocess
import time
import zipfile
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
        self.nav_error_code = None
        self.page_loaded = None
        self.recording = False
        self.connected = False
        self.browser_version = None
        self.messages = Queue.Queue()
        self.video_processing = None
        self.is_navigating = False
        self.main_frame = None
        self.main_request = None
        self.page = {}
        self.requests = {}
        self.id_map = {}
        self.response_bodies = {}
        self.bodies_zip_file = None
        self.body_fail_count = 0
        self.body_index = 0
        self.last_activity = monotonic.monotonic()
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')
        self.path_base = None
        self.websocket = None
        self.command_id = 0
        self.command_responses = {}
        self.pending_commands = []
        self.webinspector_proxy = None
        self.ios_utils_path = None
        self.ios_version = None
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
        self.task = task
        self.page = {}
        self.requests = {}
        self.nav_error = None
        self.nav_error_code = None
        self.main_request = None
        self.ios.notification_queue = self.messages
        self.ios.stop_browser()
        if not task['cached']:
            self.clear_profile(task)
        self.path_base = os.path.join(self.task['dir'], self.task['prefix'])
        if self.bodies_zip_file is not None:
            self.bodies_zip_file.close()
            self.bodies_zip_file = None
        if 'bodies' in self.job and self.job['bodies']:
            self.bodies_zip_file = zipfile.ZipFile(self.path_base + '_bodies.zip', 'w',
                                                   zipfile.ZIP_DEFLATED)

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
        self.ios_version = self.ios.get_os_version()
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
                if response.text:
                    tabs = response.json()
                    logging.debug("Dev Tools tabs: %s", json.dumps(tabs))
                    if tabs:
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

    def wait_for_page_load(self):
        """Wait for the onload event from the extension"""
        if self.connected:
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
                        if self.nav_error_code is not None:
                            self.task['page_data']['result'] = self.nav_error_code
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

    def process_message(self, msg):
        """Process a message from the browser
        https://trac.webkit.org/browser/webkit/trunk/Source/JavaScriptCore/inspector/protocol"""
        try:
            if 'method' in msg and self.recording:
                parts = msg['method'].split('.')
                if len(parts) >= 2:
                    category = parts[0]
                    event = parts[1]
                    if category == 'Page':
                        self.process_page_event(event, msg)
                    elif category == 'Network':
                        self.process_network_event(event, msg)
                    elif category == 'Inspector':
                        self.process_inspector_event(event)
                    elif category == 'Timeline':
                        self.process_timeline_event(event, msg)
                    elif category == 'Console':
                        self.process_console_event(event, msg)
        except Exception:
            pass
        if 'id' in msg:
            response_id = int(re.search(r'\d+', str(msg['id'])).group())
            if response_id in self.pending_commands:
                self.pending_commands.remove(response_id)
                self.command_responses[response_id] = msg

    def process_page_event(self, event, msg):
        """Process Page.* dev tools events"""
        if 'start' not in self.page:
            self.page['start'] = msg['params']['timestamp']
        if event == 'loadEventFired':
            self.page_loaded = monotonic.monotonic()
            self.page['load'] = msg['params']['timestamp']
        elif event == 'domContentEventFired':
            self.page['domContentLoaded'] = msg['params']['timestamp']
        elif event == 'frameStartedLoading':
            if self.is_navigating and self.main_frame is None:
                self.is_navigating = False
                self.main_frame = msg['params']['frameId']
            if self.main_frame == msg['params']['frameId']:
                logging.debug("Navigating main frame")
                self.last_activity = monotonic.monotonic()
                self.page_loaded = None
        elif event == 'frameStoppedLoading':
            if self.main_frame is not None and \
                    not self.page_loaded and \
                    self.main_frame == msg['params']['frameId']:
                if self.nav_error is not None:
                    self.task['error'] = self.nav_error
                    logging.debug("Page load failed: %s", self.nav_error)
                    if self.nav_error_code is not None:
                        self.task['page_data']['result'] = self.nav_error_code
                self.page_loaded = monotonic.monotonic()

    def process_network_event(self, event, msg):
        """Process Network.* dev tools events"""
        if 'requestId' in msg['params']:
            request_id = msg['params']['requestId']
            original_request_id = request_id
            if original_request_id in self.id_map:
                request_id = str(request_id) + '.' + str(self.id_map[original_request_id])
            if request_id not in self.requests:
                self.requests[request_id] = {'id': request_id,
                                             'original_id': original_request_id,
                                             'bytesIn': 0}
            request = self.requests[request_id]
            if 'targetId' in msg['params']:
                request['targetId'] = msg['params']['targetId']
            ignore_activity = request['is_video'] if 'is_video' in request else False
            if event == 'requestWillBeSent':
                # For a redirect, close out the existing request and start a new one
                if 'redirectResponse' in msg['params']:
                    request['is_redirect'] = True
                    response = msg['params']['redirectResponse']
                    request['status'] = response['status']
                    request['statusText'] = response['statusText']
                    request['responseHeaders'] = response['headers']
                    if response['source'] != 'network':
                        request['fromNet'] = False
                    if 'timing' in response:
                        request['timing'] = response['timing']
                    if original_request_id in self.id_map:
                        self.id_map[original_request_id] += 1
                    else:
                        self.id_map[original_request_id] = 1
                    request_id = str(request_id) + '.' + str(self.id_map[original_request_id])
                    self.requests[request_id] = {'id': request_id,
                                                 'original_id': original_request_id,
                                                 'bytesIn': 0}
                    request = self.requests[request_id]
                    if 'targetId' in msg['params']:
                        request['targetId'] = msg['params']['targetId']
                if 'start' not in request:
                    request['start'] = msg['params']['timestamp']
                request['initiator'] = msg['params']['initiator']
                request['url'] = msg['params']['request']['url']
                request['method'] = msg['params']['request']['method']
                request['requestHeaders'] = msg['params']['request']['headers']
                if 'type' in msg['params']:
                    request['type'] = msg['params']['type']
                if request['url'].endswith('.mp4'):
                    request['is_video'] = True
                request['fromNet'] = True
                if msg['params']['frameId'] != self.main_frame:
                    request['frame'] = msg['params']['frameId']
                if self.main_frame is not None and \
                        self.main_request is None and \
                        msg['params']['frameId'] == self.main_frame:
                    logging.debug('Main request detected')
                    self.main_request = request_id
                    self.page['start'] = float(msg['params']['timestamp'])
            elif event == 'responseReceived':
                response = msg['params']['response']
                request['status'] = response['status']
                request['statusText'] = response['statusText']
                request['responseHeaders'] = response['headers']
                if response['source'] != 'network':
                    request['fromNet'] = False
                if 'timing' in response:
                    request['timing'] = response['timing']
                if 'mimeType' in response and response['mimeType'].startswith('video/'):
                    request['is_video'] = True
                if 'firstByte' not in request:
                    request['firstByte'] = msg['params']['timestamp']
                request['end'] = msg['params']['timestamp']
            elif event == 'dataReceived':
                if 'encodedDataLength' in msg['params']:
                    request['bytesIn'] += msg['params']['encodedDataLength']
                elif 'dataLength' in msg['params']:
                    request['bytesIn'] += msg['params']['dataLength']
                request['end'] = msg['params']['timestamp']
            elif event == 'loadingFinished':
                request['end'] = msg['params']['timestamp']
                if 'metrics' in msg['params']:
                    metrics = msg['params']['metrics']
                    if 'priority' in metrics:
                        request['priority'] = metrics['priority']
                    if 'protocol' in metrics:
                        request['protocol'] = metrics['protocol']
                    if 'remoteAddress' in metrics:
                        request['ip'] = metrics['remoteAddress']
                    if 'requestHeaderBytesSent' in metrics:
                        request['bytesOut'] = metrics['requestHeaderBytesSent']
                        if 'requestBodyBytesSent' in metrics:
                            request['bytesOut'] += metrics['requestBodyBytesSent']
                    if 'responseBodyBytesReceived' in metrics:
                        request['bytesIn'] = metrics['responseBodyBytesReceived']
                        if 'responseHeaderBytesReceived' in metrics:
                            request['bytesIn'] += metrics['responseHeaderBytesReceived']
                if request['fromNet']:
                    self.get_response_body(request_id, original_request_id)
            elif event == 'loadingFailed':
                request['end'] = msg['params']['timestamp']
                request['statusText'] = msg['params']['errorText']
                if self.main_request is not None and \
                        request_id == self.main_request and \
                        'canceled' in msg['params'] and \
                        not msg['params']['canceled']:
                    self.nav_error = msg['params']['errorText']
                    self.nav_error_code = 404
                    logging.debug('Navigation error: %s', self.nav_error)
            elif event == 'requestServedFromMemoryCache':
                request['fromNet'] = False
            else:
                ignore_activity = True
            if not self.task['stop_at_onload'] and not ignore_activity:
                self.last_activity = monotonic.monotonic()

    def process_inspector_event(self, event):
        """Process Inspector.* dev tools events"""
        if event == 'detached':
            self.task['error'] = 'Inspector detached, possibly crashed.'
        elif event == 'targetCrashed':
            self.task['error'] = 'Browser crashed.'

    def process_timeline_event(self, event, msg):
        """Handle Timeline.* events"""
        return

    def process_console_event(self, event, msg):
        """Handle Console.* events"""
        return

    def get_response_body(self, request_id, original_id):
        """Retrieve and store the given response body (if necessary)"""
        if original_id not in self.response_bodies and self.body_fail_count < 3:
            request = self.requests[request_id]
            if 'status' in request and request['status'] == 200 and 'responseHeaders' in request:
                logging.debug('Getting body for %s (%d) - %s', request_id,
                              request['bytesIn'], request['url'])
                path = os.path.join(self.task['dir'], 'bodies')
                if not os.path.isdir(path):
                    os.makedirs(path)
                body_file_path = os.path.join(path, original_id)
                if not os.path.exists(body_file_path):
                    # Only grab bodies needed for optimization checks
                    # or if we are saving full bodies
                    need_body = True
                    content_type = self.get_header_value(request['responseHeaders'],
                                                         'Content-Type')
                    is_text = False
                    if content_type is not None:
                        content_type = content_type.lower()
                        if content_type.startswith('text/') or \
                                content_type.find('javascript') >= 0 or \
                                content_type.find('json') >= 0:
                            is_text = True
                        # Ignore video files over 10MB
                        if content_type[:6] == 'video/' and request['bytesIn'] > 10000000:
                            need_body = False
                    optimization_checks_disabled = bool('noopt' in self.job and self.job['noopt'])
                    if optimization_checks_disabled and self.bodies_zip_file is None:
                        need_body = False
                    if need_body:
                        target_id = None
                        response = self.send_command("Network.getResponseBody",
                                                     {'requestId': original_id}, wait=True)
                        if response is None:
                            self.body_fail_count += 1
                            logging.warning('No response to body request for request %s',
                                            request_id)
                        elif 'result' not in response or \
                                'body' not in response['result']:
                            self.body_fail_count = 0
                            logging.warning('Missing response body for request %s',
                                            request_id)
                        elif len(response['result']['body']):
                            self.body_fail_count = 0
                            # Write the raw body to a file (all bodies)
                            if 'base64Encoded' in response['result'] and \
                                    response['result']['base64Encoded']:
                                body = base64.b64decode(response['result']['body'])
                            else:
                                body = response['result']['body'].encode('utf-8')
                                is_text = True
                            # Add text bodies to the zip archive
                            if self.bodies_zip_file is not None and is_text:
                                self.body_index += 1
                                name = '{0:03d}-{1}-body.txt'.format(self.body_index, request_id)
                                self.bodies_zip_file.writestr(name, body)
                                logging.debug('%s: Stored body in zip', request_id)
                            logging.debug('%s: Body length: %d', request_id, len(body))
                            self.response_bodies[request_id] = body
                            with open(body_file_path, 'wb') as body_file:
                                body_file.write(body)
                        else:
                            self.body_fail_count = 0
                            self.response_bodies[request_id] = response['result']['body']

    def get_header_value(self, headers, name):
        """Get the value for the requested header"""
        value = None
        if headers:
            if name in headers:
                value = headers[name]
            else:
                find = name.lower()
                for header_name in headers:
                    check = header_name.lower()
                    if check == find or (check[0] == ':' and check[1:] == find):
                        value = headers[header_name]
                        break
        return value

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
            if self.ios_version:
                task['page_data']['osVersion'] = self.ios_version
                task['page_data']['os_version'] = self.ios_version
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
        if self.bodies_zip_file is not None:
            self.bodies_zip_file.close()
            self.bodies_zip_file = None

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
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
