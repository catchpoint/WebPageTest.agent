# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Microsoft Edge testing"""
from datetime import datetime
import glob
import gzip
import logging
import os
import re
import shutil
import subprocess
import time
import monotonic
import ujson as json
from .desktop_browser import DesktopBrowser
from .etw import ETW

class Edge(DesktopBrowser):
    """Microsoft Edge"""
    def __init__(self, path, options, job):
        DesktopBrowser.__init__(self, path, options, job)
        self.job = job
        self.task = None
        self.options = options
        self.path = path
        self.event_name = None
        self.etw = None
        self.etw_log = None
        self.driver = None
        self.nav_error = None
        self.page_loaded = None
        self.recording = False
        self.last_activity = monotonic.monotonic()
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        self.etw_log = os.path.join(task['dir'], 'etw.log')
        task['need_orange'] = True
        self.kill()
        if not task['cached']:
            self.clear_cache()
        DesktopBrowser.prepare(self, job, task)

    def launch(self, _job, task):
        """Launch the browser"""
        try:
            from selenium import webdriver
            logging.debug('Launching Edge')
            self.driver = webdriver.Edge(executable_path=self.path)
            self.driver.set_page_load_timeout(task['time_limit'])
            self.driver.get('about:blank')
            logging.debug('Resizing browser to %dx%d', task['width'], task['height'])
            self.driver.set_window_position(0, 0)
            self.driver.set_window_size(task['width'], task['height'])
            DesktopBrowser.wait_for_idle(self)
        except Exception as err:
            task['error'] = 'Error starting Firefox: {0}'.format(err.__str__())

    def stop(self, job, task):
        """Kill the browser"""
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        DesktopBrowser.stop(self, job, task)
        self.kill()
        self.delete_logs()

    def kill(self):
        """Kill any running instances"""
        processes = ['MicrosoftEdge.exe', 'MicrosoftEdgeCP.exe', 'plugin-container.exe',
                     'browser_broker.exe', 'smartscreen.exe']
        for exe in processes:
            subprocess.call(['taskkill', '/F', '/T', '/IM', exe])

    def clear_cache(self):
        """Clear the browser cache"""
        local_app_data = os.getenv('LOCALAPPDATA')
        edge_root = os.path.join(local_app_data, 'Packages',
                                 'Microsoft.MicrosoftEdge_8wekyb3d8bbwe')
        directories = ['AC', 'AppData']
        for directory in directories:
            path = os.path.join(edge_root, directory)
            try:
                shutil.rmtree(path)
            except Exception:
                pass

    def delete_logs(self):
        """Delete the ETW logs"""
        if self.etw_log is not None:
            files = sorted(glob.glob(self.etw_log + '*'))
            for path in files:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def run_task(self, task):
        """Run an individual test"""
        if self.driver is not None:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic.monotonic() + task['time_limit']
            task['current_step'] = 1
            recording = False
            while len(task['script']) and task['error'] is None and \
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
            # Always navigate to about:blank after finishing in case the tab is
            # remembered across sessions
            try:
                self.driver.get('about:blank')
            except Exception:
                logging.debug('Webdriver exception navigating to about:blank after the test')
            self.task = None

    def wait_for_page_load(self):
        """Wait for the page to finish loading"""
        # For right now, just wait for 2 seconds since webdriver returns when loaded.
        # TODO: switch to waiting for network idle
        time.sleep(2)

    def execute_js(self, script):
        """Run javascipt"""
        ret = None
        if self.driver is not None:
            try:
                self.driver.set_script_timeout(30)
                ret = self.driver.execute_script(script)
            except Exception:
                pass
        return ret

    def run_js_file(self, file_name):
        """Execute one of our js scripts"""
        ret = None
        script = None
        script_file_path = os.path.join(self.script_dir, file_name)
        if os.path.isfile(script_file_path):
            with open(script_file_path, 'rb') as script_file:
                script = script_file.read()
        if script is not None:
            try:
                self.driver.set_script_timeout(30)
                ret = self.driver.execute_script(script)
            except Exception:
                pass
            if ret is not None:
                logging.debug(ret)
        return ret

    def collect_browser_metrics(self, task):
        """Collect all of the in-page browser metrics that we need"""
        logging.debug("Collecting user timing metrics")
        user_timing = self.run_js_file('user_timing.js')
        if user_timing is not None:
            path = os.path.join(task['dir'], task['prefix'] + '_timed_events.json.gz')
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json.dumps(user_timing))
        logging.debug("Collecting page-level metrics")
        page_data = self.run_js_file('page_data.js')
        if page_data is not None:
            task['page_data'].update(page_data)
        if 'customMetrics' in self.job:
            self.driver.set_script_timeout(30)
            custom_metrics = {}
            for name in self.job['customMetrics']:
                logging.debug("Collecting custom metric %s", name)
                script = 'var wptCustomMetric = function() {' +\
                         self.job['customMetrics'][name] +\
                         '};try{return wptCustomMetric();}catch(e){};'
                try:
                    custom_metrics[name] = self.driver.execute_script(script)
                    if custom_metrics[name] is not None:
                        logging.debug(custom_metrics[name])
                except Exception:
                    pass
            path = os.path.join(task['dir'], task['prefix'] + '_metrics.json.gz')
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json.dumps(custom_metrics))

    def prepare_task(self, task):
        """Format the file prefixes for multi-step testing"""
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
        # Mark the start point in the various log files
        self.delete_logs()
        self.recording = True
        self.etw = ETW()
        self.etw.start_recording(self.etw_log)
        now = monotonic.monotonic()
        if not self.task['stop_at_onload']:
            self.last_activity = now
        if self.page_loaded is not None:
            self.page_loaded = now
        DesktopBrowser.on_start_recording(self, task)
        logging.debug('Starting measurement')
        task['start_time'] = datetime.utcnow()

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        self.recording = False
        DesktopBrowser.on_stop_recording(self, task)
        if self.job['pngScreenShot']:
            screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.png')
            self.grab_screenshot(screen_shot, png=True)
        else:
            screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
            self.grab_screenshot(screen_shot, png=False, resize=600)
        if self.etw is not None:
            self.etw.stop_recording()
        # Collect end of test data from the browser
        self.collect_browser_metrics(task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        DesktopBrowser.on_start_processing(self, task)
        if self.etw is not None:
            requests = self.etw.process(task)
            if requests is not None:
                requests_file = os.path.join(task['dir'], task['prefix'] + '_requests.json.gz')
                with gzip.open(requests_file, 'wb', 7) as f_out:
                    json.dump(requests, f_out)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DesktopBrowser.wait_for_processing(self, task)

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.task['url'] = command['target']
            self.driver.get(command['target'])
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
            self.driver.set_script_timeout(30)
            self.driver.execute_script(command['target'])
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
                        self.driver.add_cookie({'url': url, 'name': name, 'value': value})

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.driver is not None:
            try:
                self.driver.get(url)
            except Exception as err:
                logging.debug("Error navigating Edge: %s", str(err))

    def grab_screenshot(self, path, png=True, resize=0):
        """Save the screen shot (png or jpeg)"""
        if self.driver is not None:
            try:
                data = self.driver.get_screenshot_as_png()
                if data is not None:
                    resize_string = '' if not resize else '-resize {0:d}x{0:d} '.format(resize)
                    if png:
                        with open(path, 'wb') as image_file:
                            image_file.write(data)
                        if len(resize_string):
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
            except Exception as err:
                logging.debug('Exception grabbing screen shot: %s', str(err))
