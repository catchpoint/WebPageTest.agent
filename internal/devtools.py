# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for interfacing with Chrome's remote debugging protocol"""
import json
import logging
import time

class DevTools(object):
    """Interface into Chrome's remote dev tools protocol"""
    def __init__(self, task):
        self.url = "http://localhost:{0:d}/json".format(task['port'])
        self.websocket = None
        self.task = task
        self.command_id = 0

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
                                self.send_command('Page.enable', {})
                                self.send_command('Network.enable', {})
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
        pass

    def send_command(self, method, params):
        """Send a raw dev tools message"""
        if self.websocket:
            self.command_id += 1
            msg = {'id': self.command_id, 'method': method, 'params': params}
            try:
                self.websocket.send(json.dumps(msg))
            except BaseException as err:
                logging.critical("Websocket send error: %s", err.__str__)

    def wait_for_page_load(self):
        """Wait for the page load and activity to finish"""
        end_time = time.clock() + self.task['time_limit']
        self.page_loaded = False
        while not self.page_loaded and time.clock() < end_time:
            try:
                raw = self.websocket.recv()
                if raw is not None and len(raw):
                    logging.debug(raw)
                    msg = json.loads(raw)
                    if 'method' in msg:
                        self.process_message(msg)
            except BaseException as _:
                # ignore timeouts when we're in a polling read loop
                pass

    def process_message(self, msg):
        """Process an inbound dev tools message"""
        if msg['method'] == 'Page.loadEventFired':
            self.page_loaded = True
