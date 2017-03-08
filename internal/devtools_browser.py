# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for browsers that speak the dev tools protocol"""
import gzip
import logging
import os
import subprocess
import threading
import monotonic
import ujson as json
import constants

class DevtoolsBrowser(object):
    """Devtools Browser base"""
    def __init__(self, job):
        self.job = job
        self.devtools = None
        self.task = None
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support')
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')

    def connect(self, task):
        """Connect to the dev tools interface"""
        ret = False
        from internal.devtools import DevTools
        self.devtools = DevTools(self.job, task)
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
            self.devtools = None

    def prepare_browser(self):
        """Prepare the running browser (mobile emulation, UA string, etc"""
        if self.devtools is not None:
            # Mobile Emulation
            if 'mobile' in self.job and self.job['mobile'] and \
                    'width' in self.job and 'height' in self.job and \
                    'dpr' in self.job:
                self.devtools.send_command("Emulation.setDeviceMetricsOverride",
                                           {"width": int(self.job['width']),
                                            "height": int(self.job['height']),
                                            "screenWidth": int(self.job['width']),
                                            "screenHeight": int(self.job['height']),
                                            "positionX": 0,
                                            "positionY": 0,
                                            "deviceScaleFactor": float(self.job['dpr']),
                                            "mobile": True, "fitWindow": True},
                                           wait=True)
                self.devtools.send_command("Emulation.setVisibleSize",
                                           {"width": int(self.job['width']),
                                            "height": int(self.job['height'])},
                                           wait=True)
            # UA String
            if 'uastring' in self.job:
                ua_string = self.job['uastring']
            else:
                ua_string = self.devtools.execute_js("navigator.userAgent")
            if ua_string is not None and 'keepua' not in self.job or not self.job['keepua']:
                ua_string += ' PTST/{0:d}'.format(constants.CURRENT_VERSION)
            if ua_string is not None:
                self.devtools.send_command('Network.setUserAgentOverride',
                                           {'userAgent': ua_string},
                                           wait=True)

    def on_start_recording(self, _):
        """Start recording"""
        self.devtools.start_recording()

    def on_stop_recording(self, _):
        """Stop recording"""
        self.devtools.stop_recording()

    def run_task(self, task):
        """Run an individual test"""
        if self.devtools is not None:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic.monotonic() + task['time_limit']
            task['current_step'] = 1
            recording = False
            while len(task['script']) and monotonic.monotonic() < end_time:
                self.prepare_task(task)
                command = task['script'].pop(0)
                if not recording and command['record']:
                    recording = True
                    self.on_start_recording(task)
                self.process_command(command)
                if command['record']:
                    self.devtools.wait_for_page_load()
                    if not task['combine_steps'] or not len(task['script']):
                        self.on_stop_recording(task)
                        recording = False
                        if task['log_data']:
                            if self.job['pngss']:
                                screen_shot = os.path.join(task['dir'],
                                                           task['prefix'] + 'screen.png')
                                self.devtools.grab_screenshot(screen_shot, png=True)
                            else:
                                screen_shot = os.path.join(task['dir'],
                                                           task['prefix'] + 'screen.jpg')
                                self.devtools.grab_screenshot(screen_shot, png=False)
                            self.collect_browser_metrics(task)
                            # Post-process each step separately
                            trace_thread = threading.Thread(target=self.process_trace)
                            trace_thread.start()
                            self.process_video()
                            trace_thread.join()
                            # Move on to the next step
                            task['current_step'] += 1
            self.task = None

    def prepare_task(self, task):
        """Format the file prefixes for multi-step testing"""
        if task['current_step'] == 1:
            task['prefix'] = task['task_prefix']
            task['video_subdirectory'] = task['task_video_prefix']
        else:
            task['prefix'] = '{0}{1:d}_'.format(task['task_prefix'], task['current_step'])
            task['video_subdirectory'] = '{0}_{1:d}'.format(task['task_video_prefix'],
                                                            task['current_step'])
        if task['video_subdirectory'] not in task['video_directories']:
            task['video_directories'].append(task['video_subdirectory'])

    def process_video(self):
        """Post process the video"""
        from internal.video_processing import VideoProcessing
        video = VideoProcessing(self.job, self.task)
        video.process()

    def process_trace(self):
        """Post-process the trace file"""
        path_base = os.path.join(self.task['dir'], self.task['prefix'])
        trace_file = path_base + 'trace.json.gz'
        if os.path.isfile(trace_file):
            user_timing = path_base + 'user_timing.json.gz'
            cpu_slices = path_base + 'timeline_cpu.json.gz'
            script_timing = path_base + 'script_timing.json.gz'
            feature_usage = path_base + 'feature_usage.json.gz'
            interactive = path_base + 'interactive.json.gz'
            v8_stats = path_base + 'v8stats.json.gz'
            trace_parser = os.path.join(self.support_path, "trace-parser.py")
            cmd = ['python', trace_parser, '-t', trace_file, '-u', user_timing,
                   '-c', cpu_slices, '-j', script_timing, '-f', feature_usage,
                   '-i', interactive, '-s', v8_stats]
            logging.debug(cmd)
            subprocess.call(cmd)

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
        if 'customMetrics' in self.job:
            custom_metrics = {}
            for name in self.job['customMetrics']:
                script = 'var wptCustomMetric = function() {' +\
                         self.job['customMetrics'][name] +\
                         '};try{wptCustomMetric();}catch(e){};'
                custom_metrics[name] = self.devtools.execute_js(script)
            path = os.path.join(task['dir'], task['prefix'] + 'metrics.json.gz')
            with gzip.open(path, 'wb') as outfile:
                outfile.write(json.dumps(custom_metrics))

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.devtools.start_navigating()
            self.devtools.send_command('Page.navigate', {'url': command['target']})
        elif command['command'] == 'logdata':
            self.task['combine_steps'] = False
            if int(command['target']):
                logging.debug("Data logging enabled")
                self.task['log_data'] = True
            else:
                logging.debug("Data logging disabled")
                self.task['log_data'] = False
        elif command['command'] == 'combinesteps':
            self.task['log_data'] = True
            self.task['combine_steps'] = True

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.devtools is not None:
            self.devtools.send_command('Page.navigate', {'url': url}, wait=True)
