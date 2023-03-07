# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Support for Safari on iOS using iWptBrowser"""
import base64
from datetime import datetime
import gzip
import io
import logging
import multiprocessing
import os
import platform
import re
import subprocess
import sys
import time
import zipfile
if (sys.version_info >= (3, 0)):
    from time import monotonic
    from urllib.parse import urlsplit # pylint: disable=import-error
    unicode = str
    GZIP_TEXT = 'wt'
else:
    from monotonic import monotonic
    from urlparse import urlsplit # pylint: disable=import-error
    GZIP_TEXT = 'w'
try:
    import ujson as json
except BaseException:
    import json
from ws4py.client.threadedclient import WebSocketClient
from .optimization_checks import OptimizationChecks
from .base_browser import BaseBrowser

class iWptBrowser(BaseBrowser):
    """iOS"""

    def __init__(self, ios_device, options, job):
        BaseBrowser.__init__(self)
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
        self.messages = multiprocessing.JoinableQueue()
        self.video_processing = None
        self.optimization = None
        self.is_navigating = False
        self.main_frame = None
        self.main_request = None
        self.page = {}
        self.requests = {}
        self.request_count = 0
        self.connections = {}
        self.last_connection_id = 0
        self.console_log = []
        self.timeline = None
        self.trace_parser = None
        self.wpt_result = None
        self.id_map = {}
        self.response_bodies = {}
        self.bodies_zip_file = None
        self.body_fail_count = 0
        self.body_index = 0
        self.last_activity = monotonic()
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')
        self.path_base = None
        self.websocket = None
        self.command_id = 0
        self.command_responses = {}
        self.pending_commands = []
        self.headers = {}
        self.webinspector_proxy = None
        self.ios_version = None
        self.workers = []
        self.default_target = None
        self.total_sleep = 0
        self.wait_interval = 5.0
        self.wait_for_script = None

    def prepare(self, job, task):
        """Prepare the OS for the browser"""
        self.task = task
        self.page = {}
        self.requests = {}
        self.request_count = 0
        self.console_log = []
        if self.timeline is not None:
            self.timeline.close()
            self.timeline = None
        self.nav_error = None
        self.nav_error_code = None
        self.main_request = None
        self.ios.notification_queue = self.messages
        self.ios.stop_browser()
        if 'browser' in job and job['browser'].lower().find('landscape') >= 0:
            self.ios.landscape()
        else:
            self.ios.portrait()
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
        if self.must_exit:
            return
        self.connected = False
        self.flush_messages()
        self.ios_version = self.ios.get_os_version()
        if self.ios.start_browser():
            # Start the webinspector proxy
            args = ['ios_webkit_debug_proxy', '-F', '-u', self.ios.serial]
            logging.debug(' '.join(args))
            try:
                self.webinspector_proxy = subprocess.Popen(args)

                if self.webinspector_proxy:
                    # Connect to the dev tools interface
                    self.connected = self.connect()

                if self.connected:
                    self.send_command('Target.setPauseOnStart', {'pauseOnStart': True}, wait=True)
                    # Override the UA String if necessary
                    ua_string = self.execute_js('navigator.userAgent;')
                    if 'uastring' in self.job:
                        ua_string = self.job['uastring']
                    if ua_string is not None and 'AppendUA' in task:
                        ua_string += ' ' + task['AppendUA']
                    if ua_string is not None:
                        self.job['user_agent_string'] = ua_string
            except Exception:
                logging.exception("Error starting webkit proxy")

        self.flush_messages()

    def connect(self, timeout=30):
        """Connect to the dev tools interface"""
        import requests
        proxies = {"http": None, "https": None}
        ret = False
        self.default_target = None
        end_time = monotonic() + timeout
        while not ret and monotonic() < end_time and not self.must_exit:
            try:
                response = requests.get("http://localhost:9222/json", timeout=timeout, proxies=proxies)
                if response.text:
                    tabs = response.json()
                    logging.debug("Dev Tools tabs: %s", json.dumps(tabs))
                    if tabs:
                        websocket_url = None
                        for index in range(len(tabs)):
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
                                logging.exception("Connect to dev tools websocket Error: %s", err.__str__())
                            if not ret:
                                # try connecting to 127.0.0.1 instead of localhost
                                try:
                                    websocket_url = websocket_url.replace('localhost', '127.0.0.1')
                                    self.websocket = DevToolsClient(websocket_url)
                                    self.websocket.messages = self.messages
                                    self.websocket.connect()
                                    ret = True
                                except Exception as err:
                                    logging.exception("Connect to dev tools websocket Error: %s", err.__str__())
                        else:
                            time.sleep(0.5)
                    else:
                        time.sleep(0.5)
            except Exception as err:
                logging.exception("Connect to dev tools Error: %s", err.__str__())
                time.sleep(0.5)
        return ret

    def stop(self, job, task):
        """Kill the browser"""
        if self.websocket:
            try:
                self.websocket.close()
            except Exception:
                logging.exception('Error closing websocket')
            self.websocket = None
        if self.webinspector_proxy:
            self.webinspector_proxy.terminate()
            self.webinspector_proxy.communicate()
            self.webinspector_proxy = None
        self.ios.stop_browser()

    def run_lighthouse_test(self, task):
        """Stub for lighthouse test"""
        pass

    def run_task(self, task):
        """Run an individual test"""
        if self.connected:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic() + task['test_time_limit']
            task['current_step'] = 1
            recording = False
            while task['script'] and task['error'] is None and monotonic() < end_time and not self.must_exit:
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
            self.task = None

    def wait_for_page_load(self):
        """Wait for the onload event from the extension"""
        if self.connected:
            start_time = monotonic()
            end_time = start_time + self.task['time_limit']
            done = False
            interval = 1
            last_wait_interval = start_time
            max_requests = int(self.job['max_requests']) if 'max_requests' in self.job else 0
            while not done and not self.must_exit:
                if self.page_loaded is not None:
                    interval = 0.1
                try:
                    message = self.messages.get(timeout=interval)
                    try:
                        self.process_message(message)
                    except Exception:
                        logging.exception('Error processing message')
                except Exception:
                    pass
                now = monotonic()
                elapsed_test = now - start_time
                if 'minimumTestSeconds' in self.task and \
                        elapsed_test < self.task['minimumTestSeconds'] and \
                        now < end_time:
                    continue
                if self.nav_error is not None:
                    done = True
                    if self.page_loaded is None or 'minimumTestSeconds' in self.task:
                        self.task['error'] = self.nav_error
                        if self.nav_error_code is not None:
                            self.task['page_data']['result'] = self.nav_error_code
                        else:
                            self.task['page_data']['result'] = 12999
                elif now >= end_time:
                    done = True
                    # only consider it an error if we didn't get a page load event
                    if self.page_loaded is None:
                        self.task['error'] = "Page Load Timeout"
                        self.task['soft_error'] = True
                elif max_requests > 0 and self.request_count > max_requests:
                    done = True
                    # only consider it an error if we didn't get a page load event
                    if self.page_loaded is None:
                        self.task['error'] = "Exceeded Maximum Requests"
                        self.task['soft_error'] = True
                        self.task['page_data']['result'] = 99997
                elif self.wait_for_script is not None:
                    elapsed_interval = now - last_wait_interval
                    if elapsed_interval >= self.wait_interval:
                        last_wait_interval = now
                        ret = self.execute_js(self.wait_for_script)
                        if ret == True:
                            done = True
                else:
                    elapsed_activity = now - self.last_activity
                    elapsed_page_load = now - self.page_loaded if self.page_loaded else 0
                    if elapsed_page_load >= 1 and elapsed_activity >= self.task['activity_time']:
                        done = True
                    elif self.task['error'] is not None:
                        done = True

    def execute_js(self, script):
        """Run javascipt (stub for overriding"""
        if self.must_exit:
            return
        ret = None
        if self.connected:
            result = self.send_command('Runtime.evaluate', {'expression': script, 'returnByValue': True}, timeout=30, wait=True)
            if result is not None and 'result' in result and 'result' in result['result'] and 'value' in result['result']['result']:
                ret = result['result']['result']['value']
        return ret

    def run_js_file(self, file_name):
        """Execute one of our js scripts"""
        if self.must_exit:
            return
        ret = None
        if self.connected:
            script = None
            script_file_path = os.path.join(self.script_dir, file_name)
            if os.path.isfile(script_file_path):
                with open(script_file_path, 'r') as script_file:
                    script = script_file.read()
            if script is not None:
                ret = self.execute_js(script)
        return ret

    def set_header(self, header):
        """Add/modify a header on the outbound requests"""
        if header is not None and len(header):
            separator = header.find(':')
            if separator > 0:
                name = header[:separator].strip()
                value = header[separator + 1:].strip()
                self.headers[name] = value
                self.send_command('Network.setExtraHTTPHeaders',
                                  {'headers': self.headers}, wait=True)
                if len(self.workers):
                    for target in self.workers:
                        self.send_command('Network.setExtraHTTPHeaders',
                                          {'headers': self.headers}, target_id=target['targetId'])

    def reset_headers(self):
        """Add/modify a header on the outbound requests"""
        self.headers = {}
        self.send_command('Network.setExtraHTTPHeaders',
                          {'headers': self.headers}, wait=True)
        if len(self.workers):
            for target in self.workers:
                self.send_command('Network.setExtraHTTPHeaders',
                                  {'headers': self.headers}, target_id=target['targetId'])

    def get_sorted_requests_json(self, include_bodies):
        return 'null'

    def collect_browser_metrics(self, task):
        """Collect all of the in-page browser metrics that we need"""
        if self.must_exit:
            return
        if 'customMetrics' in self.job:
            custom_metrics = {}
            requests = None
            bodies = None
            for name in sorted(self.job['customMetrics']):
                if name == 'jsLibsVulns':
                    continue
                logging.debug("Collecting custom metric %s", name)
                custom_script = unicode(self.job['customMetrics'][name])
                if custom_script.find('$WPT_TEST_URL') >= 0:
                    wpt_url = 'window.location.href'
                    if 'page_data' in self.task and 'URL' in self.task['page_data']:
                        wpt_url = '{}'.format(json.dumps(self.task['page_data']['URL']))
                    elif 'url' in self.job:
                        wpt_url = '{}'.format(json.dumps(self.job['URL']))
                    try:
                        custom_script = custom_script.replace('$WPT_TEST_URL', wpt_url)
                    except Exception:
                        logging.exception('Error substituting URL data into custom script')
                if custom_script.find('$WPT_REQUESTS') >= 0:
                    if requests is None:
                        requests = self.get_sorted_requests_json(False)
                    try:
                        custom_script = custom_script.replace('$WPT_REQUESTS', requests)
                    except Exception:
                        logging.exception('Error substituting request data into custom script')
                if custom_script.find('$WPT_BODIES') >= 0:
                    if bodies is None:
                        bodies = self.get_sorted_requests_json(True)
                    try:
                        custom_script = custom_script.replace('$WPT_BODIES', bodies)
                    except Exception:
                        logging.exception('Error substituting request data with bodies into custom script')
                script = '(function() {' + custom_script + '})()'
                try:
                    custom_metrics[name] = self.execute_js(script)
                    if custom_metrics[name] is not None:
                        logging.debug(custom_metrics[name])
                except Exception:
                    logging.exception('Error collecting custom metric')
            if  self.path_base is not None:
                path = self.path_base + '_metrics.json.gz'
                with gzip.open(path, GZIP_TEXT, 7) as outfile:
                    outfile.write(json.dumps(custom_metrics))
        logging.debug("Collecting user timing metrics")
        user_timing = self.run_js_file('user_timing.js')
        logging.debug(user_timing)
        if user_timing is not None and self.path_base is not None:
            path = self.path_base + '_timed_events.json.gz'
            with gzip.open(path, GZIP_TEXT, 7) as outfile:
                outfile.write(json.dumps(user_timing))
        logging.debug("Collecting page-level metrics")
        page_data = self.run_js_file('page_data.js')
        logging.debug(page_data)
        if page_data is not None:
            task['page_data'].update(page_data)

    def process_message(self, msg):
        """Process a message from the browser
        https://trac.webkit.org/browser/webkit/trunk/Source/JavaScriptCore/inspector/protocol"""
        try:
            if 'method' in msg:
                parts = msg['method'].split('.')
                if len(parts) >= 2:
                    category = parts[0]
                    event = parts[1]
                    if category == 'Page' and self.recording:
                        self.process_page_event(event, msg)
                    elif category == 'Network' and self.recording:
                        self.process_network_event(event, msg)
                    elif category == 'Inspector':
                        self.process_inspector_event(event)
                    elif category == 'Timeline' and self.recording:
                        self.process_timeline_event(event, msg)
                    elif category == 'Console' and self.recording:
                        self.process_console_event(event, msg)
                    elif category == 'Target':
                        self.process_target_event(event, msg)
        except Exception:
            logging.exception('Error processing browser message')
        if self.timeline and 'method' in msg and not msg['method'].startswith('Target.') and self.recording:
            json.dump(msg, self.timeline)
            self.timeline.write(",\n")
        if 'id' in msg:
            response_id = int(re.search(r'\d+', str(msg['id'])).group())
            if response_id in self.pending_commands:
                self.pending_commands.remove(response_id)
                self.command_responses[response_id] = msg

    def process_page_event(self, event, msg):
        """Process Page.* dev tools events"""
        if 'start' not in self.page and 'params' in msg and 'timestamp' in msg['params']:
            self.page['start'] = msg['params']['timestamp']
        if event == 'loadEventFired':
            self.page_loaded = monotonic()
            self.page['loaded'] = msg['params']['timestamp']
        elif event == 'domContentEventFired':
            self.page['DOMContentLoaded'] = msg['params']['timestamp']
        elif event == 'frameStartedLoading':
            if self.is_navigating and self.main_frame is None:
                self.is_navigating = False
                self.main_frame = msg['params']['frameId']
            if self.main_frame == msg['params']['frameId']:
                logging.debug("Navigating main frame")
                self.last_activity = monotonic()
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
                self.page_loaded = monotonic()

    def process_network_event(self, event, msg):
        """Process Network.* dev tools events"""
        if 'requestId' in msg['params']:
            timestamp = None
            if 'params' in msg and 'timestamp' in msg['params']:
                timestamp = msg['params']['timestamp']
            request_id = msg['params']['requestId']
            original_request_id = request_id
            if original_request_id in self.id_map:
                request_id = str(original_request_id) + '.' + str(self.id_map[original_request_id])
            if request_id not in self.requests:
                self.requests[request_id] = {'id': request_id,
                                             'original_id': original_request_id,
                                             'bytesIn': 0,
                                             'objectSize': 0,
                                             'objectSizeUncompressed': 0,
                                             'transfer_size': 0,
                                             'fromNet': False,
                                             'is_redirect': False}
                if timestamp:
                    self.requests[request_id]['created'] = timestamp
            request = self.requests[request_id]
            if 'targetId' in msg['params']:
                request['targetId'] = msg['params']['targetId']
            ignore_activity = request['is_video'] if 'is_video' in request else False
            if event == 'requestWillBeSent':
                if 'start' not in self.page and timestamp:
                    self.page['start'] = timestamp
                # For a redirect, close out the existing request and start a new one
                if 'redirectResponse' in msg['params']:
                    if timestamp and 'start' in request and timestamp > request['start']:
                        if 'firstByte' not in request or timestamp < request['firstByte']:
                            request['firstByte'] = timestamp
                        if 'end' not in request or timestamp > request['end']:
                            request['end'] = timestamp
                    request['is_redirect'] = True
                    response = msg['params']['redirectResponse']
                    request['status'] = response['status']
                    request['statusText'] = response['statusText']
                    request['response_headers'] = response['headers']
                    if 'fromDiskCache' in response and response['fromDiskCache']:
                        request['fromNet'] = False
                    if 'source' in response and response['source'] not in ['network', 'unknown']:
                        request['fromNet'] = False
                    if 'timing' in response:
                        request['timing'] = response['timing']
                    if original_request_id in self.id_map:
                        self.id_map[original_request_id] += 1
                    else:
                        self.id_map[original_request_id] = 1
                    request_id = str(original_request_id) + '.' + \
                                 str(self.id_map[original_request_id])
                    self.requests[request_id] = {'id': request_id,
                                                 'original_id': original_request_id,
                                                 'bytesIn': 0,
                                                 'objectSize': 0,
                                                 'objectSizeUncompressed': 0,
                                                 'transfer_size': 0,
                                                 'fromNet': False,
                                                 'is_redirect': True}
                    if timestamp:
                        self.requests[request_id]['created'] = timestamp
                    request = self.requests[request_id]
                if timestamp:
                    request['start'] = timestamp
                request['initiator'] = msg['params']['initiator']
                request['url'] = msg['params']['request']['url']
                request['method'] = msg['params']['request']['method']
                request['request_headers'] = msg['params']['request']['headers']
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
                    if timestamp:
                        self.page['start'] = float(msg['params']['timestamp'])
            elif event == 'responseReceived':
                response = msg['params']['response']
                request['status'] = response['status']
                request['statusText'] = response['statusText']
                request['response_headers'] = response['headers']
                if 'fromDiskCache' in response and response['fromDiskCache']:
                    request['fromNet'] = False
                if 'source' in response and response['source'] not in ['network', 'unknown']:
                    request['fromNet'] = False
                if 'timing' in response:
                    request['timing'] = response['timing']
                if 'mimeType' in response and response['mimeType'].startswith('video/'):
                    request['is_video'] = True
                if timestamp and 'start' in request and timestamp > request['start']:
                    if 'firstByte' not in request or timestamp < request['firstByte']:
                        request['firstByte'] = timestamp
                    if 'end' not in request or timestamp > request['end']:
                        request['end'] = timestamp
            elif event == 'dataReceived':
                bytesIn = 0
                if 'encodedDataLength' in msg['params'] and \
                        msg['params']['encodedDataLength'] >= 0:
                    bytesIn = msg['params']['encodedDataLength']
                    request['objectSize'] += bytesIn
                    request['bytesIn'] += bytesIn
                    request['transfer_size'] += bytesIn
                elif 'dataLength' in msg['params'] and msg['params']['dataLength'] >= 0:
                    bytesIn = msg['params']['dataLength']
                    request['objectSize'] += bytesIn
                    request['bytesIn'] +=bytesIn
                    request['transfer_size'] += bytesIn
                if 'dataLength' in msg['params'] and msg['params']['dataLength'] >= 0:
                    request['objectSizeUncompressed'] += msg['params']['dataLength']
                if timestamp and 'start' in request and timestamp > request['start']:
                    if 'chunks' not in request:
                        request['chunks'] = []
                    request['chunks'].append({'ts': timestamp, 'bytes': bytesIn})
                    if 'firstByte' not in request or timestamp < request['firstByte']:
                        request['firstByte'] = timestamp
                    if 'end' not in request or timestamp > request['end']:
                        request['end'] = timestamp
            elif event == 'loadingFinished':
                if timestamp and 'start' in request and timestamp > request['start']:
                    if 'firstByte' not in request or timestamp < request['firstByte']:
                        request['firstByte'] = timestamp
                    if 'end' not in request or timestamp > request['end']:
                        request['end'] = timestamp
                if 'metrics' in msg['params']:
                    metrics = msg['params']['metrics']
                    if 'priority' in metrics:
                        request['priority'] = metrics['priority']
                    if 'protocol' in metrics:
                        request['protocol'] = metrics['protocol']
                    if 'remoteAddress' in metrics:
                        separator = metrics['remoteAddress'].rfind(':')
                        if separator >= 0:
                            request['ip'] = metrics['remoteAddress'][:separator]
                        else:
                            request['ip'] = metrics['remoteAddress']
                    if 'connectionIdentifier' in metrics:
                        identifier = metrics['connectionIdentifier']
                        if identifier in self.connections:
                            request['connection'] = self.connections[identifier]
                        else:
                            self.last_connection_id += 1
                            self.connections[identifier] = self.last_connection_id
                            request['connection'] = self.last_connection_id
                    if 'requestHeaderBytesSent' in metrics:
                        request['bytesOut'] = metrics['requestHeaderBytesSent']
                        if 'requestBodyBytesSent' in metrics:
                            request['bytesOut'] += metrics['requestBodyBytesSent']
                    if 'responseBodyBytesReceived' in metrics:
                        request['bytesIn'] = metrics['responseBodyBytesReceived']
                        request['objectSize'] = metrics['responseBodyBytesReceived']
                        request['transfer_size'] = metrics['responseBodyBytesReceived']
                        if 'responseHeaderBytesReceived' in metrics and \
                                metrics['responseHeaderBytesReceived'] >= 0:
                            request['bytesIn'] += metrics['responseHeaderBytesReceived']
                        if 'responseBodyDecodedSize' in metrics and \
                                metrics['responseBodyDecodedSize'] >= 0:
                            request['objectSizeUncompressed'] = \
                                    metrics['responseBodyDecodedSize']
                if request['fromNet']:
                    self.request_count += 1
                    self.get_response_body(request_id, original_request_id)
            elif event == 'loadingFailed':
                if timestamp and 'start' in request and timestamp > request['start']:
                    if 'firstByte' not in request or timestamp < request['firstByte']:
                        request['firstByte'] = timestamp
                    if 'end' not in request or timestamp > request['end']:
                        request['end'] = timestamp
                request['statusText'] = msg['params']['errorText']
                if self.main_request is not None and request_id == self.main_request:
                    if 'canceled' not in msg['params'] or not msg['params']['canceled']:
                        self.task['error'] = msg['params']['errorText']
                        self.nav_error = msg['params']['errorText']
                        self.nav_error_code = 12999
                        logging.debug('Navigation error: %s', self.nav_error)
            elif event == 'requestServedFromMemoryCache':
                request['fromNet'] = False
            else:
                ignore_activity = True
            if not self.task['stop_at_onload'] and not ignore_activity:
                self.last_activity = monotonic()

    def process_inspector_event(self, event):
        """Process Inspector.* dev tools events"""
        if event == 'detached':
            self.task['error'] = 'Inspector detached, possibly crashed.'
        elif event == 'targetCrashed':
            self.task['error'] = 'Browser crashed.'

    def process_timeline_event(self, event, msg):
        """Handle Timeline.* events"""
        if self.trace_parser is not None and 'params' in msg and 'record' in msg['params']:
            if 'start' not in self.page:
                return
            if self.trace_parser.start_time is None:
                self.trace_parser.start_time = self.page['start'] * 1000000.0
                self.trace_parser.end_time = self.page['start'] * 1000000.0
            if 'timestamp' in msg['params']:
                timestamp = msg['params']['timestamp'] * 1000000.0
                if timestamp > self.trace_parser.end_time:
                    self.trace_parser.end_time = timestamp
            processed = self.trace_parser.ProcessOldTimelineEvent(msg['params']['record'], None)
            if processed is not None:
                self.trace_parser.timeline_events.append(processed)

    def process_console_event(self, event, msg):
        """Handle Console.* events"""
        if event == 'messageAdded' and 'message' in msg['params']:
            self.console_log.append(msg['params']['message'])

    def process_target_event(self, event, msg):
        """Process Target.* dev tools events"""
        if event == 'dispatchMessageFromTarget':
            if 'message' in msg['params']:
                logging.debug(msg['params']['message'][:200])
                target_message = json.loads(msg['params']['message'])
                self.process_message(target_message)
        if event == 'targetCreated':
            if 'targetInfo' in msg['params'] and 'targetId' in msg['params']['targetInfo']:
                target = msg['params']['targetInfo']
                target_id = target['targetId']
                if 'type' in target and target['type'] == 'page':
                    self.default_target = target_id
                    if self.recording:
                        self.enable_safari_events()
                else:
                    self.workers.append(target)
                    if self.recording:
                        self.enable_target(target_id)
                self.send_command('Target.resume', {'targetId': target_id})

    def get_response_body(self, request_id, original_id):
        """Retrieve and store the given response body (if necessary)"""
        if original_id not in self.response_bodies and self.body_fail_count < 3 and not self.must_exit:
            request = self.requests[request_id]
            if 'status' in request and request['status'] == 200 and 'response_headers' in request and 'url' in request and request['url'].startswith('http'):
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
                    content_type = self.get_header_value(request['response_headers'],
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
                if os.path.exists(body_file_path):
                    request['body'] = body_file_path

    def get_header_value(self, headers, name):
        """Get the value for the requested header"""
        value = None
        try:
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
        except Exception:
            logging.exception('Error getting header value for %s', name)
        return value

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
        self.path_base = os.path.join(self.task['dir'], self.task['prefix'])
        if 'steps' not in task:
            task['steps'] = []
        task['steps'].append({
            'prefix': str(task['prefix']),
            'video_subdirectory': str(task['video_subdirectory']),
            'step_name': str(task['step_name']),
            'start_time': time.time(),
            'num': int(task['current_step'])
        })
    
    def enable_target(self, target_id):
        """Enable all of the targe-specific events"""
        self.send_command('Network.enable', {}, target_id=target_id)
        if self.headers:
            self.send_command('Network.setExtraHTTPHeaders', {'headers': self.headers}, target_id=target_id)

    def enable_safari_events(self):
        self.send_command('Inspector.enable', {})
        self.send_command('Network.enable', {})
        self.send_command('Runtime.enable', {})
        if self.headers:
            self.send_command('Network.setExtraHTTPHeaders', {'headers': self.headers})
        if len(self.workers):
            for target in self.workers:
                self.enable_target(target['targetId'])
        if 'user_agent_string' in self.job:
            self.ios.set_user_agent(self.job['user_agent_string'])
        if self.task['log_data']:
            self.send_command('Console.enable', {})
            self.send_command('Timeline.start', {}, wait=True)
        self.send_command('Page.enable', {}, wait=True)

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        self.page = {}
        self.requests = {}
        self.console_log = []
        self.response_bodies = {}
        if self.timeline is not None:
            self.timeline.close()
            self.timeline = None
        self.wpt_result = None
        task['page_data'] = {'date': time.time()}
        task['page_result'] = None
        task['run_start_time'] = monotonic()
        self.flush_messages()
        self.enable_safari_events()
        if self.task['log_data']:
            if not self.job['dtShaper']:
                if not self.job['shaper'].configure(self.job, task):
                    self.task['error'] = "Error configuring traffic-shaping"
            if self.bodies_zip_file is not None:
                self.bodies_zip_file.close()
                self.bodies_zip_file = None
            if 'bodies' in self.job and self.job['bodies']:
                self.bodies_zip_file = zipfile.ZipFile(self.path_base + '_bodies.zip', 'w',
                                                       zipfile.ZIP_DEFLATED)
            if 'timeline' in self.job and self.job['timeline']:
                if self.path_base is not None:
                    timeline_path = self.path_base + '_devtools.json.gz'
                    self.timeline = gzip.open(timeline_path, GZIP_TEXT, 7)
                    if self.timeline:
                        self.timeline.write('[\n')
                from internal.support.trace_parser import Trace
                self.trace_parser = Trace()
                self.trace_parser.cpu['main_thread'] = '0'
                self.trace_parser.threads['0'] = {}
            self.ios.show_orange()
            if self.path_base is not None and not self.job['disable_video']:
                task['video_file'] = self.path_base + '_video.mp4'
                self.ios.start_video()
            if self.ios_version:
                task['page_data']['osVersion'] = self.ios_version
                task['page_data']['os_version'] = self.ios_version
                task['page_data']['browserVersion'] = self.ios_version
                task['page_data']['browser_version'] = self.ios_version
        self.recording = True
        now = monotonic()
        if not self.task['stop_at_onload']:
            self.last_activity = now
        if self.page_loaded is not None:
            self.page_loaded = now
        logging.debug('Starting measurement')
        task['start_time'] = datetime.utcnow()

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        pass

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        self.recording = False
        self.send_command('Page.disable', {})
        self.send_command('Inspector.disable', {})
        self.send_command('Network.disable', {})
        self.send_command('Runtime.disable', {})
        if len(self.workers):
            for target in self.workers:
                self.send_command('Network.disable', {}, target_id=target['targetId'])
        self.send_command('Inspector.disable', {})
        if self.task['log_data']:
            self.send_command('Console.disable', {})
            if 'timeline' in self.job and self.job['timeline']:
                self.send_command('Timeline.stop', {})
            if self.job['pngScreenShot'] and self.path_base is not None:
                screen_shot = self.path_base + '_screen.png'
                self.grab_screenshot(screen_shot, png=True)
            elif self.path_base is not None:
                screen_shot = self.path_base + '_screen.jpg'
                self.grab_screenshot(screen_shot, png=False, resize=600)
            # Grab the video and kick off processing async
            if 'video_file' in task:
                self.ios.stop_video()
            # Collect end of test data from the browser
            self.collect_browser_metrics(task)
        if self.bodies_zip_file is not None:
            self.bodies_zip_file.close()
            self.bodies_zip_file = None
        self.job['shaper'].reset()

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        if task['log_data']:
            # Attach response bodies to all of the appropriate requests
            requests = {}
            for request_id in self.requests:
                request = self.requests[request_id]
                if request['fromNet'] and 'url' in request and request['url'].startswith('http'):
                    if not request['is_redirect'] and \
                            request['original_id'] in self.response_bodies:
                        request['response_body'] = self.response_bodies[request['original_id']]
                    requests[request_id] = request
            # Start the optimization checks in a background thread
            self.optimization = OptimizationChecks(self.job, task, requests)
            self.optimization.start()
            support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
            # Start processing the timeline
            if self.timeline:
                self.timeline.write("{}]")
                self.timeline.close()
                self.timeline = None
            # Grab the video and kick off processing async
            if 'video_file' in task and self.ios.get_video(task['video_file']):
                video_path = os.path.join(task['dir'], task['video_subdirectory'])
                if task['current_step'] == 1:
                    filename = '{0:d}.{1:d}.histograms.json.gz'.format(task['run'], task['cached'])
                else:
                    filename = '{0:d}.{1:d}.{2:d}.histograms.json.gz'.format(task['run'],
                                                                             task['cached'],
                                                                             task['current_step'])
                histograms = os.path.join(task['dir'], filename)
                progress_file = os.path.join(task['dir'], task['prefix']) + \
                                '_visual_progress.json.gz'
                visualmetrics = os.path.join(support_path, "visualmetrics.py")
                args = [sys.executable, visualmetrics, '-i', task['video_file'],
                        '-d', video_path, '--force', '--quality',
                        '{0:d}'.format(self.job['imageQuality']),
                        '--viewport', '--orange', '--maxframes', '50', '--histogram', histograms,
                        '--progress', progress_file]
                if 'debug' in self.job and self.job['debug']:
                    args.append('-vvvv')
                if 'renderVideo' in self.job and self.job['renderVideo']:
                    video_out = self.path_base + '_rendered_video.mp4'
                    args.extend(['--render', video_out])
                if 'fullSizeVideo' in self.job and self.job['fullSizeVideo']:
                    args.append('--full')
                if 'thumbsize' in self.job:
                    try:
                        thumbsize = int(self.job['thumbsize'])
                        if thumbsize > 0 and thumbsize <= 2000:
                            args.extend(['--thumbsize', str(thumbsize)])
                    except Exception:
                        pass
                try:
                    logging.debug('Video file size: %d', os.path.getsize(video_path))
                except Exception:
                    pass
                logging.debug(' '.join(args))
                self.video_processing = subprocess.Popen(args, close_fds=True)
            # Save the console logs
            if self.console_log and self.path_base is not None:
                log_file = self.path_base + '_console_log.json.gz'
                with gzip.open(log_file, GZIP_TEXT, 7) as f_out:
                    json.dump(self.console_log, f_out)
            # Process the timeline data
            if self.trace_parser is not None and self.path_base is not None:
                start = monotonic()
                logging.debug("Processing the trace timeline events")
                self.trace_parser.ProcessTimelineEvents()
                self.trace_parser.WriteCPUSlices(self.path_base + '_timeline_cpu.json.gz')
                self.trace_parser.WriteScriptTimings(self.path_base + '_script_timing.json.gz')
                self.trace_parser.WriteInteractive(self.path_base + '_interactive.json.gz')
                self.trace_parser.WriteLongTasks(self.path_base + '_long_tasks.json.gz')
                elapsed = monotonic() - start
                logging.debug("Done processing the trace events: %0.3fs", elapsed)
            self.trace_parser = None
            # Calculate the request and page stats
            self.wpt_result = {}
            self.wpt_result['requests'] = self.process_requests(requests)
            self.wpt_result['pageData'] = self.calculate_page_stats(self.wpt_result['requests'])
            if 'metadata' in self.job:
                self.wpt_result['pageData']['metadata'] = self.job['metadata']

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
        opt = None
        if self.optimization is not None:
            opt = self.optimization.join()
        if self.wpt_result is not None:
            self.process_optimization_results(self.wpt_result['pageData'],
                                              self.wpt_result['requests'], opt)
            if self.path_base is not None:
                devtools_file = self.path_base + '_devtools_requests.json.gz'
                with gzip.open(devtools_file, GZIP_TEXT, 7) as f_out:
                    json.dump(self.wpt_result, f_out)

    def step_complete(self, task):
        """Final step processing"""
        logging.debug("Writing end-of-step data")
        # Write out the accumulated page_data
        if task['log_data'] and task['page_data']:
            if 'browser' in self.job:
                task['page_data']['browser_name'] = self.job['browser']
            if 'step_name' in task:
                task['page_data']['eventName'] = task['step_name']
            if 'run_start_time' in task:
                task['page_data']['test_run_time_ms'] = \
                        int(round((monotonic() - task['run_start_time']) * 1000.0))
            if self.path_base is not None:
                path = self.path_base + '_page_data.json.gz'
                json_page_data = json.dumps(task['page_data'])
                logging.debug('Page Data: %s', json_page_data)
                with gzip.open(path, GZIP_TEXT, 7) as outfile:
                    outfile.write(json_page_data)

    def send_command(self, method, params, wait=False, timeout=10, target_id=None):
        """Send a raw dev tools message and optionally wait for the response"""
        ret = None
        if target_id is None and self.default_target is not None and \
                not method.startswith('Target.') and \
                not method.startswith('Tracing.'):
            target_id = self.default_target
        if target_id is not None:
            self.command_id += 1
            command_id = int(self.command_id)
            msg = {'id': command_id, 'method': method, 'params': params}
            if wait:
                self.pending_commands.append(command_id)
            end_time = monotonic() + timeout
            self.send_command('Target.sendMessageToTarget',
                              {'targetId': target_id, 'message': json.dumps(msg)},
                              wait=True, timeout=timeout)
            if wait:
                if command_id in self.command_responses:
                    ret = self.command_responses[command_id]
                    del self.command_responses[command_id]
                else:
                    while ret is None and monotonic() < end_time:
                        try:
                            msg = self.messages.get(timeout=1)
                            try:
                                if msg:
                                    self.process_message(msg)
                            except Exception:
                                logging.exception('Error processing command response')
                        except Exception:
                            pass
                        if command_id in self.command_responses:
                            ret = self.command_responses[command_id]
                            del self.command_responses[command_id]
        elif self.websocket:
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
                    end_time = monotonic() + timeout
                    while ret is None and monotonic() < end_time:
                        try:
                            msg = self.messages.get(timeout=1)
                            try:
                                if msg:
                                    self.process_message(msg)
                            except Exception:
                                logging.exception('Error processing response to command')
                        except Exception:
                            pass
                        if command_id in self.command_responses:
                            ret = self.command_responses[command_id]
                            del self.command_responses[command_id]
            except Exception as err:
                logging.exception("Websocket send error: %s", err.__str__())
        return ret

    def flush_pending_messages(self):
        """Clear out any pending websocket messages"""
        if self.websocket:
            try:
                while True and not self.must_exit:
                    msg = self.messages.get(timeout=0)
                    try:
                        if msg:
                            self.process_message(msg)
                    except Exception:
                        logging.exception('Error processing message')
            except Exception:
                pass

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.task['page_data']['URL'] = command['target']
            self.main_frame = None
            self.main_request = None
            self.is_navigating = True
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
            if command['record']:
                self.main_frame = None
                self.main_request = None
                self.is_navigating = True
            self.execute_js(command['target'], remove_orange=self.recording)
        elif command['command'] == 'sleep':
            available_sleep = 60 - self.total_sleep
            delay = min(available_sleep, max(0, int(re.search(r'\d+', str(command['target'])).group())))
            if delay > 0:
                self.total_sleep += delay
                time.sleep(delay)
        elif command['command'] == 'setabm':
            self.task['stop_at_onload'] = \
                bool('target' in command and int(re.search(r'\d+',
                                                           str(command['target'])).group()) == 0)
        elif command['command'] == 'setactivitytimeout':
            if 'target' in command:
                milliseconds = int(re.search(r'\d+', str(command['target'])).group())
                self.task['activity_time'] = max(0, min(30, float(milliseconds) / 1000.0))
        elif command['command'] == 'setminimumstepseconds':
            self.task['minimumTestSeconds'] = int(re.search(r'\d+', str(command['target'])).group())
        elif command['command'] == 'setuseragent':
            self.job['user_agent_string'] = command['target']
        elif command['command'] == 'setcookie':
            if 'target' in command and 'value' in command:
                try:
                    url = command['target'].strip()
                    cookie = command['value']
                    pos = cookie.find(';')
                    if pos > 0:
                        cookie = cookie[:pos]
                    pos = cookie.find('=')
                    if pos > 0:
                        name = cookie[:pos].strip()
                        value = cookie[pos + 1:].strip()
                        if len(name) and len(value) and len(url):
                            self.ios.set_cookie(url, name, value)
                except Exception:
                    logging.exception('Error setting cookie')
        elif command['command'] == 'clearcache':
            self.ios.clear_cache()
        elif command['command'] == 'addheader':
            self.set_header(command['target'])
        elif command['command'] == 'setheader':
            self.set_header(command['target'])
        elif command['command'] == 'resetheaders':
            self.reset_headers()
        elif command['command'] == 'waitfor':
            try:
                self.wait_for_script = command['target'] if command['target'] else None
            except Exception:
                logging.exception('Error processing waitfor command')
        elif command['command'] == 'waitinterval':
            try:
                interval = float(command['target'])
                if interval > 0:
                    self.wait_interval = interval
            except Exception:
                logging.exception('Error processing waitfor command')

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.connected:
            self.ios.navigate(url)

    def grab_screenshot(self, path, png=True, resize=0):
        """Save the screen shot (png or jpeg)"""
        if self.connected and not self.must_exit:
            data = self.ios.screenshot()
            if data:
                resize_string = '' if not resize else '-resize {0:d}x{0:d} '.format(resize)
                if png:
                    with open(path, 'wb') as image_file:
                        image_file.write(data)
                    if resize_string:
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

    def get_empty_request(self, request_id, url):
        """Return and empty, initialized request"""
        parts = urlsplit(url)
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
        if parts.query:
            request['url'] += '?' + parts.query
        return request

    def process_requests(self, raw_requests):
        """Convert all of the request events into the format needed for WPT"""
        requests = []
        if 'start' in self.page:
            start = self.page['start']
            for request_id in raw_requests:
                r = raw_requests[request_id]
                request = self.get_empty_request(request_id, r['url'])
                if 'ip' in r:
                    request['ip_addr'] = r['ip']
                if 'connection' in r:
                    request['socket'] = r['connection']
                if 'priority' in r:
                    request['priority'] = r['priority']
                if 'protocol' in r:
                    request['protocol'] = r['protocol']
                if 'method' in r:
                    request['method'] = r['method']
                if 'status' in r:
                    request['responseCode'] = r['status']
                if 'type' in r:
                    request['requestType'] = r['type']
                if 'created' in r:
                    request['created'] = int(round((r['created'] - start) * 1000.0))
                request['load_start'] = int(round((r['start'] - start) * 1000.0))
                if 'end' in r:
                    request['load_ms'] = int(round((r['end'] - r['start']) * 1000.0))
                if 'firstByte' in r:
                    request['ttfb_ms'] = int(round((r['firstByte'] - r['start']) * 1000.0))
                if 'timing' in r and not r['is_redirect']:
                    start_ms = int(request['load_start'])
                    timing = r['timing']
                    if timing['domainLookupStart'] > 0 or timing['domainLookupEnd'] > 0:
                        request['dns_start'] = int(round(start_ms + timing['domainLookupStart']))
                        request['dns_end'] = int(round(start_ms + timing['domainLookupEnd']))
                    if timing['connectStart'] > 0 or timing['connectEnd'] > 0:
                        request['connect_start'] = int(round(start_ms + timing['connectStart']))
                        request['connect_end'] = int(round(start_ms + timing['connectEnd']))
                        if timing['secureConnectionStart'] >= 0:
                            request['ssl_start'] = int(round(start_ms +
                                                             timing['secureConnectionStart']))
                            request['ssl_end'] = request['connect_end']
                            request['connect_end'] = request['ssl_start']
                    if timing['requestStart'] >= 0:
                        request['load_start'] = int(round(start_ms + timing['requestStart']))
                        request['load_ms'] -= int(round(timing['requestStart']))
                        request['ttfb_ms'] -= int(round(timing['requestStart']))
                        if timing['responseStart'] >= 0:
                            request['ttfb_ms'] = int(round(timing['responseStart'] -
                                                           timing['requestStart']))
                if 'chunks' in r:
                    request['chunks'] = []
                    for chunk in r['chunks']:
                        ts = (chunk['ts'] - start) * 1000.0
                        request['chunks'].append({'ts': ts, 'bytes': chunk['bytes']})
                request['bytesIn'] = r['bytesIn']
                if 'bytesOut' in r:
                    request['bytesOut'] = r['bytesOut']
                if 'objectSize' in r:
                    request['objectSize'] = r['objectSize']
                if 'objectSizeUncompressed' in r:
                    request['objectSizeUncompressed'] = r['objectSizeUncompressed']
                if 'initiator' in r:
                    if 'url' in r['initiator']:
                        request['initiator'] = r['initiator']['url']
                        if 'lineNumber' in r['initiator']:
                            request['initiator_line'] = r['initiator']['lineNumber']
                    elif 'stackTrace' in r['initiator'] and r['initiator']['stackTrace']:
                        for entry in r['initiator']['stackTrace']:
                            if 'url' in entry and entry['url'].startswith('http'):
                                request['initiator'] = entry['url']
                                if 'lineNumber' in entry:
                                    request['initiator_line'] = entry['lineNumber']
                                    if 'columnNumber' in entry:
                                        request['initiator_column'] = entry['columnNumber']
                                break
                if 'request_headers' in r:
                    for name in r['request_headers']:
                        for value in r['request_headers'][name].splitlines():
                            request['headers']['request'].append(u'{0}: {1}'.format(name, value))
                if 'response_headers' in r:
                    for name in r['response_headers']:
                        for value in r['response_headers'][name].splitlines():
                            request['headers']['response'].append(u'{0}: {1}'.format(name, value))
                    value = self.get_header_value(r['response_headers'], 'Expires')
                    if value:
                        request['expires'] = value
                    value = self.get_header_value(r['response_headers'], 'Cache-Control')
                    if value:
                        request['cacheControl'] = value
                    value = self.get_header_value(r['response_headers'], 'Content-Type')
                    if value:
                        request['contentType'] = value
                    value = self.get_header_value(r['response_headers'], 'Content-Encoding')
                    if value:
                        request['contentEncoding'] = value
                    # If a content-length header is available, use that instead of the values
                    # reported by Safari which only show the unencoded size (even though it
                    # claims otherwise).
                    try:
                        value = self.get_header_value(r['response_headers'], 'Content-Length')
                        if value:
                            content_length = int(value)
                            if content_length >= 0:
                                request['objectSize'] = content_length
                                request['bytesIn'] = content_length + \
                                        sum(len(s) for s in request['headers']['response'])
                    except Exception:
                        logging.exception('Error processing response length')
                requests.append(request)
        # Strip the headers if necessary
        noheaders = False
        if 'noheaders' in self.job and self.job['noheaders']:
            noheaders = True
        if noheaders:
            for request in requests:
                if 'headers' in request:
                    del request['headers']
        requests.sort(key=lambda x: x['load_start'])
        return requests

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
                'optimization_checked': 0
               }
        if 'loadEventStart' in self.task['page_data']:
            page['loadTime'] = self.task['page_data']['loadEventStart']
            page['docTime'] = page['loadTime']
            page['loadEventStart'] = page['loadTime']
            page['loadEventEnd'] = page['loadTime']
        if 'loaded' in self.page:
            page['loadTime'] = int(round((self.page['loaded'] - self.page['start']) * 1000.0))
            page['docTime'] = page['loadTime']
            page['loadEventStart'] = page['loadTime']
            page['loadEventEnd'] = page['loadTime']
        if 'DOMContentLoaded' in self.page:
            page['domContentLoadedEventStart'] = int(round((self.page['DOMContentLoaded'] -
                                                            self.page['start']) * 1000.0))
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
                if 'URL' not in self.task['page_data']:
                    self.task['page_data']['URL'] = page['final_url']
                if request['ttfb_ms'] >= 0:
                    page['TTFB'] = request['load_start'] + request['ttfb_ms']
                if request['ssl_end'] >= request['ssl_start'] and \
                        request['ssl_start'] >= 0:
                    page['basePageSSLTime'] = int(round(request['ssl_end'] - \
                                                        request['ssl_start']))
        if self.nav_error_code is not None:
            page['result'] = self.nav_error_code
        elif page['responses_200'] == 0 and len(requests):
            if 'responseCode' in requests[0]:
                page['result'] = requests[0]['responseCode']
            else:
                page['result'] = 12999
        self.task['page_result'] = page['result']
        return page

    def process_optimization_results(self, page_data, requests, optimization_results):
        """Merge the data from the optimization checks file"""
        if optimization_results and not self.must_exit:
            page_data['score_cache'] = -1
            page_data['score_cdn'] = -1
            page_data['score_gzip'] = -1
            page_data['score_cookies'] = -1
            page_data['score_keep-alive'] = -1
            page_data['score_minify'] = -1
            page_data['score_combine'] = -1
            page_data['score_compress'] = -1
            page_data['score_etags'] = -1
            page_data['score_progressive_jpeg'] = -1
            page_data['gzip_total'] = 0
            page_data['gzip_savings'] = 0
            page_data['minify_total'] = -1
            page_data['minify_savings'] = -1
            page_data['image_total'] = 0
            page_data['image_savings'] = 0
            page_data['optimization_checked'] = 1
            page_data['base_page_cdn'] = ''
            cache_count = 0
            cache_total = 0
            cdn_count = 0
            cdn_total = 0
            keep_alive_count = 0
            keep_alive_total = 0
            progressive_total_bytes = 0
            progressive_bytes = 0
            for request in requests:
                if request['responseCode'] == 200:
                    request_id = str(request['id'])
                    pos = request_id.find('-')
                    if pos > 0:
                        request_id = request_id[:pos]
                    if request_id in optimization_results:
                        opt = optimization_results[request_id]
                        if 'cache' in opt:
                            request['score_cache'] = opt['cache']['score']
                            request['cache_time'] = opt['cache']['time']
                            cache_count += 1
                            cache_total += request['score_cache']
                        if 'cdn' in opt:
                            request['score_cdn'] = opt['cdn']['score']
                            request['cdn_provider'] = opt['cdn']['provider']
                            cdn_count += 1
                            cdn_total += request['score_cdn']
                            if 'is_base_page' in request and request['is_base_page'] and \
                                    request['cdn_provider'] is not None:
                                page_data['base_page_cdn'] = request['cdn_provider']
                        if 'keep_alive' in opt:
                            request['score_keep-alive'] = opt['keep_alive']['score']
                            keep_alive_count += 1
                            keep_alive_total += request['score_keep-alive']
                        if 'gzip' in opt:
                            savings = opt['gzip']['size'] - opt['gzip']['target_size']
                            request['score_gzip'] = opt['gzip']['score']
                            request['gzip_total'] = opt['gzip']['size']
                            request['gzip_save'] = savings
                            page_data['gzip_total'] += opt['gzip']['size']
                            page_data['gzip_savings'] += savings
                        if 'image' in opt:
                            savings = opt['image']['size'] - opt['image']['target_size']
                            request['score_compress'] = opt['image']['score']
                            request['image_total'] = opt['image']['size']
                            request['image_save'] = savings
                            page_data['image_total'] += opt['image']['size']
                            page_data['image_savings'] += savings
                        if 'progressive' in opt:
                            size = opt['progressive']['size']
                            request['jpeg_scan_count'] = opt['progressive']['scan_count']
                            progressive_total_bytes += size
                            if request['jpeg_scan_count'] > 1:
                                request['score_progressive_jpeg'] = 100
                                progressive_bytes += size
                            elif size < 10240:
                                request['score_progressive_jpeg'] = 50
                            else:
                                request['score_progressive_jpeg'] = 0
            if cache_count > 0:
                page_data['score_cache'] = int(round(cache_total / cache_count))
            if cdn_count > 0:
                page_data['score_cdn'] = int(round(cdn_total / cdn_count))
            if keep_alive_count > 0:
                page_data['score_keep-alive'] = int(round(keep_alive_total / keep_alive_count))
            if page_data['gzip_total'] > 0:
                page_data['score_gzip'] = 100 - int(page_data['gzip_savings'] * 100 /
                                                    page_data['gzip_total'])
            if page_data['image_total'] > 0:
                page_data['score_compress'] = 100 - int(page_data['image_savings'] * 100 /
                                                        page_data['image_total'])
            if progressive_total_bytes > 0:
                page_data['score_progressive_jpeg'] = int(round(progressive_bytes * 100 /
                                                                progressive_total_bytes))

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
                if message.find("Timeline.eventRecorded") == -1:
                    logging.debug(message[:200])
                if message:
                    message = json.loads(message)
                    if message:
                        self.messages.put(message)
        except Exception:
            logging.exception('Error processing received message')
