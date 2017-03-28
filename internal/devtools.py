# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for interfacing with Chrome's remote debugging protocol"""
import base64
import gzip
import logging
import os
import Queue
import re
import subprocess
import time
import monotonic
import ujson as json
from ws4py.client.threadedclient import WebSocketClient

class DevTools(object):
    """Interface into Chrome's remote dev tools protocol"""
    def __init__(self, job, task, use_devtools_video):
        self.url = "http://localhost:{0:d}/json".format(task['port'])
        self.websocket = None
        self.job = job
        self.task = task
        self.command_id = 0
        self.page_loaded = None
        self.main_frame = None
        self.is_navigating = False
        self.last_activity = monotonic.monotonic()
        self.dev_tools_file = None
        self.trace_file = None
        self.trace_enabled = False
        self.requests = {}
        self.nav_error = None
        self.main_request = None
        self.path_base = None
        self.support_path = None
        self.video_path = None
        self.video_prefix = None
        self.recording = False
        self.mobile_viewport = None
        self.tab_id = None
        self.use_devtools_video = use_devtools_video
        self.recording_video = False
        self.prepare()

    def prepare(self):
        """Set up the various paths and states"""
        self.requests = {}
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
                                    self.tab_id = tabs[index]['id']
                                else:
                                    # Close extra tabs
                                    requests.get(self.url + '/close/' + tabs[index]['id'])
                        if websocket_url is not None:
                            try:
                                self.websocket = DevToolsClient(websocket_url)
                                self.websocket.connect()
                                ret = True
                            except Exception as err:
                                logging.critical("Connect to dev tools websocket Error: %s",
                                                 err.__str__())
                            if not ret:
                                # try connecting to 127.0.0.1 instead of localhost
                                try:
                                    websocket_url = websocket_url.replace('localhost', '127.0.0.1')
                                    self.websocket = DevToolsClient(websocket_url)
                                    self.websocket.connect()
                                    ret = True
                                except Exception as err:
                                    logging.critical("Connect to dev tools websocket Error: %s",
                                                     err.__str__())
                        else:
                            time.sleep(0.5)
                    else:
                        time.sleep(0.5)
            except Exception as err:
                logging.critical("Connect to dev tools Error: %s", err.__str__())
                time.sleep(1)
        return ret

    def close(self, close_tab=True):
        """Close the dev tools connection"""
        if close_tab and self.tab_id is not None:
            import requests
            requests.get(self.url + '/close/' + self.tab_id)
        if self.websocket:
            self.websocket.close()
            self.websocket = None
        self.tab_id = None

    def start_recording(self):
        """Start capturing dev tools, timeline and trace data"""
        self.prepare()
        self.recording = True
        if self.use_devtools_video and self.job['video'] and self.task['log_data']:
            self.grab_screenshot(self.video_prefix + '000000.png')
        self.flush_pending_messages()
        self.send_command('Page.enable', {})
        self.send_command('Inspector.enable', {})
        self.send_command('Network.enable', {})
        if 'user_agent_string' in self.job:
            self.send_command('Network.setUserAgentOverride',
                              {'userAgent': self.job['user_agent_string']}, wait=True)
        if 'headers' in self.job:
            self.send_command('Network.setExtraHTTPHeaders',
                              {'headers': self.job['headers']}, wait=True)
        if len(self.task['block']):
            for block in self.task['block']:
                self.send_command('Network.addBlockedURL', {'url': block})
        if self.task['log_data']:
            self.send_command('Security.enable', {})
            self.send_command('Console.enable', {})
            if 'trace' in self.job and self.job['trace']:
                if 'traceCategories' in self.job:
                    trace = self.job['traceCategories']
                else:
                    trace = "-*,blink,v8,cc,gpu,blink.net,disabled-by-default-v8.runtime_stats"
            else:
                trace = "-*"
            if 'timeline' in self.job and self.job['timeline']:
                trace += ",blink.console,disabled-by-default-devtools.timeline,devtools.timeline"
                trace += ",disabled-by-default-blink.feature_usage"
                trace += ",toplevel,disabled-by-default-devtools.timeline.frame"
                trace += "devtools.timeline.frame"
            if self.use_devtools_video and self.job['video']:
                trace += ",disabled-by-default-devtools.screenshot"
                self.recording_video = True
            trace += ",blink.user_timing,netlog"
            self.trace_enabled = True
            self.send_command('Tracing.start',
                              {'categories': trace, 'options': 'record-as-much-as-possible'})
        now = monotonic.monotonic()
        if not self.task['stop_at_onload']:
            self.last_activity = now
        if self.page_loaded is not None:
            self.page_loaded = now

    def stop_recording(self):
        """Stop capturing dev tools, timeline and trace data"""
        self.recording = False
        self.send_command('Inspector.disable', {})
        self.send_command('Page.disable', {})
        self.collect_trace()
        if self.task['log_data']:
            self.send_command('Security.disable', {})
            self.send_command('Console.disable', {})
            self.get_response_bodies()
        self.send_command('Network.disable', {})
        if self.dev_tools_file is not None:
            self.dev_tools_file.write("\n]")
            self.dev_tools_file.close()
            self.dev_tools_file = None

    def collect_trace(self):
        """Stop tracing and collect the results"""
        if self.trace_enabled:
            self.trace_enabled = False
            video_prefix = self.video_prefix if self.recording_video else None
            self.websocket.start_processing_trace(self.path_base + '_trace.json', video_prefix)
            self.send_command('Tracing.end', {})
            start = monotonic.monotonic()
            # Keep pumping messages until we get tracingComplete or
            # we get a gap of 30 seconds between messages
            if self.websocket:
                logging.info('Collecting trace events')
                done = False
                no_message_count = 0
                while not done and no_message_count < 30:
                    try:
                        raw = self.websocket.get_message(1)
                        if raw is not None and len(raw):
                            no_message_count = 0
                            msg = json.loads(raw)
                            if 'method' in msg and msg['method'] == 'Tracing.tracingComplete':
                                done = True
                        else:
                            no_message_count += 1
                    except Exception:
                        pass
            self.websocket.stop_processing_trace()
            elapsed = monotonic.monotonic() - start
            logging.debug("Time to collect trace: %0.3f sec", elapsed)
            self.recording_video = False

    def get_response_bodies(self):
        """Retrieve all of the response bodies for the requests that we know about"""
        import zipfile
        requests = self.get_requests()
        if requests:
            # see if we also need to zip them up
            zip_file = None
            if 'bodies' in self.job and self.job['bodies']:
                zip_file = zipfile.ZipFile(self.path_base + '_bodies.zip', 'w',
                                           zipfile.ZIP_DEFLATED)
            path = os.path.join(self.task['dir'], 'bodies')
            if not os.path.isdir(path):
                os.makedirs(path)
            index = 0
            for request_id in requests:
                request = requests[request_id]
                if 'status' in request and \
                        request['status'] == 200 and \
                        'response_headers' in request:
                    content_length = self.get_header_value(request['response_headers'],
                                                           'Content-Length')
                    if content_length is not None:
                        content_length = int(content_length)
                    elif 'transfer_size' in request:
                        content_length = request['transfer_size']
                    if content_length > 0:
                        body_file_path = os.path.join(path, request_id)
                        if not os.path.exists(body_file_path):
                            # Only grab bodies needed for optimization checks
                            # or if we are saving full bodies
                            need_body = False
                            content_type = self.get_header_value(request['response_headers'],
                                                                 'Content-Type')
                            content_encoding = self.get_header_value(request['response_headers'],
                                                                     'Content-Encoding')
                            is_image = False
                            is_text = False
                            is_video = False
                            if content_type is not None:
                                content_type = content_type.lower()
                                if content_type[:6] or \
                                        content_type.find('javascript') >= 0 or \
                                        content_type.find('json') >= 0:
                                    is_text = True
                                if content_type[:6] == 'image/':
                                    is_image = True
                                if content_type[:6] == 'video/':
                                    is_video = True
                            is_compressed = False
                            if content_encoding is not None:
                                content_encoding = content_encoding.lower()
                                if content_encoding.find('gzip') >= 0 or \
                                        content_encoding.find('deflate') >= 0 or \
                                        content_encoding.find('br') >= 0:
                                    is_compressed = True
                            if is_image and content_length >= 1400:
                                need_body = True
                            if not is_compressed and not is_video and content_length >= 1400:
                                need_body = True
                            elif zip_file is not None and is_text:
                                need_body = True
                            if need_body:
                                response = self.send_command("Network.getResponseBody",
                                                             {'requestId': request_id}, wait=True)
                                if response is None or 'result' not in response or \
                                        'body' not in response['result']:
                                    logging.warning('Missing response body for request %s',
                                                    request_id)
                                elif len(response['result']['body']):
                                    # Write the raw body to a file (all bodies)
                                    if 'base64Encoded' in response['result'] and \
                                            response['result']['base64Encoded']:
                                        with open(body_file_path, 'wb') as body_file:
                                            body_file.write(
                                                base64.b64decode(response['result']['body']))
                                    else:
                                        body = response['result']['body'].encode('utf-8')
                                        with open(body_file_path, 'wb') as body_file:
                                            body_file.write(body)
                                        # Add text bodies to the zip archive
                                        if zip_file is not None:
                                            index += 1
                                            name = '{0:03d}-{1}-body.txt'.format(index, request_id)
                                            zip_file.writestr(name, body)
            if zip_file is not None:
                zip_file.close()

    def get_requests(self):
        """Get a dictionary of all of the requests and the details (headers, body file)"""
        requests = None
        if self.requests:
            body_path = os.path.join(self.task['dir'], 'bodies')
            for request_id in self.requests:
                if 'fromNet' in self.requests[request_id] and self.requests[request_id]['fromNet']:
                    events = self.requests[request_id]
                    request = {'id': request_id}
                    # See if we have a body
                    body_file_path = os.path.join(body_path, request_id)
                    if os.path.isfile(body_file_path):
                        request['body'] = body_file_path
                    # Get the headers from responseReceived
                    if 'response' in events:
                        response = events['response'][-1]
                        if 'response' in response:
                            if 'url' in response['response']:
                                request['url'] = response['response']['url']
                            if 'status' in response['response']:
                                request['status'] = response['response']['status']
                            if 'headers' in response['response']:
                                request['response_headers'] = response['response']['headers']
                            if 'requestHeaders' in response['response']:
                                request['request_headers'] = response['response']['requestHeaders']
                            if 'connectionId' in response['response']:
                                request['connection'] = response['response']['connectionId']
                    # Fill in any missing details from the requestWillBeSent event
                    if 'request' in events:
                        req = events['request'][-1]
                        if 'request' in req:
                            if 'url' not in request and 'url' in req['request']:
                                request['url'] = req['request']['url']
                            if 'request_headers' not in request and 'headers' in req['request']:
                                request['request_headers'] = req['request']['headers']
                    # Get the response length from the data events
                    if 'finished' in events and 'encodedDataLength' in events['finished']:
                        request['transfer_size'] = events['finished']['encodedDataLength']
                    elif 'data' in events:
                        transfer_size = 0
                        for data in events['data']:
                            if 'encodedDataLength' in data:
                                transfer_size += data['encodedDataLength']
                            elif 'dataLength' in data:
                                transfer_size += data['dataLength']
                        request['transfer_size'] = transfer_size

                    if requests is None:
                        requests = {}
                    requests[request_id] = request
        return requests

    def flush_pending_messages(self):
        """Clear out any pending websocket messages"""
        if self.websocket:
            try:
                while True:
                    raw = self.websocket.get_message(0)
                    if raw is not None and len(raw):
                        logging.debug(raw[:200])
                        msg = json.loads(raw)
                        self.process_message(msg)
                    if not raw:
                        break
            except Exception:
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
                    end_time = monotonic.monotonic() + timeout
                    while ret is None and monotonic.monotonic() < end_time:
                        try:
                            raw = self.websocket.get_message(1)
                            if raw is not None and len(raw):
                                logging.debug(raw[:200])
                                msg = json.loads(raw)
                                self.process_message(msg)
                                if 'id' in msg and int(msg['id']) == self.command_id:
                                    ret = msg
                        except Exception:
                            pass
            except Exception as err:
                logging.critical("Websocket send error: %s", err.__str__())
        return ret

    def wait_for_page_load(self):
        """Wait for the page load and activity to finish"""
        if self.websocket:
            start_time = monotonic.monotonic()
            end_time = start_time + self.task['time_limit']
            done = False
            while not done:
                try:
                    raw = self.websocket.get_message(1)
                    if raw is not None and len(raw):
                        logging.debug(raw[:200])
                        msg = json.loads(raw)
                        self.process_message(msg)
                except Exception:
                    # ignore timeouts when we're in a polling read loop
                    pass
                now = monotonic.monotonic()
                elapsed_test = now - start_time
                if now >= end_time:
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

    def grab_screenshot(self, path, png=True):
        """Save the screen shot (png or jpeg)"""
        response = self.send_command("Page.captureScreenshot", {}, wait=True, timeout=5)
        if response is not None and 'result' in response and 'data' in response['result']:
            if png:
                with open(path, 'wb') as image_file:
                    image_file.write(base64.b64decode(response['result']['data']))
                self.crop_screen_shot(path)
            else:
                tmp_file = path + '.png'
                with open(tmp_file, 'wb') as image_file:
                    image_file.write(base64.b64decode(response['result']['data']))
                self.crop_screen_shot(tmp_file)
                command = 'convert -quality {0:d} "{1}" "{2}"'.format(
                    self.job['iq'], tmp_file, path)
                logging.debug(command)
                subprocess.call(command, shell=True)
                if os.path.isfile(tmp_file):
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass

    def colors_are_similar(self, color1, color2, threshold=15):
        """See if 2 given pixels are of similar color"""
        similar = True
        delta_sum = 0
        for value in xrange(3):
            delta = abs(color1[value] - color2[value])
            delta_sum += delta
            if delta > threshold:
                similar = False
        if delta_sum > threshold:
            similar = False
        return similar

    def crop_screen_shot(self, path):
        """Crop to the viewport (for mobile tests)"""
        if 'mobile' in self.job and self.job['mobile']:
            try:
                # detect the viewport if we haven't already
                if self.mobile_viewport is None:
                    from PIL import Image
                    image = Image.open(path)
                    width, height = image.size
                    pixels = image.load()
                    background = pixels[10, 10]
                    viewport_width = None
                    viewport_height = None
                    x_pos = 10
                    y_pos = 10
                    while viewport_width is None and x_pos < width:
                        pixel_color = pixels[x_pos, y_pos]
                        if not self.colors_are_similar(background, pixel_color):
                            viewport_width = x_pos
                        else:
                            x_pos += 1
                    if viewport_width is None:
                        viewport_width = width
                    x_pos = 10
                    while viewport_height is None and y_pos < height:
                        pixel_color = pixels[x_pos, y_pos]
                        if not self.colors_are_similar(background, pixel_color):
                            viewport_height = y_pos
                        else:
                            y_pos += 1
                    if viewport_height is None:
                        viewport_height = height
                    self.mobile_viewport = '{0:d}x{1:d}+0+0'.format(viewport_width, viewport_height)
                    logging.debug('Mobile viewport found: %s in %dx%d screen shot',
                                  self.mobile_viewport, width, height)
                if self.mobile_viewport is not None:
                    command = 'mogrify -crop {0} "{1}"'.format(self.mobile_viewport, path)
                    logging.debug(command)
                    subprocess.call(command, shell=True)
            except Exception:
                pass

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
        if 'method' in msg and self.recording:
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
                else:
                    self.log_dev_tools_event(msg)

    def process_page_event(self, event, msg):
        """Process Page.* dev tools events"""
        if event == 'loadEventFired':
            self.page_loaded = monotonic.monotonic()
        elif event == 'frameStartedLoading' and 'params' in msg and 'frameId' in msg['params']:
            if self.is_navigating and self.main_frame is None:
                self.is_navigating = False
                self.main_frame = msg['params']['frameId']
            if self.main_frame == msg['params']['frameId']:
                logging.debug("Navigating main frame")
                self.last_activity = monotonic.monotonic()
                self.page_loaded = None
        elif event == 'frameStoppedLoading' and 'params' in msg and 'frameId' in msg['params']:
            if self.main_frame is not None and \
                    not self.page_loaded and \
                    self.main_frame == msg['params']['frameId']:
                if self.nav_error is not None:
                    self.task['error'] = self.nav_error
                    logging.debug("Page load failed: %s", self.nav_error)
                self.page_loaded = monotonic.monotonic()
        elif event == 'javascriptDialogOpening':
            self.task['error'] = "Page opened a modal dailog"

    def process_network_event(self, event, msg):
        """Process Network.* dev tools events"""
        if not self.task['stop_at_onload']:
            self.last_activity = monotonic.monotonic()
        if 'requestId' in msg['params']:
            request_id = msg['params']['requestId']
            if request_id not in self.requests:
                request = {'id': request_id}
                self.requests[request_id] = request
            if event == 'requestWillBeSent':
                if 'request' not in self.requests[request_id]:
                    self.requests[request_id]['request'] = []
                self.requests[request_id]['request'].append(msg['params'])
                self.requests[request_id]['fromNet'] = True
                if self.main_frame is not None and \
                        self.main_request is None and \
                        'frameId' in msg['params'] and \
                        msg['params']['frameId'] == self.main_frame:
                    logging.debug('Main request detected')
                    self.main_request = request_id
            elif event == 'resourceChangedPriority':
                if 'priority' not in self.requests[request_id]:
                    self.requests[request_id]['priority'] = []
                self.requests[request_id]['priority'].append(msg['params'])
            elif event == 'requestServedFromCache':
                self.requests[request_id]['fromNet'] = False
            elif event == 'responseReceived':
                if 'response' not in self.requests[request_id]:
                    self.requests[request_id]['response'] = []
                self.requests[request_id]['response'].append(msg['params'])
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

    def log_dev_tools_event(self, msg):
        """Log the dev tools events to a file"""
        if self.task['log_data']:
            if self.dev_tools_file is None:
                path = self.path_base + '_devtools.json.gz'
                self.dev_tools_file = gzip.open(path, 'wb', 7)
                self.dev_tools_file.write("[{}")
            if self.dev_tools_file is not None:
                self.dev_tools_file.write(",\n")
                self.dev_tools_file.write(json.dumps(msg))

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

class DevToolsClient(WebSocketClient):
    """DevTools Websocket client"""
    def __init__(self, url, protocols=None, extensions=None, heartbeat_freq=None,
                 ssl_options=None, headers=None):
        WebSocketClient.__init__(self, url, protocols, extensions, heartbeat_freq,
                                 ssl_options, headers)
        self.connected = False
        self.messages = Queue.Queue()
        self.trace_file_path = None
        self.trace_file = None
        self.video_prefix = None
        self.trace_ts_start = None
        self.trace_data = re.compile(r'method"\s*:\s*"Tracing.dataCollected')
        self.trace_done = re.compile(r'method"\s*:\s*"Tracing.tracingComplete')

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
                compare = message[:50]
                is_trace_data = False
                if self.trace_file_path is not None and self.trace_data.search(compare):
                    is_trace_data = True
                    msg = json.loads(message)
                    self.messages.put('{"method":"got_message"}')
                    if msg is not None:
                        self.process_trace_event(msg)
                elif self.trace_file is not None and self.trace_done.search(compare):
                    self.trace_file.write("\n]}")
                    self.trace_file.close()
                    self.trace_file = None
                if not is_trace_data:
                    self.messages.put(message)
        except Exception:
            pass

    def get_message(self, timeout):
        """Wait for and return a message from the queue"""
        message = None
        try:
            if timeout is None or timeout <= 0:
                message = self.messages.get_nowait()
            else:
                message = self.messages.get(True, timeout)
            self.messages.task_done()
        except Exception:
            pass
        return message

    def start_processing_trace(self, trace_file, video_prefix):
        """Write any trace events to the given file"""
        self.trace_ts_start = None
        self.trace_file_path = trace_file
        self.video_prefix = video_prefix

    def stop_processing_trace(self):
        """All done"""
        self.trace_ts_start = None
        self.trace_file_path = None
        if self.trace_file is not None:
            self.trace_file.close()
            self.trace_file = None

    def process_trace_event(self, msg):
        """Process Tracing.* dev tools events"""
        if 'params' in msg and 'value' in msg['params'] and len(msg['params']['value']):
            if self.trace_file is None:
                self.trace_file = open(self.trace_file_path, 'wb')
                self.trace_file.write('{"traceEvents":[{}')
            # write out the trace events one-per-line but pull out any
            # devtools screenshots as separate files.
            if self.trace_file is not None:
                trace_events = msg['params']['value']
                for _, trace_event in enumerate(trace_events):
                    is_screenshot = False
                    if self.video_prefix is not None and 'cat' in trace_event and \
                            'name' in trace_event and 'ts' in trace_event:
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

