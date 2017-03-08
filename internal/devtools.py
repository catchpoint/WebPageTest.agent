# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for interfacing with Chrome's remote debugging protocol"""
import base64
import gzip
import logging
import os
import subprocess
import time
import monotonic
import ujson as json

class DevTools(object):
    """Interface into Chrome's remote dev tools protocol"""
    def __init__(self, job, task):
        self.url = "http://localhost:{0:d}/json".format(task['port'])
        self.websocket = None
        self.job = job
        self.task = task
        self.command_id = 0
        self.page_loaded = False
        self.main_frame = None
        self.is_navigating = False
        self.last_activity = monotonic.monotonic()
        self.dev_tools_file = None
        self.trace_file = None
        self.trace_enabled = False
        self.prepare()

    def prepare(self):
        """Set up the various paths and states"""
        self.requests = {}
        self.trace_ts_start = None
        self.nav_error = None
        self.main_request = None
        self.path_base = os.path.join(self.task['dir'], self.task['prefix'])
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
        self.video_path = os.path.join(self.task['dir'], self.task['video_subdirectory'])
        self.video_prefix = os.path.join(self.video_path, 'ms_')
        if not os.path.isdir(self.video_path):
            os.makedirs(self.video_path)

    def start_navigating(self):
        """Indicate that we are about to start a known-navigation"""
        self.main_frame = None
        self.is_navigating = True

    def connect(self, timeout):
        """Connect to the browser"""
        import requests
        ret = False
        end_time = monotonic.monotonic() + timeout
        while not ret and monotonic.monotonic() < end_time:
            try:
                response = requests.get(self.url, timeout=timeout)
                if len(response.text):
                    tabs = response.json()
                    logging.debug("Dev Tools tabs: %s", json.dumps(tabs))
                    if len(tabs):
                        websocket_url = None
                        for index in xrange(len(tabs)):
                            if 'type' in tabs[index] and \
                                    tabs[index]['type'] == 'page' and \
                                    'webSocketDebuggerUrl' in tabs[index] and \
                                    'id' in tabs[index]:
                                if websocket_url is None:
                                    websocket_url = tabs[index]['webSocketDebuggerUrl']
                                else:
                                    # Close extra tabs
                                    requests.get(self.url + '/close/' + tabs[index]['id'])
                        if websocket_url is not None:
                            from websocket import create_connection
                            self.websocket = create_connection(websocket_url)
                            if self.websocket:
                                self.websocket.settimeout(1)
                                ret = True
                        else:
                            time.sleep(1)
            except BaseException as err:
                logging.critical("Connect to dev tools Error: %s", err.__str__())
                time.sleep(1)
        return ret

    def close(self):
        """Close the dev tools connection"""
        if self.websocket:
            self.websocket.close()
            self.websocket = None

    def start_recording(self):
        """Start capturing dev tools, timeline and trace data"""
        self.prepare()
        if 'Capture Video' in self.job and self.job['Capture Video'] and self.task['log_data']:
            self.grab_screenshot(self.video_prefix + '000000.png')
        self.flush_pending_messages()
        self.send_command('Page.enable', {})
        self.send_command('Network.enable', {})
        if self.task['log_data']:
            self.send_command('Security.enable', {})
            self.send_command('Console.enable', {})
            if 'trace' in self.job and self.job['trace']:
                if 'traceCategories' in self.job:
                    trace = self.job['traceCategories']
                else:
                    trace = "-*,blink,v8,cc,gpu,blink.net,netlog,disabled-by-default-v8.runtime_stats"
            else:
                trace = "-*"
            if 'timeline' in self.job and self.job['timeline']:
                trace += ",blink.console,disabled-by-default-devtools.timeline,devtools.timeline"
                trace += ",disabled-by-default-blink.feature_usage"
                trace += ",toplevel,disabled-by-default-devtools.timeline.frame,devtools.timeline.frame"
            if 'Capture Video' in self.job and self.job['Capture Video']:
                trace += ",disabled-by-default-devtools.screenshot"
            trace += ",blink.user_timing"
            self.trace_enabled = True
            self.send_command('Tracing.start',
                            {'categories': trace, 'options': 'record-as-much-as-possible'})
        if 'web10' not in self.task or not self.task['web10']:
            self.last_activity = monotonic.monotonic()

    def stop_recording(self):
        """Stop capturing dev tools, timeline and trace data"""
        self.send_command('Page.disable', {})
        if self.task['log_data']:
            self.send_command('Security.disable', {})
            self.send_command('Console.disable', {})
            self.get_response_bodies()
        self.send_command('Network.disable', {})
        if self.dev_tools_file is not None:
            self.dev_tools_file.write("\n]")
            self.dev_tools_file.close()
            self.dev_tools_file = None
        self.collect_trace()

    def collect_trace(self):
        """Stop tracing and collect the results"""
        if self.trace_enabled:
            self.trace_enabled = False
            self.send_command('Tracing.end', {})
            # Keep pumping messages until we get tracingComplete or
            # we get a gap of 30 seconds between messages
            if self.websocket:
                logging.info('Collecting trace events')
                done = False
                last_message = monotonic.monotonic()
                self.websocket.settimeout(1)
                while not done and monotonic.monotonic() - last_message < 30:
                    try:
                        raw = self.websocket.recv()
                        if raw is not None and len(raw):
                            msg = json.loads(raw)
                            if 'method' in msg:
                                if msg['method'] == 'Tracing.tracingComplete':
                                    done = True
                                elif msg['method'] == 'Tracing.dataCollected':
                                    last_message = monotonic.monotonic()
                                    self.process_trace_event(msg)
                    except BaseException as _:
                        pass
            if self.trace_file is not None:
                self.trace_file.write("\n]}")
                self.trace_file.close()
                self.trace_file = None

    def get_response_bodies(self):
        """Retrieve all of the response bodies for the requests that we know about"""
        import zipfile
        if self.requests:
            # see if we also need to zip them up
            zip_file = None
            if 'bodies' in self.job and self.job['bodies']:
                zip_file = zipfile.ZipFile(self.path_base + 'bodies.zip', 'w', zipfile.ZIP_DEFLATED)
            path = os.path.join(self.task['dir'], 'bodies')
            if not os.path.isdir(path):
                os.makedirs(path)
            index = 0
            for request_id in self.requests:
                if 'finished' in self.requests[request_id] and \
                        'fromNet' in self.requests[request_id] and \
                        self.requests[request_id]['fromNet']:
                    index += 1
                    body_file_path = os.path.join(path, request_id)
                    if not os.path.exists(body_file_path):
                        response = self.send_command("Network.getResponseBody",
                                                     {'requestId': request_id}, wait=True)
                        if response is not None and 'result' in response and \
                                'body' in response['result'] and len(response['result']['body']):
                            # Write the raw body to a file (all bodies)
                            if 'base64Encoded' in response['result'] and \
                                    response['result']['base64Encoded']:
                                with open(body_file_path, 'wb') as body_file:
                                    body_file.write(base64.b64decode(response['result']['body']))
                            else:
                                body = response['result']['body'].encode('utf-8')
                                with open(body_file_path, 'wb') as body_file:
                                    body_file.write(body)
                                # Add text bodies to the zip archive
                                if zip_file is not None:
                                    name = '{0:03d}-{1}-body.txt'.format(index, request_id)
                                    zip_file.writestr(name, body)
            if zip_file is not None:
                zip_file.close()

    def flush_pending_messages(self):
        """Clear out any pending websocket messages"""
        if self.websocket:
            try:
                self.websocket.settimeout(0)
                while True:
                    raw = self.websocket.recv()
                    if not raw:
                        break
            except BaseException as _:
                pass

    def send_command(self, method, params, wait=False, timeout=30):
        """Send a raw dev tools message and optionally wait for the response"""
        ret = None
        if self.websocket:
            self.command_id += 1
            msg = {'id': self.command_id, 'method': method, 'params': params}
            try:
                out = json.dumps(msg)
                logging.debug("Sending: %s", out)
                self.websocket.send(out)
                if wait:
                    self.websocket.settimeout(1)
                    end_time = monotonic.monotonic() + timeout
                    while ret is None and monotonic.monotonic() < end_time:
                        try:
                            raw = self.websocket.recv()
                            if raw is not None and len(raw):
                                logging.debug(raw[:1000])
                                msg = json.loads(raw)
                                if 'id' in msg and int(msg['id']) == self.command_id:
                                    ret = msg
                        except BaseException as _:
                            pass
            except BaseException as err:
                logging.critical("Websocket send error: %s", err.__str__())
        return ret

    def wait_for_page_load(self):
        """Wait for the page load and activity to finish"""
        if self.websocket:
            self.websocket.settimeout(1)
            now = monotonic.monotonic()
            end_time = now + self.task['time_limit']
            done = False
            while not done:
                try:
                    raw = self.websocket.recv()
                    if raw is not None and len(raw):
                        logging.debug(raw[:1000])
                        msg = json.loads(raw)
                        if 'method' in msg:
                            self.process_message(msg)
                except BaseException as _:
                    # ignore timeouts when we're in a polling read loop
                    pass
                now = monotonic.monotonic()
                elapsed_activity = now - self.last_activity
                if self.page_loaded and elapsed_activity >= 2:
                    done = True
                elif self.task['error'] is not None:
                    done = True
                elif now >= end_time:
                    done = True
                    self.task['error'] = "Page Load Timeout"

    def grab_screenshot(self, path, png=True):
        """Save the screen shot (png or jpeg)"""
        response = self.send_command("Page.captureScreenshot", {}, wait=True, timeout=5)
        if response is not None and 'result' in response and 'data' in response['result']:
            if png:
                with open(path, 'wb') as image_file:
                    image_file.write(base64.b64decode(response['result']['data']))
            else:
                tmp_file = path + '.png'
                with open(tmp_file, 'wb') as image_file:
                    image_file.write(base64.b64decode(response['result']['data']))
                command = 'convert -quality {0:d} "{1}" "{2}"'.format(
                    self.job['iq'], tmp_file, path)
                logging.debug(command)
                subprocess.call(command, shell=True)
                if os.path.isfile(tmp_file):
                    os.remove(tmp_file)

    def execute_js(self, script):
        """Run the provided JS in the browser and return the result"""
        ret = None
        response = self.send_command("Runtime.evaluate",
                                     {'expression': script, 'returnByValue': True},
                                     wait=True)
        if response is not None and 'result' in response and\
                'result' in response['result'] and\
                'value' in response['result']['result']:
            ret = response['result']['result']['value']
        return ret

    def process_message(self, msg):
        """Process an inbound dev tools message"""
        parts = msg['method'].split('.')
        if len(parts) >= 2:
            category = parts[0]
            event = parts[1]
            if category == 'Page':
                self.process_page_event(event, msg)
                self.log_dev_tools_event(msg)
            elif category == 'Network':
                self.process_network_event(event, msg)
                self.log_dev_tools_event(msg)
            elif category == 'Inspector':
                self.process_inspector_event(event)
            elif category == 'Tracing':
                self.process_trace_event(msg)
            else:
                self.log_dev_tools_event(msg)

    def process_page_event(self, event, msg):
        """Process Page.* dev tools events"""
        if event == 'loadEventFired':
            self.page_loaded = True
        elif event == 'frameStartedLoading' and 'params' in msg and 'frameId' in msg['params']:
            if self.is_navigating and self.main_frame is None:
                self.is_navigating = False
                self.main_frame = msg['params']['frameId']
            if self.main_frame == msg['params']['frameId']:
                logging.debug("Navigating main frame")
                self.last_activity = monotonic.monotonic()
                self.page_loaded = False
        elif event == 'frameStoppedLoading' and 'params' in msg and 'frameId' in msg['params']:
            if self.main_frame is not None and \
                    not self.page_loaded and \
                    self.main_frame == msg['params']['frameId']:
                if self.nav_error is not None:
                    self.task['error'] = self.nav_error
                    logging.debug("Page load failed: %s", self.nav_error)
                self.page_loaded = True
        elif event == 'javascriptDialogOpening':
            self.task['error'] = "Page opened a modal dailog"

    def process_network_event(self, event, msg):
        """Process Network.* dev tools events"""
        if 'web10' not in self.task or not self.task['web10']:
            self.last_activity = monotonic.monotonic()
        if 'requestId' in msg['params']:
            request_id = msg['params']['requestId']
            if request_id not in self.requests:
                request = {'id': request_id}
                self.requests[request_id] = request
            if event == 'requestWillBeSent':
                self.requests[request_id]['request'] = msg['params']
                self.requests[request_id]['fromNet'] = True
                if self.main_frame is not None and \
                        self.main_request is None and \
                        'frameId' in msg['params'] and \
                        msg['params']['frameId'] == self.main_frame:
                    logging.debug('Main request detected')
                    self.main_request = request_id
            elif event == 'resourceChangedPriority':
                self.requests[request_id]['priority'] = msg['params']
            elif event == 'requestServedFromCache':
                self.requests[request_id]['fromNet'] = False
            elif event == 'responseReceived':
                self.requests[request_id]['response'] = msg['params']
                if 'response' in msg['params'] and \
                        'fromDiskCache' in msg['params']['response'] and \
                        msg['params']['response']['fromDiskCache']:
                    self.requests[request_id]['fromNet'] = False
            elif event == 'dataReceived':
                if 'data' not in self.requests[request_id]:
                    self.requests[request_id]['data'] = []
                self.requests[request_id]['data'].append(msg['params'])
            elif event == 'loadingFinished':
                self.requests[request_id]['finished'] = msg['params']
            elif event == 'loadingFailed':
                self.requests[request_id]['failed'] = msg['params']
                if self.main_request is not None and \
                        request_id == self.main_request and \
                        'errorText' in msg['params'] and \
                        'canceled' in msg['params'] and \
                        not msg['params']['canceled']:
                    self.nav_error = msg['params']['errorText']
                    logging.debug('Navigation error: %s', self.nav_error)

    def process_inspector_event(self, event):
        """Process Inspector.* dev tools events"""
        if event == 'detached':
            self.task['error'] = 'Inspector detached, possibly crashed.'
        elif event == 'targetCrashed':
            self.task['error'] = 'Browser crashed.'

    def process_trace_event(self, msg):
        """Process Tracing.* dev tools events"""
        if msg['method'] == 'Tracing.dataCollected' and \
                'params' in msg and \
                'value' in msg['params'] and \
                len(msg['params']['value']):
            if self.trace_file is None:
                self.trace_file = gzip.open(self.path_base + 'trace.json.gz', 'wb')
                self.trace_file.write('{"traceEvents":[{}')
            # write out the trace events one-per-line but pull out any
            # devtools screenshots as separate files.
            if self.trace_file is not None:
                for index in xrange(len(msg['params']['value'])):
                    trace_event = msg['params']['value'][index]
                    is_screenshot = False
                    if 'cat' in trace_event and 'name' in trace_event and 'ts' in trace_event:
                        if self.trace_ts_start is None and \
                                trace_event['name'] == 'navigationStart' and \
                                trace_event['cat'].find('blink.user_timing') > -1:
                            self.trace_ts_start = trace_event['ts']
                        if trace_event['name'] == 'Screenshot' and \
                                trace_event['cat'].find('devtools.screenshot') > -1:
                            is_screenshot = True
                            if self.trace_ts_start is not None and \
                                    'args' in trace_event and \
                                    'snapshot' in trace_event['args']:
                                ms_elapsed = int(round(float(trace_event['ts'] - \
                                                             self.trace_ts_start) / 1000.0))
                                if ms_elapsed >= 0:
                                    path = '{0}{1:06d}.png'.format(self.video_prefix, ms_elapsed)
                                    with open(path, 'wb') as image_file:
                                        image_file.write(
                                            base64.b64decode(trace_event['args']['snapshot']))
                    if not is_screenshot:
                        self.trace_file.write(",\n")
                        self.trace_file.write(json.dumps(trace_event))
                logging.debug("Processed %d trace events", len(msg['params']['value']))

    def log_dev_tools_event(self, msg):
        """Log the dev tools events to a file"""
        if self.task['log_data']:
            if self.dev_tools_file is None:
                path = self.path_base + 'devtools.json.gz'
                self.dev_tools_file = gzip.open(path, 'wb')
                self.dev_tools_file.write("[{}")
            if self.dev_tools_file is not None:
                self.dev_tools_file.write(",\n")
                self.dev_tools_file.write(json.dumps(msg))
