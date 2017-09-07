# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Support for Safari on iOS using iWptBrowser"""
from datetime import datetime
import gzip
import logging
import os
import Queue
import re
import subprocess
import time
import monotonic
import ujson as json

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
        self.page = {}
        self.requests = {}
        self.last_activity = monotonic.monotonic()
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')

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
        self.flush_messages()
        self.connected = self.ios.start_browser()

    def stop(self, job, task):
        """Kill the browser"""
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
                command = task['script'].pop(0)
                if not recording and command['record']:
                    recording = True
                    self.prepare_task(task)
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
        logging.debug(message)
        if self.recording:
            if 'msg' in message and message['msg'].startswith('page.'):
                self.last_activity = monotonic.monotonic()
                if message['msg'] == 'page.didFinish' or message['msg'] == 'page.loadingFinished':
                    self.page_loaded = monotonic.monotonic()

    def prepare_task(self, task):
        """Format the file prefixes for multi-step testing"""
        self.page = {}
        self.requests = {}
        task['page_data'] = {}
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
        if self.connected:
            if self.job['pngScreenShot']:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.png')
                self.grab_screenshot(screen_shot, png=True)
            else:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
                self.grab_screenshot(screen_shot, png=False, resize=600)
        # Collect end of test data from the browser
        self.collect_browser_metrics(task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        pass

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        pass

    def step_complete(self, task):
        """Final step processing"""
        pass

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
            self.ios.execute_js(command['target'])
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
