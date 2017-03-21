# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for browsers that speak the dev tools protocol"""
import gzip
import logging
import os
import subprocess
import time
import threading
import monotonic
import ujson as json
from .optimization_checks import OptimizationChecks

class DevtoolsBrowser(object):
    """Devtools Browser base"""
    CONNECT_TIME_LIMIT = 30
    CURRENT_VERSION = 1

    def __init__(self, job, use_devtools_video=True):
        self.job = job
        self.devtools = None
        self.task = None
        self.event_name = None
        self.use_devtools_video = use_devtools_video
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support')
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')

    def connect(self, task):
        """Connect to the dev tools interface"""
        ret = False
        from internal.devtools import DevTools
        self.devtools = DevTools(self.job, task, self.use_devtools_video)
        if self.devtools.connect(self.CONNECT_TIME_LIMIT):
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

    def prepare_browser(self, task):
        """Prepare the running browser (mobile emulation, UA string, etc"""
        if self.devtools is not None:
            # Clear the caches
            if not task['cached']:
                self.devtools.send_command("Network.clearBrowserCache", {},
                                           wait=True)
                self.devtools.send_command("Network.clearBrowserCookies", {},
                                           wait=True)

            # Mobile Emulation
            if 'mobile' in self.job and self.job['mobile'] and \
                    'width' in self.job and 'height' in self.job and \
                    'dpr' in self.job:
                self.devtools.send_command("Emulation.setDeviceMetricsOverride",
                                           {"width": int(self.job['width']),
                                            "height": int(self.job['height']),
                                            "screenWidth": int(self.job['width']),
                                            "screenHeight": int(self.job['height']),
                                            "scale": 1,
                                            "positionX": 0,
                                            "positionY": 0,
                                            "deviceScaleFactor": float(self.job['dpr']),
                                            "mobile": True,
                                            "fitWindow": False},
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
                ua_string += ' PTST/{0:d}'.format(self.CURRENT_VERSION)
            if ua_string is not None:
                self.job['user_agent_string'] = ua_string
            # Disable js
            if self.job['noscript']:
                self.devtools.send_command("Emulation.setScriptExecutionDisabled",
                                           {"value": True}, wait=True)

    def on_start_recording(self, _):
        """Start recording"""
        if self.devtools is not None:
            self.devtools.start_recording()

    def on_stop_recording(self, _):
        """Stop recording"""
        if self.devtools is not None:
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
                            # Start the processing that can run in a background thread
                            optimization = OptimizationChecks(self.job, task, self.get_requests())
                            optimization.start()
                            trace_thread = threading.Thread(target=self.process_trace)
                            trace_thread.start()
                            # Collect end of test data from the browser
                            if self.job['pngss']:
                                screen_shot = os.path.join(task['dir'],
                                                           task['prefix'] + '_screen.png')
                                self.devtools.grab_screenshot(screen_shot, png=True)
                            else:
                                screen_shot = os.path.join(task['dir'],
                                                           task['prefix'] + '_screen.jpg')
                                self.devtools.grab_screenshot(screen_shot, png=False)
                            self.collect_browser_metrics(task)
                            # Run the rest of the post-processing
                            self.process_video()
                            logging.debug('Waiting for trace processing to complete')
                            trace_thread.join()
                            optimization.join()
                            # Move on to the next step
                            task['current_step'] += 1
                            self.event_name = None
                        self.wait_for_processing()
            self.task = None

    def prepare_task(self, task):
        """Format the file prefixes for multi-step testing"""
        task['page_data'] = {}
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

    def process_video(self):
        """Post process the video"""
        from internal.video_processing import VideoProcessing
        video = VideoProcessing(self.job, self.task)
        video.process()

    def process_trace(self):
        """Post-process the trace file"""
        path_base = os.path.join(self.task['dir'], self.task['prefix'])
        trace_file = path_base + '_trace.json.gz'
        if os.path.isfile(trace_file):
            user_timing = path_base + '_user_timing.json.gz'
            cpu_slices = path_base + '_timeline_cpu.json.gz'
            script_timing = path_base + '_script_timing.json.gz'
            feature_usage = path_base + '_feature_usage.json.gz'
            interactive = path_base + '_interactive.json.gz'
            netlog = path_base + '_netlog_requests.json.gz'
            v8_stats = path_base + '_v8stats.json.gz'
            trace_parser = os.path.join(self.support_path, "trace-parser.py")
            cmd = ['python', trace_parser, '-t', trace_file, '-u', user_timing,
                   '-c', cpu_slices, '-j', script_timing, '-f', feature_usage,
                   '-i', interactive, '-n', netlog, '-s', v8_stats]
            logging.debug(cmd)
            subprocess.call(cmd)
            # delete the trace file if it wasn't requested
            trace_enabled = bool('trace' in self.job and self.job['trace'])
            timeline_enabled = bool('timeline' in self.job and self.job['timeline'])
            if not trace_enabled and not timeline_enabled:
                try:
                    os.remove(trace_file)
                except Exception:
                    pass

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
            path = os.path.join(task['dir'], task['prefix'] + '_timed_events.json.gz')
            with gzip.open(path, 'wb') as outfile:
                outfile.write(json.dumps(user_timing))
        page_data = self.run_js_file('page_data.js')
        if page_data is not None:
            task['page_data'].update(page_data)
        if 'customMetrics' in self.job:
            custom_metrics = {}
            for name in self.job['customMetrics']:
                script = 'var wptCustomMetric = function() {' +\
                         self.job['customMetrics'][name] +\
                         '};try{wptCustomMetric();}catch(e){};'
                custom_metrics[name] = self.devtools.execute_js(script)
            path = os.path.join(task['dir'], task['prefix'] + '_metrics.json.gz')
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
        elif command['command'] == 'seteventname':
            self.event_name = command['target']
        elif command['command'] == 'exec':
            if command['record']:
                self.devtools.start_navigating()
            self.devtools.execute_js(command['target'])
        elif command['command'] == 'sleep':
            delay = min(60, max(0, int(command['target'])))
            if delay > 0:
                time.sleep(delay)
        elif command['command'] == 'setabm':
            self.task['stop_at_onload'] = bool('target' in command and int(command['target']) == 0)
        elif command['command'] == 'setactivitytimeout':
            if 'target' in command:
                self.task['activity_time'] = max(0, min(30, int(command['target'])))
        elif command['command'] == 'block':
            block_list = command['target'].split()
            for block in block_list:
                block = block.strip()
                if len(block):
                    logging.debug("Blocking: %s", block)
                    self.devtools.send_command('Network.addBlockedURL', {'url': block})
        elif command['command'] == 'setuseragent':
            self.task['user_agent_string'] = command['target']
        elif command['command'] == 'setcookie':
            if 'target' in command and 'value' in command:
                url = command['target'].strip()
                cookie = command['value']
                pos = cookie.find(';')
                if pos > 0:
                    cookie = cookie[:pos]
                pos = cookie.find('=')
                if pos > 0:
                    name = cookie[:pos].strip()
                    value = cookie[pos+1:].strip()
                    if len(name) and len(value) and len(url):
                        self.devtools.send_command('Network.setCookie',
                                                   {'url': url, 'name': name, 'value': value})

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.devtools is not None:
            self.devtools.send_command('Page.navigate', {'url': url}, wait=True)

    def get_requests(self):
        """Get the request details for running an optimization check"""
        requests = None
        if self.devtools is not None:
            requests = self.devtools.get_requests()
        return requests
