# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for browsers that speak the dev tools protocol"""
import gzip
import logging
import os
import monotonic
import ujson as json
import constants

class DevtoolsBrowser(object):
    """Devtools Browser base"""
    def __init__(self, job):
        self.devtools_job = job
        self.devtools = None
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')

    def connect(self, task):
        """Connect to the dev tools interface"""
        ret = False
        from internal.devtools import DevTools
        self.devtools = DevTools(self.devtools_job, task)
        if self.devtools.connect(constants.START_BROWSER_TIME_LIMIT):
            logging.debug("Devtools connected")
            ret = True
        else:
            task['error'] = "Error connecting to dev tools interface"
            logging.critical(task['error'])
            self.devtools = None
        return ret

    def disconnect(self):
        """Disconnect from dev tools"""
        if self.devtools is not None:
            self.devtools.close()

    def run_task(self, task):
        """Run an individual test"""
        if self.devtools is not None:
            logging.debug("Devtools connected")
            end_time = monotonic.monotonic() + task['time_limit']
            while len(task['script']) and monotonic.monotonic() < end_time:
                command = task['script'].pop(0)
                if command['record']:
                    self.devtools.start_recording()
                self.process_command(command)
                if command['record']:
                    self.devtools.wait_for_page_load()
                    self.devtools.stop_recording()
                    if self.devtools_job['pngss']:
                        screen_shot = os.path.join(task['dir'], task['prefix'] + 'screen.png')
                        self.devtools.grab_screenshot(screen_shot, png=True)
                    else:
                        screen_shot = os.path.join(task['dir'], task['prefix'] + 'screen.jpg')
                        self.devtools.grab_screenshot(screen_shot, png=False)
                    self.collect_browser_metrics(task)

    def run_js_file(self, file_name):
        """Execute one of our js scripts"""
        ret = None
        script = None
        script_file_path = os.path.join(self.script_dir, file_name)
        if os.path.isfile(script_file_path):
            with open(script_file_path, 'rb') as script_file:
                script = script_file.read()
        if script is not None:
            ret = self.devtools.execute_js(script)
        return ret

    def collect_browser_metrics(self, task):
        """Collect all of the in-page browser metrics that we need"""
        user_timing = self.run_js_file('user_timing.js')
        if user_timing is not None:
            path = os.path.join(task['dir'], task['prefix'] + 'timed_events.json.gz')
            with gzip.open(path, 'wb') as outfile:
                outfile.write(json.dumps(user_timing))
        page_data = self.run_js_file('page_data.js')
        if page_data is not None:
            path = os.path.join(task['dir'], task['prefix'] + 'page_data.json.gz')
            with gzip.open(path, 'wb') as outfile:
                outfile.write(json.dumps(page_data))
        if 'customMetrics' in self.devtools_job:
            custom_metrics = {}
            for name in self.devtools_job['customMetrics']:
                script = 'var wptCustomMetric = function() {' +\
                         self.devtools_job['customMetrics'][name] +\
                         '};try{wptCustomMetric();}catch(e){};'
                custom_metrics[name] = self.devtools.execute_js(script)
            path = os.path.join(task['dir'], task['prefix'] + 'metrics.json.gz')
            with gzip.open(path, 'wb') as outfile:
                outfile.write(json.dumps(custom_metrics))

    def process_command(self, command):
        """Process an individual script command"""
        if command['command'] == 'navigate':
            self.devtools.send_command('Page.navigate', {'url': command['target']})
