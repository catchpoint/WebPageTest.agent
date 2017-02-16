# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for interfacing with Chrome's remote debugging protocol"""
import base64
import gzip
import json
import logging
import os
import subprocess
import time

class DevTools(object):
    """Interface into Chrome's remote dev tools protocol"""
    def __init__(self, job, task):
        self.url = "http://localhost:{0:d}/json".format(task['port'])
        self.path_base = os.path.join(task['dir'], task['prefix'])
        self.websocket = None
        self.job = job
        self.task = task
        self.command_id = 0
        self.page_loaded = False
        self.main_frame = None
        self.is_navigating = False
        self.last_activity = time.clock()
        self.error = None
        self.dev_tools_file = None
        self.trace_file = None

    def connect(self, timeout):
        """Connect to the browser"""
        import requests
        ret = False
        end_time = time.clock() + timeout
        while not ret and time.clock() < end_time:
            try:
                response = requests.get(self.url, timeout=timeout)
                if len(response.text):
                    tabs = response.json()
                    logging.debug("Dev Tools tabs: %s", json.dumps(tabs))
                    if len(tabs):
                        websocket_url = None
                        for index in range(0, len(tabs)):
                            if 'type' in tabs[index] and \
                                    tabs[index]['type'] == 'page' and \
                                    'webSocketDebuggerUrl' in tabs[index]:
                                websocket_url = tabs[index]['webSocketDebuggerUrl']
                                break
                        if websocket_url is not None:
                            from websocket import create_connection
                            self.websocket = create_connection(websocket_url)
                            if self.websocket:
                                self.websocket.settimeout(1)
                                ret = True
                        else:
                            time.sleep(1)
            except BaseException as err:
                logging.critical("Connect to dev tools Error: %s", err.__str__)
        return ret

    def close(self):
        """Close the dev tools connection"""
        if self.websocket:
            self.websocket.close()
            self.websocket = None

    def start_recording(self):
        """Start capturing dev tools, timeline and trace data"""
        self.page_loaded = False
        self.is_navigating = True
        self.error = None
        self.flush_pending_messages()
        self.send_command('Page.enable', {})
        self.send_command('Network.enable', {})
        if 'web10' not in self.task or not self.task['web10']:
            self.last_activity = time.clock()

    def stop_recording(self):
        """Stop capturing dev tools, timeline and trace data"""
        self.send_command('Page.disable', {})
        self.send_command('Network.disable', {})
        if self.dev_tools_file is not None:
            self.dev_tools_file.write("\n]")
            self.dev_tools_file.close()
            self.dev_tools_file = None
        self.flush_pending_messages()

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

    def send_command(self, method, params, wait=False):
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
                    end_time = time.clock() + 30
                    while ret is None and time.clock() < end_time:
                        try:
                            raw = self.websocket.recv()
                            if raw is not None and len(raw):
                                logging.debug(raw[:1000])
                                msg = json.loads(raw)
                                if 'id' in msg and int(msg['id']) == msg['id']:
                                    ret = msg
                        except BaseException as _:
                            pass
            except BaseException as err:
                logging.critical("Websocket send error: %s", err.__str__)
        return ret

    def wait_for_page_load(self):
        """Wait for the page load and activity to finish"""
        if self.websocket:
            self.websocket.settimeout(1)
            now = time.clock()
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
                now = time.clock()
                elapsed_activity = now - self.last_activity
                if self.page_loaded and elapsed_activity >= 2:
                    done = True
                elif self.error is not None:
                    done = True
                elif now >= end_time:
                    done = True
                    self.error = "Timeout"
        return self.error

    def grab_screenshot(self):
        """Save the screen shot (png or jpeg)"""
        path = self.path_base + 'screen.png'
        response = self.send_command("Page.captureScreenshot", {}, True)
        if response is not None and 'result' in response and 'data' in response['result']:
            with open(path, 'wb') as image_file:
                image_file.write(base64.b64decode(response['result']['data']))
            if not self.job['pngss']:
                jpeg = self.path_base + 'screen.jpg'
                args = ['convert', '-scale', '50%', '-quality', str(self.job['iq']), path, jpeg]
                subprocess.call(args, shell=True)
                if os.path.isfile(jpeg):
                    os.remove(path)

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
                self.process_network_event()
                self.log_dev_tools_event(msg)
            elif category == 'Inspector':
                self.process_inspector_event(event)
            elif category == 'Tracing':
                self.process_trace_event(event, msg)
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
                self.last_activity = time.clock()
                self.page_loaded = False
        elif event == 'javascriptDialogOpening':
            self.error = "Page opened a modal dailog"

    def process_network_event(self):
        """Process Network.* dev tools events"""
        if 'web10' not in self.task or not self.task['web10']:
            logging.debug('Activity detected')
            self.last_activity = time.clock()

    def process_inspector_event(self, event):
        """Process Inspector.* dev tools events"""
        if event == 'detached':
            self.error = 'Inspector detached, possibly crashed.'
        elif event == 'targetCrashed':
            self.error = 'Browser crashed.'

    def process_trace_event(self, event, msg):
        """Process Tracing.* dev tools events"""

    def log_dev_tools_event(self, msg):
        """Log the dev tools events to a file"""
        if self.dev_tools_file is None:
            path = self.path_base + 'devtools.json.gz'
            self.dev_tools_file = gzip.open(path, 'wb')
            self.dev_tools_file.write("[{}")
        if self.dev_tools_file is not None:
            self.dev_tools_file.write(",\n")
            self.dev_tools_file.write(json.dumps(msg))
