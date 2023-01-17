# Copyright 2022 WebPageTest LLC.
# Copyright 2022 Google Inc.
# Copyright 2022 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Support for Safari Desktop using Webdriver and no extensions"""
from datetime import datetime, timedelta
import gzip
import logging
import multiprocessing
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from time import monotonic
from urllib.parse import urlsplit # pylint: disable=import-error
try:
    import ujson as json
except BaseException:
    import json
from .desktop_browser import DesktopBrowser


class SafariDesktop(DesktopBrowser):
    """SafariDesktop"""
    def __init__(self, path, options, job):
        DesktopBrowser.__init__(self, path, options, job)
        self.job = job
        self.task = None
        self.options = options
        self.path = path
        self.event_name = None
        self.driver = None
        self.nav_error = None
        self.page_loaded = None
        self.recording = False
        self.browser_version = None
        self.wait_interval = 5.0
        self.wait_for_script = None
        self.must_exit_now = False
        self.page = {}
        self.requests = {}
        self.connections = {}
        self.request_count = 0
        self.total_sleep = 0
        self.long_tasks = []
        self.last_activity = monotonic()
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')
        self.start_page = 'http://127.0.0.1:8888/orange.html'
        self.safari_log = None
        self.safari_log_thread = None
        self.stop_safari_log = False
        self.events = multiprocessing.JoinableQueue()
        self.re_content_resource = re.compile(r'^[^\[]*\[[^\]]*frameID=(?P<frame>\d+)[^\]]*resourceID=(?P<resource>\d+)[^\]]*\] (?P<msg>.*)$')
        self.re_net_task = re.compile(r'Task (?P<task><[^>]+>\.<[^>]+>)')
        self.re_net_connection = re.compile(r'Connection (?P<connection>\d+)')
        self.re_connection = re.compile(r' \[C(?P<connection>\d+)')
        self.re_parens = re.compile(r'\(([^\)]+)\)')
        self.re_curley = re.compile(r'\{([^\}]+)\}')
        self.tasks = {}
        self.start_time = None

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        self.page = {}
        self.requests = {}
        self.request_count = 0
        self.start_time = None
        # Manually kill Safari gracefully here because DesktopBrowser will try a SIGTERM which doesn't work
        if self.path is not None:
            try:
                subprocess.call(['killall', os.path.basename(self.path)])
            except OSError:
                pass
            except Exception:
                logging.exception("Exception preparing Safari")
        DesktopBrowser.prepare(self, job, task)

    def start_safari(self, job, task):
        """Start Safari using WebDriver"""
        if self.must_exit:
            return
        # Start logging the browser output
        args = ['log',
                'stream',
                '--level', 'debug',
                '--style', 'json',
                '--process', 'Safari',
                '--process', 'com.apple.WebKit.WebContent',
                '--process', 'com.apple.WebKit.Networking',
                ]
        self.safari_log = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.stop_safari_log = False
        self.safari_log_thread = threading.Thread(target=self.pump_safari_log_output)
        self.safari_log_thread.start()
        # Start the browser
        from selenium import webdriver # pylint: disable=import-error
        self.driver = webdriver.Safari()
        self.driver.set_page_load_timeout(task['time_limit'])
        if 'browserVersion' in self.driver.capabilities:
            self.browser_version = self.driver.capabilities['browserVersion']
        elif 'version' in self.driver.capabilities:
            self.browser_version = self.driver.capabilities['version']
        DesktopBrowser.wait_for_idle(self)
        self.driver.get(self.start_page)
        logging.debug('Resizing browser to %dx%d', task['width'], task['height'])
        self.driver.set_window_position(0, 0)
        self.driver.set_window_size(task['width'], task['height'])
        time.sleep(0.5)

    def launch(self, job, task):
        """Launch the browser"""
        if self.must_exit:
            return
        try:
            self.start_safari(job, task)
            if self.driver:
                # Figure out the native viewport size
                size = self.execute_js("return [window.innerWidth, window.innerHeight]")
                logging.debug(size)
                if size is not None and len(size) == 2:
                    task['actual_viewport'] = {"width": size[0], "height": size[1]}
                    if 'adjust_viewport' in job and job['adjust_viewport']:
                        delta_x = max(task['width'] - size[0], 0)
                        delta_y = max(task['height'] - size[1], 0)
                        if delta_x or delta_y:
                            width = task['width'] + delta_x
                            height = task['height'] + delta_y
                            logging.debug('Resizing browser to %dx%d', width, height)
                            self.set_window_size(width, height)
                # Wait for the browser startup to finish
                DesktopBrowser.wait_for_idle(self)
                self.wait_for_events_idle()
        except Exception as err:
            logging.exception("Error starting Safari")
            task['error'] = 'Error starting Safari: {0}'.format(err.__str__())

    def set_window_size(self, width, height):
        """Position the window"""
        self.driver.set_window_size(width, height)

    def disconnect_driver(self):
        """Disconnect WebDriver"""
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                logging.exception('Error quitting WebDriver')
            self.driver = None

    def close_browser(self, job, task):
        """Terminate the browser but don't do all of the cleanup that stop does"""
        self.disconnect_driver()
        subprocess.call(['killall', 'Safari'])
        DesktopBrowser.close_browser(self, job, task)
        # make SURE the Safari processes are gone
        subprocess.call(['killall', '-9', 'Safari'])

    def stop(self, job, task):
        """Kill the browser"""
        self.close_browser(job, task)
        DesktopBrowser.stop(self, job, task)
        # Stop the logging
        try:
            import signal
            if self.safari_log is not None:
                self.stop_safari_log = True
                self.safari_log.send_signal(signal.SIGINT)
                self.safari_log.wait(2)
                subprocess.call(['killall', 'log'])
                self.safari_log = None
        except Exception:
            logging.exception('Error terminating logging')
        if self.safari_log_thread is not None:
            try:
                self.safari_log_thread.join(10)
            except Exception:
                logging.exception('Error waiting for safari log output')
            self.safari_log_thread = None

    def run_lighthouse_test(self, task):
        """Stub for lighthouse test"""
        pass

    def run_task(self, task):
        """Run an individual test"""
        if self.driver:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic() + task['test_time_limit']
            task['current_step'] = 1
            recording = False
            while len(task['script']) and task['error'] is None and monotonic() < end_time and not self.must_exit:
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

    def alert_size(self, _alert_config, _task_dir, _prefix):
        '''Checks the agents file size and alert on certain percentage over avg byte size'''             
        self.alert_desktop_results(_alert_config, 'Safari', _task_dir, _prefix)

    def wait_for_page_load(self):
        """Wait for the onload event from the extension"""
        if self.driver:
            logging.debug('Waiting for page load')
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
                    event = self.get_event(interval)
                    self.process_event(event)
                except Exception:
                    # ignore timeouts when we're in a polling read loop
                    pass
                now = monotonic()
                elapsed_test = now - start_time
                if 'minimumTestSeconds' in self.task and \
                        elapsed_test < self.task['minimumTestSeconds'] and \
                        now < end_time:
                    continue
                if self.nav_error is not None:
                    logging.debug('Navigation error')
                    done = True
                    if self.page_loaded is None or 'minimumTestSeconds' in self.task:
                        logging.debug('Page not loaded')
                        self.task['error'] = self.nav_error
                        self.task['page_data']['result'] = 12999
                    else:
                        logging.debug('Page loaded')
                elif now >= end_time:
                    done = True
                    # only consider it an error if we didn't get a page load event
                    if self.page_loaded is None:
                        self.task['error'] = "Page Load Timeout"
                        self.task['soft_error'] = True
                        self.task['page_data']['result'] = 99998
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
                        ret = self.execute_js('return (' + self.wait_for_script + ');')
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
        """Run JavaScript"""
        if self.must_exit:
            return
        ret = None
        if self.driver is not None:
            try:
                self.driver.set_script_timeout(30)
                ret = self.driver.execute_script(script)
            except Exception:
                logging.exception('Error executing script')
        return ret

    def run_js_file(self, file_name):
        """Execute one of our JS scripts"""
        if self.must_exit:
            return
        ret = None
        script = None
        script_file_path = os.path.join(self.script_dir, file_name)
        if os.path.isfile(script_file_path):
            with open(script_file_path, 'r') as script_file:
                script = script_file.read()
        if self.driver is not None and script is not None:
            try:
                self.driver.set_script_timeout(30)
                ret = self.driver.execute_script('return ' + script)
            except Exception:
                logging.exception('Error executing script file')
            if ret is not None:
                logging.debug(ret)
        return ret

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
                logging.debug("Collecting custom metric %s", name)
                custom_script = str(self.job['customMetrics'][name])
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
                script = 'var wptCustomMetric = function() {' + custom_script + '};try{return wptCustomMetric();}catch(e){};'
                try:
                    custom_metrics[name] = self.execute_js(script)
                    if custom_metrics[name] is not None:
                        logging.debug(custom_metrics[name])
                except Exception:
                    logging.exception('Error collecting custom metrics')
            path = os.path.join(task['dir'], task['prefix'] + '_metrics.json.gz')
            with gzip.open(path, 'wt', 7) as outfile:
                outfile.write(json.dumps(custom_metrics))
        logging.debug("Collecting user timing metrics")
        user_timing = self.run_js_file('user_timing.js')
        if user_timing is not None:
            path = os.path.join(task['dir'], task['prefix'] + '_timed_events.json.gz')
            with gzip.open(path, 'wt', 7) as outfile:
                outfile.write(json.dumps(user_timing))
        logging.debug("Collecting page-level metrics")
        page_data = self.run_js_file('page_data.js')
        if page_data is not None:
            task['page_data'].update(page_data)

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
        if 'steps' not in task:
            task['steps'] = []
        task['steps'].append({
            'prefix': str(task['prefix']),
            'video_subdirectory': str(task['video_subdirectory']),
            'step_name': str(task['step_name']),
            'start_time': time.time(),
            'num': int(task['current_step'])
        })

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        # Clear the state
        self.page = {}
        self.requests = {}
        self.long_tasks = []
        task['page_data'] = {'date': time.time()}
        task['page_result'] = None
        task['run_start_time'] = monotonic()
        if self.browser_version is not None and 'browserVersion' not in task['page_data']:
            task['page_data']['browserVersion'] = self.browser_version
            task['page_data']['browser_version'] = self.browser_version
        self.recording = True
        now = monotonic()
        if not self.task['stop_at_onload']:
            self.last_activity = now
        if self.page_loaded is not None:
            self.page_loaded = now
        self.flush_pending_events()
        DesktopBrowser.on_start_recording(self, task)
        logging.debug('Starting measurement')
        task['start_time'] = datetime.utcnow()

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        DesktopBrowser.on_stop_capture(self, task)

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        self.recording = False
        DesktopBrowser.on_stop_recording(self, task)
        if self.driver:
            if self.job['pngScreenShot']:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.png')
                self.grab_screenshot(screen_shot, png=True)
            else:
                screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
                self.grab_screenshot(screen_shot, png=False, resize=600)
        # Collect end of test data from the browser
        self.collect_browser_metrics(task)
        # Write out the long tasks
        try:
            long_tasks_file = os.path.join(task['dir'], task['prefix'] + '_long_tasks.json.gz')
            with gzip.open(long_tasks_file, 'wt', 7) as f_out:
                f_out.write(json.dumps(self.long_tasks))
        except Exception:
            logging.exception("Error writing the long tasks")
        try:
            interactive_periods = []
            last_end = 0
            for period in self.long_tasks:
                interactive_periods.append([last_end, period[0]])
                last_end = period[1]
            test_end = int((monotonic() - task['run_start_time']) * 1000)
            interactive_periods.append([last_end, test_end])
            interactive_file = os.path.join(task['dir'], task['prefix'] + '_interactive.json.gz')
            with gzip.open(interactive_file, 'wt', 7) as f_out:
                f_out.write(json.dumps(interactive_periods))
        except Exception:
            logging.exception("Error writing the interactive periods")
        # Close the browser if we are done testing (helps flush logs)
        if not len(task['script']):
            self.close_browser(self.job, task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        DesktopBrowser.on_start_processing(self, task)
        if self.must_exit:
            return
        self.process_requests(task)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DesktopBrowser.wait_for_processing(self, task)

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.task['page_data']['URL'] = command['target']
            url = str(command['target']).replace('"', '\"')
            script = 'window.location="{0}";'.format(url)
            script = self.prepare_script_for_record(script)
            self.execute_js(script)
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
            script = command['target']
            if command['record']:
                script = self.prepare_script_for_record(script)
            self.execute_js(script)
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
        if self.driver is not None:
            try:
                self.driver.get(url)
            except Exception as err:
                logging.exception("Error navigating Safari: %s", str(err))

    def grab_raw_screenshot(self):
        """Grab a screenshot using Webdriver"""
        if self.must_exit:
            return
        return self.driver.get_screenshot_as_png()

    def grab_screenshot(self, path, png=True, resize=0):
        """Save the screen shot (png or jpeg)"""
        if self.driver:
            try:
                data = self.grab_raw_screenshot()
                if data is not None:
                    resize_string = '' if not resize else '-resize {0:d}x{0:d} '.format(resize)
                    if png:
                        with open(path, 'wb') as image_file:
                            image_file.write(data)
                        if len(resize_string):
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
            except Exception as err:
                logging.exception('Exception grabbing screen shot: %s', str(err))

    def process_requests(self, task):
        """Convert all of the request and page events into the format needed for WPT"""
        result = {}
        result['requests'] = self.merge_requests()
        result['pageData'] = self.calculate_page_stats(result['requests'])
        if 'metadata' in self.job:
            result['pageData']['metadata'] = self.job['metadata']
        devtools_file = os.path.join(task['dir'], task['prefix'] + '_devtools_requests.json.gz')
        with gzip.open(devtools_file, 'wt', 7) as f_out:
            json.dump(result, f_out)

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
        if len(parts.query):
            request['url'] += '?' + parts.query
        return request

    def merge_requests(self):
        """Merge the requests from the extension and log files"""
        requests = []
        # Start with the requests reported from the extension
        for req_id in self.requests:
            try:
                req = self.requests[req_id]
                if req['from_net'] and 'start' in req:
                    # Generate a bogus URL for the request
                    proto = 'http'
                    if 'socket' in req and req['socket'] in self.connections and 'tls_start' in self.connections[req['socket']]:
                        proto = 'https'
                    if 'socket' in req:
                        req['url'] = '{}://{}/{}'.format(proto, req['socket'], req_id)
                    else:
                        req['url'] = '{}://{}/'.format(proto, req_id)
                    request = self.get_empty_request(req['id'], req['url'])
                    if 'status' in req:
                        request['responseCode'] = req['status']
                    request['created'] = int(round(req['created'] * 1000.0))
                    request['load_start'] = int(round(req['start'] * 1000.0))
                    request['startTime'] = req['start'] * 1000.0
                    if 'first_byte' in req:
                        ttfb = int(round((req['first_byte'] - req['start']) * 1000.0))
                        request['ttfb_ms'] = max(0, ttfb)
                    if 'end' in req:
                        load_time = int(round((req['end'] - req['start']) * 1000.0))
                        request['load_ms'] = max(0, load_time)
                    copy_keys = ['priority', 'socket', 'contentType']
                    for key in copy_keys:
                        if key in req:
                            request[key] = req[key]
                    if 'bytes_in' in req:
                        request['bytesIn'] = req['bytes_in']
                        request['objectSize'] = req['bytes_in']
                    if 'bytes_out' in req:
                        request['bytesOut'] = req['bytes_out']
                    if 'bytes_in_uncompressed' in req:
                        request['objectSizeUncompressed'] = req['bytes_in_uncompressed']
                    # Add the connection timings
                    if 'socket' in req and req['socket'] in self.connections:
                        conn = self.connections[req['socket']]
                        if not conn['claimed']:
                            conn['claimed'] = True
                            if 'dns_start' in conn and conn['dns_start'] >= 0:
                                request['dns_start'] = int(conn['dns_start'] * 1000)
                            if 'dns_end' in conn and conn['dns_end'] >= 0:
                                request['dns_end'] = int(round(conn['dns_end'] * 1000.0))
                            if 'start' in conn and conn['start'] >= 0:
                                request['connect_start'] = int(conn['start'] * 1000)
                            if 'end' in conn and conn['end'] >= 0:
                                request['connect_end'] = int(round(conn['end'] * 1000.0))
                            if 'tls_start' in conn and conn['tls_start'] >= 0:
                                request['ssl_start'] = int(conn['tls_start'] * 1000)
                            if 'tls_end' in conn and conn['tls_end'] >= 0:
                                request['ssl_end'] = int(round(conn['tls_end'] * 1000.0))
                    requests.append(request)
            except Exception:
                logging.error('Error merging request')
        requests.sort(key=lambda x: x['startTime'] if 'startTime' in x else 0)
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
        if 'loaded' in self.page:
            page['loadTime'] = int(round(self.page['loaded'] * 1000.0))
            page['docTime'] = page['loadTime']
            page['loadEventStart'] = page['loadTime']
            page['loadEventEnd'] = page['loadTime']
        if 'DOMContentLoaded' in self.page:
            page['domContentLoadedEventStart'] = int(round(self.page['DOMContentLoaded'] * 1000.0))
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
                    (request['responseCode'] == 200 or request['responseCode'] == 304) and \
                    ('contentType' not in request or
                     (request['contentType'] != 'application/ocsp-response' and
                      request['contentType'] != 'application/pkix-crl')):
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
                    page['basePageSSLTime'] = int(round(request['ssl_end'] - request['ssl_start']))
        if page['responses_200'] == 0 and len(requests):
            if 'responseCode' in requests[0]:
                page['result'] = requests[0]['responseCode']
            else:
                page['result'] = 12999
        self.task['page_result'] = page['result']
        return page

    def process_event(self, event):
        """ Process and individual event """
        # Use the timestamp from the first log event as the start time base if
        # we are not doing the first page load
        if self.start_time is None and self.page_loaded is not None:
            self.start_time = event['ts']

        # Process the actual event
        if event['proc'] == 'Safari':
            self.process_safari_event(event)
        elif event['proc'] == 'com.apple.WebKit.Networking':
            self.process_network_event(event)
        elif event['proc'] == 'com.apple.WebKit.WebContent':
            self.process_content_event(event)

    def process_safari_event(self, event):
        """Process 'Safari' process events"""
        msg = event['msg']
        if event['cat'] == 'Loading' and msg.find('isMainFrame=1') >= 0:
            if msg.find('didStartProvisionalLoadForFrame') >= 0:
                self.page_loaded = None
                if self.start_time is None:
                    self.start_time = event['ts']
                ts = max(.0, event['ts'] - self.start_time)
                logging.debug('%0.6f ***** Safari page load start', ts)
            elif msg.find('didFinishLoadForFrame') >= 0:
                if self.start_time is None:
                    self.start_time = event['ts']
                ts = max(.0, event['ts'] - self.start_time)
                self.page['loaded'] = ts
                self.page_loaded = monotonic()
                logging.debug('%0.6f ***** Safari page load done', ts)

    def process_network_event(self, event):
        """Process 'com.apple.WebKit.Networking' process events"""
        msg = event['msg']
        if event['cat'] in ['boringssl', 'connection']:
            if self.start_time is None:
                self.start_time = event['ts']
            ts = max(.0, event['ts'] - self.start_time)
            connection_search = self.re_connection.search(event['msg'])
            connection = connection_search.group('connection') if connection_search else None
            if connection is not None:
                if connection not in self.connections:
                    self.connections[connection] = {'claimed': False}
                conn = self.connections[connection]
                id = conn['request'] if 'request' in conn else ''
                if event['cat'] == 'boringssl':
                    if 'start' not in conn:
                        conn['start'] = ts
                    if 'tls_start' not in conn:
                        conn['end'] = ts
                        conn['tls_start'] = ts
                        logging.debug('%0.6f %s: Connection %s TLS started', ts, id, connection)
                    if 'tls_end' not in conn and msg.find('Client handshake done') >= 0:
                        conn['tls_end'] = ts
                        logging.debug('%0.6f %s: Connection %s TLS complete', ts, id, connection)
                elif event['cat'] == 'connection':
                    if 'dns_start' not in conn and msg.find('Starting host resolution') >= 0:
                        conn['dns_start'] = ts
                        logging.debug('%0.6f %s (connection): DNS Lookup started', ts, id)
                    if 'dns_end' not in conn and msg.find('Got DNS result') >= 0:
                        conn['dns_end'] = ts
                        if 'start' in conn:
                            conn['start'] = ts
                        logging.debug('%0.6f %s (connection): DNS Lookup complete', ts, id)
        else:
            res = self.re_content_resource.match(event['msg'])
            task_search = self.re_net_task.search(event['msg'])
            task = task_search.group('task') if task_search else None
            connection_search = self.re_net_connection.search(event['msg'])
            connection = connection_search.group('connection') if connection_search else None
            id = None
            if res:
                id = '{}.{}'.format(res.group('frame'), res.group('resource'))
                msg = res.group('msg')
                if task and task not in self.tasks:
                    self.tasks[task] = id
            elif task and task in self.tasks:
                id = self.tasks[task]
            # Only process network events where we have a resource ID
            if id:
                self.last_activity = monotonic()
                if self.start_time is None:
                    self.start_time = event['ts']
                ts = max(.0, event['ts'] - self.start_time)
                if id not in self.requests:
                    self.requests[id] = {'id': id,
                                        'created': ts,
                                        'url': 'https://' + id,
                                        'from_net': True}
                    self.request_count += 1
                request = self.requests[id]
                if connection is not None:
                    request['socket'] = connection
                    if connection not in self.connections:
                        self.connections[connection] = {'claimed': False}
                    conn = self.connections[connection]
                    if 'start' not in conn and msg.find('setting up Connection') >= 0:
                        conn['start'] = ts
                        if 'request' not in conn:
                            conn['request'] = id
                        logging.debug('%0.6f %s: Connection %s started', ts, id, connection)
                    if 'end' not in conn and msg.find('done setting up Connection') >= 0:
                        conn['end'] = ts
                        logging.debug('%0.6f %s: Connection %s complete', ts, id, connection)
                if 'start' not in request and msg.find('sent request') >= 0:
                    request['start'] = ts
                    logging.debug('%0.6f %s: Request started', ts, id)
                if 'first_byte' not in request and msg.find('received response') >= 0:
                    request['first_byte'] = ts
                    logging.debug('%0.6f %s: Response started', ts, id)
                if msg.find('NetworkResourceLoader::didReceiveResponse') >= 0 or \
                        msg.find('NetworkResourceLoader::didReceiveData') >= 0 or \
                        msg.find('NetworkResourceLoader::didFinishLoading') >= 0:
                    if 'first_byte' not in request:
                        request['first_byte'] = ts
                    request['end'] = ts
                    # Parse any details that are available
                    values_search = self.re_parens.search(event['msg'])
                    if values_search:
                        values = values_search.group(1)
                        self.parse_response_values(request, ts, values)
                elif msg.find('summary for task') >= 0:
                    # Parse any details that are available
                    values_search = self.re_curley.search(event['msg'])
                    if values_search:
                        values = values_search.group(1)
                        self.parse_response_values(request, ts, values)
                logging.debug('%0.6f %s (Net): %s', ts, id, msg)

    def process_content_event(self, event):
        """Process 'com.apple.WebKit.WebContent' process events"""
        if event['sender'] in ['CoreFoundation', 'SkyLight', 'LaunchServices', 'AppKit']:
            return
        res = self.re_content_resource.match(event['msg'])
        # Only process content events where we have a resource ID
        if res:
            if self.start_time is None:
                self.start_time = event['ts']
            ts = max(.0, event['ts'] - self.start_time)
            id = '{}.{}'.format(res.group('frame'), res.group('resource'))
            msg = res.group('msg')
            if id not in self.requests:
                self.requests[id] = {'id': id,
                                     'created': ts,
                                     'url': 'https://' + id,
                                     'from_net': True}
            request = self.requests[id]
            if msg.find('WebLoaderStrategy::scheduleLoad') >= 0:
                # Parse any details that are available
                values_search = self.re_parens.search(event['msg'])
                if values_search:
                    values = values_search.group(1)
                    self.parse_response_values(request, ts, values)
            elif msg.find('WebResourceLoader::didReceiveResponse') >= 0:
                if 'first_byte' not in request:
                    request['first_byte'] = ts
                request['end'] = ts
                # Parse any details that are available
                values_search = self.re_parens.search(event['msg'])
                if values_search:
                    values = values_search.group(1)
                    self.parse_response_values(request, ts, values)
            logging.debug('%0.6f %s (Content): %s', ts, id, msg)

    def parse_response_values(self, request, ts, values):
        """ Tokenize a set of response values """
        tokens = values.split(',')
        for token in tokens:
            try:
                parts = token.split('=')
                if len(parts) == 2:
                    key = parts[0].strip(' =,')
                    value = parts[1].strip(' =,')
                    if key == 'httpStatusCode':
                        request['status'] = int(value)
                    elif key == 'MIMEType':
                        request['contentType'] = value
                    elif key == 'reportedEncodedDataLength':
                        bytes_in = int(value)
                        if 'bytes_in' not in request:
                            request['bytes_in'] = 0
                        request['bytes_in'] += bytes_in
                    elif key == 'numBytesReceived':
                        request['bytes_in_uncompressed'] = int(value)
                    elif key == 'priority':
                        request['priority'] = value
                    elif key == 'request_bytes':
                        request['bytes_out'] = int(value)
                    elif key == 'response_bytes':
                        request['bytes_in'] = int(value)
                    elif key == 'cache_hit':
                        if value == 'true':
                            request['from_net'] = False
                    elif key == 'connection':
                        request['socket'] = value
                    elif key == 'response_status':
                        request['status'] = int(value)
            except Exception:
                logging.exception('Error processing response token')

    def get_event(self, timeout):
        """Wait for and return an event from the queue"""
        event = None
        try:
            if timeout is None or timeout <= 0:
                event = self.events.get_nowait()
            else:
                event = self.events.get(True, timeout)
            self.events.task_done()
        except Exception:
            pass
        return event

    def flush_pending_events(self):
        """Clear out any pending events"""
        try:
            while True:
                self.events.get_nowait()
                self.events.task_done()
        except Exception:
            pass

    def wait_for_events_idle(self):
        """ Wait for there to be a 1 second gap in log events or up to 30 seconds max """
        logging.debug('Waiting for Safari to go idle...')
        end = monotonic() + 30
        try:
            while monotonic() < end:
                self.events.get(True, 1)
                self.events.task_done()
        except Exception:
            pass
        logging.debug('Done waiting for Safari to go idle.')

    def pump_safari_log_output(self):
        """ Background thread for reading and processing log output """
        try:
            entry = None
            start_time = None
            while self.safari_log is not None and not self.stop_safari_log:
                line_start = self.safari_log.stdout.read(1)
                # Start collecting JSON objects at the first {
                if entry is None:
                    if  line_start == '{':
                        entry = line_start + self.safari_log.stdout.readline().strip("\r\n")
                else:
                    if line_start == '}' and entry is not None:
                        entry += '}'
                        try:
                            raw = json.loads(entry)
                            if 'processImagePath' in raw and \
                                    'senderImagePath' in raw and \
                                    'subsystem' in raw and \
                                    'eventMessage' in raw and \
                                    'category' in raw and \
                                    'timestamp' in raw:
                                ts = datetime.strptime(raw['timestamp'][0:26], '%Y-%m-%d %H:%M:%S.%f').timestamp()
                                if start_time is None:
                                    start_time = ts
                                elapsed = ts - start_time
                                event = {
                                    'proc': os.path.basename(raw['processImagePath']),
                                    'sender': os.path.basename(raw['senderImagePath']),
                                    'subsystem': raw['subsystem'],
                                    'msg': raw['eventMessage'],
                                    'cat': raw['category'],
                                    'ts': elapsed,
                                }
                                # Filter out a bunch of noise
                                if event['sender'] in ['Safari', 'WebKit'] or event['proc'] != 'Safari' :
                                    self.events.put(event)
                        except Exception:
                            logging.exception('Error parsing log event')
                    else:
                        line = line_start + self.safari_log.stdout.readline().strip("\r\n")
                        if line.startswith('{'):
                            entry = line.strip()
                        elif line.startswith(',{'):
                            entry = line[1:].strip()
                        else:
                            entry += line.strip()
        except Exception:
            logging.exception('Error processing Safari log')
        logging.debug('Done pumping Safari log messages')
