# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Support for Firefox"""
from datetime import datetime, timedelta
import glob
import gzip
import io
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
if (sys.version_info >= (3, 0)):
    from time import monotonic
    unicode = str
    from urllib.parse import urlsplit # pylint: disable=import-error
    GZIP_TEXT = 'wt'
else:
    from monotonic import monotonic
    from urlparse import urlsplit # pylint: disable=import-error
    GZIP_TEXT = 'w'
try:
    import ujson as json
except BaseException:
    import json
from .desktop_browser import DesktopBrowser


class Firefox(DesktopBrowser):
    """Firefox"""
    def __init__(self, path, options, job):
        DesktopBrowser.__init__(self, path, options, job)
        self.job = job
        self.task = None
        self.options = options
        self.path = path
        self.event_name = None
        self.moz_log = None
        self.driver = None
        self.addons = None
        self.extension_id = None
        self.possible_navigation_error = None
        self.nav_error = None
        self.page_loaded = None
        self.recording = False
        self.connected = False
        self.start_offset = None
        self.browser_version = None
        self.main_request_headers = None
        self.extension_start_time = None
        self.navigate_start_time = None
        self.wait_interval = 5.0
        self.wait_for_script = None
        self.log_pos = {}
        self.log_level = 5
        self.must_exit_now = False
        if 'browser_info' in job and 'log_level' in job['browser_info']:
            self.log_level = job['browser_info']['log_level']
        self.page = {}
        self.requests = {}
        self.request_count = 0
        self.total_sleep = 0
        self.long_tasks = []
        self.last_activity = monotonic()
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')
        self.start_page = 'http://127.0.0.1:8888/orange.html'
        self.block_domains = [
            "tracking-protection.cdn.mozilla.net",
            "shavar.services.mozilla.com",
            "firefox.settings.services.mozilla.com",
            "snippets.cdn.mozilla.net",
            "content-signature-2.cdn.mozilla.net",
            "aus5.mozilla.org"]
        self.duplicates = []

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        self.moz_log = os.path.join(task['dir'], 'moz.log')
        self.log_pos = {}
        self.page = {}
        self.requests = {}
        self.request_count = 0
        self.main_request_headers = None
        os.environ["MOZ_LOG_FILE"] = self.moz_log
        moz_log_env = 'timestamp,nsHttp:{0:d},nsSocketTransport:{0:d}'\
                      'nsHostResolver:{0:d},pipnss:5'.format(self.log_level)
        os.environ["MOZ_LOG"] = moz_log_env
        logging.debug('MOZ_LOG = %s', moz_log_env)
        DesktopBrowser.prepare(self, job, task)
        profile_template = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                        'support', 'Firefox', 'profile')
        if not task['cached'] and os.path.isdir(profile_template):
            try:
                if os.path.isdir(task['profile']):
                    shutil.rmtree(task['profile'])
                shutil.copytree(profile_template, task['profile'])
            except Exception:
                logging.exception('Error copying Firefox profile')
        # Delete any unsent crash reports
        crash_dir = None
        if platform.system() == 'Windows':
            if 'APPDATA' in os.environ:
                crash_dir = os.path.join(os.environ['APPDATA'],
                                         'Mozilla', 'Firefox', 'Crash Reports')
        else:
            crash_dir = os.path.join(os.path.expanduser('~'),
                                     '.mozilla', 'firefox', 'Crash Reports')
        if crash_dir and os.path.isdir(crash_dir):
            logging.debug("Clearing crash reports in %s", crash_dir)
            try:
                shutil.rmtree(crash_dir)
            except Exception:
                pass

        # Prepare the config for the extension to query
        if self.job['message_server'] is not None:
            config = None
            names = ['block',
                     'block_domains',
                     'block_domains_except',
                     'headers',
                     'cookies',
                     'overrideHosts']
            for name in names:
                if name in task and task[name]:
                    if config is None:
                        config = {}
                    config[name] = task[name]
            self.job['message_server'].config = config

    def start_firefox(self, job, task):
        """Start Firefox using WebDriver"""
        if self.must_exit:
            return
        from selenium import webdriver # pylint: disable=import-error

        capabilities = webdriver.DesiredCapabilities.FIREFOX.copy()
        if 'ignoreSSL' in job and job['ignoreSSL']:
            capabilities['acceptInsecureCerts'] = True
        else:
            capabilities['acceptInsecureCerts'] = False

        capabilities['moz:firefoxOptions'] = {
            'binary': self.path,
            'args': ['-profile', task['profile']],
            'prefs': self.prepare_prefs(),
            "log": {"level": "error"},
            'env': {
                "MOZ_LOG_FILE": os.environ["MOZ_LOG_FILE"],
                "MOZ_LOG": os.environ["MOZ_LOG"]
            }
        }
        service_args = ["--marionette-port", "2828"]

        self.driver = webdriver.Firefox(desired_capabilities=capabilities, service_args=service_args)
        logging.debug(self.driver.capabilities)

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

        logging.debug('Installing extension')
        extension_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support', 'Firefox', 'extension')
        self.driver.install_addon(extension_path, temporary=True)

    def launch(self, job, task):
        """Launch the browser"""
        if self.must_exit:
            return
        if self.job['message_server'] is not None:
            self.job['message_server'].flush_messages()
        self.connected = False

        try:
            self.start_firefox(job, task)
            time.sleep(0.5)
            self.wait_for_extension()
            if self.connected:
                # Disable image types in prefs 
                # eventually, we might want to consider exposing more prefs this way somewhere in here
                if self.job.get('disableAVIF'):
                    self.set_pref('image.avif.enabled', 'false')
                if self.job.get('disableWEBP'):
                    self.set_pref('image.webp.enabled', 'false')
                if self.job.get('disableJXL'):
                    self.set_pref('image.jxl.enabled', 'false')

                # Override the UA String if necessary
                ua_string = self.execute_js('return navigator.userAgent;')
                modified = False
                if 'uastring' in self.job:
                    ua_string = self.job['uastring']
                    modified = True
                if ua_string is not None and 'AppendUA' in task:
                    ua_string += ' ' + task['AppendUA']
                    modified = True
                if modified:
                    logging.debug(ua_string)
                    self.driver_set_pref('general.useragent.override', ua_string)
                # Location
                if 'lat' in self.job and 'lng' in self.job:
                    try:
                        lat = float(str(self.job['lat']))
                        lng = float(str(self.job['lng']))
                        location_uri = 'data:application/json,{{'\
                            '"status":"OK","accuracy":10.0,'\
                            '"location":{{"lat":{0:f},"lng":{1:f}}}'\
                            '}}'.format(lat, lng)
                        logging.debug('Setting location: %s', location_uri)
                        self.driver_set_pref('geo.wifi.uri', location_uri)
                    except Exception:
                        logging.exception('Error overriding location')
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
        except Exception as err:
            logging.exception("Error starting Firefox")
            task['error'] = 'Error starting Firefox: {0}'.format(err.__str__())

    def get_pref_value(self, value):
        """Convert a JSON pref value to Python"""
        str_match = re.match(r'^"(.*)"$', value)
        if value == 'true':
            value = True
        elif value == 'false':
            value = False
        elif re.match(r'^[\d]+$', value):
            value = int(value)
        elif str_match:
            value = str_match.group(1)
        else:
            value = None
        return value

    def driver_set_pref(self, key, value):
        """Set a Firefox pref at runtime"""
        if self.driver is not None:
            try:
                script = 'const { Services } = ChromeUtils.import("resource://gre/modules/Services.jsm");'
                script += 'Services.prefs.'
                if isinstance(value, bool):
                    script += 'setBoolPref'
                elif isinstance(value, (str, unicode)):
                    script += 'setStringPref'
                else:
                    script += 'setIntPref'
                script += '({0}, {1});'.format(json.dumps(key), json.dumps(value))
                logging.debug(script)
                self.driver.set_context(self.driver.CONTEXT_CHROME)
                self.driver.execute_script(script)
            except Exception:
                logging.exception("Error setting pref")
            finally:
                self.driver.set_context(self.driver.CONTEXT_CONTENT)

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
        self.connected = False
        self.disconnect_driver()
        DesktopBrowser.close_browser(self, job, task)
        # make SURE the Firefox processes are gone
        if platform.system() == "Linux":
            subprocess.call(['killall', '-9', 'firefox'])
            subprocess.call(['killall', '-9', 'firefox-trunk'])
        os.environ["MOZ_LOG_FILE"] = ''
        os.environ["MOZ_LOG"] = ''

    def stop(self, job, task):
        """Kill the browser"""
        self.close_browser(job, task)
        DesktopBrowser.stop(self, job, task)
        # delete the raw log files
        if self.moz_log is not None:
            files = sorted(glob.glob(self.moz_log + '*'))
            for path in files:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def run_lighthouse_test(self, task):
        """Stub for lighthouse test"""
        pass
        
    def run_axe(self, task):
        """Build the axe script to run in-browser"""
        self.profile_start('dtbrowser.axe_run')
        start = monotonic()
        script = None
        try:
            with open(os.path.join(self.support_path, 'axe', 'axe-core', 'axe.min.js')) as f_in:
                script = f_in.read()
            if script is not None:
                script += 'return axe.run({runOnly:['
                axe_cats = self.job.get('axe_categories').split(',')
                script += "'" + "', '".join(axe_cats) + "'"
                script += ']}).then(results=>{return results;});'
        except Exception as err:
            logging.exception("Exception running Axe: %s", err.__str__())
        if self.must_exit_now:
            return
        completed = False
        try:
            # Run the axe library (give it 30 seconds at most)
            response = self.execute_js(script)
            if response is not None:
                result = response
                if result:
                    completed = True
                    axe_results = result
                    axe_info = {}
                    if 'testEngine' in axe_results:
                        axe_info['testEngine'] = axe_results['testEngine']['version']
                    if 'violations' in axe_results:
                        axe_info['violations'] = axe_results['violations']
                    if 'passes' in axe_results:
                        axe_info['passes'] = axe_results['passes']
                    if 'incomplete' in axe_results:
                        axe_info['incomplete'] = axe_results['incomplete']
                    task['page_data']['axe'] = axe_info
        except Exception as err:
            logging.exception("Exception running Axe: %s", err.__str__())
        if not completed:
            task['page_data']['axe_failed'] = 1
        self.axe_time = monotonic() - start
        logging.debug("axe test took %0.3f seconds", self.axe_time)
        self.profile_end('dtbrowser.axe_run')

    def run_task(self, task):
        """Run an individual test"""
        if self.connected:
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
            # Always navigate to about:blank after finishing in case the tab is
            # remembered across sessions
            try:
                self.navigate('about:blank')
            except Exception:
                logging.exception('Marionette exception navigating to about:blank after the test')
            self.task = None

    def alert_size(self, _alert_config, _task_dir, _prefix):
        '''Checks the agents file size and alert on certain percentage over avg byte size'''             
        self.alert_desktop_results(_alert_config, 'Firefox', _task_dir, _prefix)


    def wait_for_extension(self):
        """Wait for the extension to send the started message"""
        if self.job['message_server'] is not None:
            end_time = monotonic() + 30
            while monotonic() < end_time and not self.connected and not self.must_exit:
                try:
                    message = self.job['message_server'].get_message(1)
                    try:
                        self.process_message(message)
                    except Exception:
                        logging.exception('Error processing message')
                except Exception:
                    pass

    def wait_for_page_load(self):
        """Wait for the onload event from the extension"""
        if self.job['message_server'] is not None and self.connected:
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
                    message = self.job['message_server'].get_message(interval)
                    try:
                        self.process_message(message)
                    except Exception:
                        logging.exception('Error processing message')
                except Exception:
                    pass
                now = monotonic()
                elapsed_test = now - start_time
                if 'minimumTestSeconds' in self.task and \
                        elapsed_test < self.task['minimumTestSeconds'] and \
                        now < end_time:
                    continue
                # Allow up to 5 seconds after a navigation for a re-navigation to happen
                # (bizarre sequence Firefox seems to do)
                if self.possible_navigation_error is not None:
                    elapsed_error = now - self.possible_navigation_error['time']
                    if elapsed_error > 5:
                        self.nav_error = self.possible_navigation_error['error']
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
                        self.task['page_data']['result'] = 99998
                elif max_requests > 0 and self.request_count > max_requests:
                    done = True
                    # only consider it an error if we didn't get a page load event
                    if self.page_loaded is None:
                        self.task['error'] = "Exceeded Maximum Requests"
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
                custom_script = unicode(self.job['customMetrics'][name])
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
            with gzip.open(path, GZIP_TEXT, 7) as outfile:
                outfile.write(json.dumps(custom_metrics))
        logging.debug("Collecting user timing metrics")
        user_timing = self.run_js_file('user_timing.js')
        if user_timing is not None:
            path = os.path.join(task['dir'], task['prefix'] + '_timed_events.json.gz')
            with gzip.open(path, GZIP_TEXT, 7) as outfile:
                outfile.write(json.dumps(user_timing))
        logging.debug("Collecting page-level metrics")
        page_data = self.run_js_file('page_data.js')
        if page_data is not None:
            task['page_data'].update(page_data)

    def process_message(self, message):
        """Process a message from the extension"""
        logging.debug(message)
        if not self.connected:
            logging.debug('Extension started')
            self.connected = True
        if 'path' in message and message['path'].startswith('wptagent.'):
                if 'ts' in message['body']:
                    if message['path'] == 'wptagent.started':
                        now = monotonic()
                        self.extension_start_time = now - (float(message['body']['ts']) / 1000.0)
                        logging.debug("Extension start time: %0.3f", self.extension_start_time)
                    elif message['path'] == 'wptagent.longTask' and 'dur' in message['body'] and self.extension_start_time is not None and self.recording:
                        # adjust the long task time to be relative to the step start time
                        duration = float(message['body']['dur']) / 1000.0
                        end_time = (self.task['run_start_time'] - self.extension_start_time) + (float(message['body']['ts']) / 1000.0)
                        start_time = end_time - duration
                        if start_time > 0:
                            self.long_tasks.append([int(start_time * 1000), int(end_time * 1000)])
                        else:
                            logging.debug("Long task outside of the test time")
                elif message['path'] == 'wptagent.overrideHost':
                    requestId = message['body']['requestId']
                    logging.debug('adding to override duplicate list %s', requestId)
                    self.duplicates.append(requestId)
                    if requestId in self.requests:
                        logging.debug('deleting duplicate request %s', requestId)
                        del self.requests[requestId]


        elif self.recording:
            self.last_activity = monotonic()
            try:
                # Make all of the timestamps relative to the test start to match the log events
                if 'timeStamp' in message['body']:
                    timestamp = message['body']['timeStamp']
                    seconds = int(timestamp / 1000)
                    milliseconds = timestamp - (seconds * 1000)
                    event_time = datetime.utcfromtimestamp(seconds)
                    event_time += timedelta(milliseconds=milliseconds)
                    elapsed = event_time - self.task['start_time']
                    message['body']['timeStamp'] = elapsed.total_seconds()
                cat, msg = message['path'].split('.', 1)
                if 'timeStamp' not in message['body'] or message['body']['timeStamp'] > 0:
                    if cat == 'webNavigation':
                        self.process_web_navigation(msg, message['body'])
                    elif cat == 'webRequest':
                        self.process_web_request(msg, message['body'])
            except Exception:
                logging.exception('Error processing message')

    def process_web_navigation(self, message, evt):
        """Handle webNavigation.*"""
        if evt is not None:
            if message == 'onBeforeNavigate':
                if 'frameId' in evt and evt['frameId'] == 0:
                    self.page_loaded = None
                    self.possible_navigation_error = None
                    logging.debug("Starting navigation")
                    if 'timeStamp' in evt and 'start' not in self.page:
                        self.page['start'] = evt['timeStamp']
            elif message == 'onCommitted':
                if 'timeStamp' in evt and 'frameId' in evt and evt['frameId'] == 0 \
                        and 'committed' not in self.page:
                    self.page['committed'] = evt['timeStamp']
                if 'injectScript' in self.job and self.connected:
                    logging.debug("Injecting script: \n%s", self.job['injectScript'])
                    try:
                        self.execute_js(self.job['injectScript'])
                    except Exception:
                        logging.exception('Error injecting script')
            elif message == 'onDOMContentLoaded':
                if 'timeStamp' in evt and 'frameId' in evt and evt['frameId'] == 0:
                    self.page['DOMContentLoaded'] = evt['timeStamp']
            elif message == 'onCompleted':
                if 'frameId' in evt and evt['frameId'] == 0:
                    self.page_loaded = monotonic()
                    logging.debug("Page loaded")
                    if 'timeStamp' in evt:
                        self.page['loaded'] = evt['timeStamp']
            elif message == 'onErrorOccurred':
                if 'frameId' in evt and evt['frameId'] == 0:
                    logging.debug("Possible navigation error")
                    err_msg = evt['error'] if 'error' in evt else 'Navigation failed'
                    self.possible_navigation_error = {
                        'time': monotonic(),
                        'error': err_msg
                    }

    def process_web_request(self, message, evt):
        """Handle webRequest.*"""
        if evt is not None and 'requestId' in evt and 'timeStamp' in evt and evt['requestId'] not in self.duplicates:
            if evt['requestId'] not in self.requests:
                self.requests[evt['requestId']] = {'id': evt['requestId'],
                                                   'from_net': True}
            request = self.requests[evt['requestId']]
            if 'url' in evt and evt['url'] is not None and 'url' not in request:
                request['url'] = evt['url']
            if 'method' in evt and evt['method'] is not None and 'method' not in request:
                request['method'] = evt['method']
            if 'type' in evt and evt['type'] is not None and 'type' not in request:
                request['type'] = evt['type']
            if 'ip' in evt and evt['ip'] is not None and 'ip' not in request:
                request['ip'] = evt['ip']
            if 'fromCache' in evt and evt['fromCache']:
                request['from_net'] = False
            if 'statusLine' in evt and evt['statusLine'] is not None:
                request['status_line'] = evt['statusLine']
            if 'statusCode' in evt and evt['statusCode'] is not None:
                request['status'] = evt['statusCode']
            if 'requestHeaders' in evt and evt['requestHeaders'] is not None and \
                    'request_headers' not in request:
                request['request_headers'] = list(evt['requestHeaders'])
            if 'responseHeaders' in evt and evt['responseHeaders'] is not None and \
                    'response_headers' not in request:
                request['response_headers'] = list(evt['responseHeaders'])
                if self.main_request_headers is None:
                    self.main_request_headers = list(evt['responseHeaders'])

            if message == 'onBeforeRequest':
                request['created'] = evt['timeStamp']
            elif message == 'onSendHeaders':
                request['start'] = evt['timeStamp']
            elif message == 'onBeforeRedirect':
                if 'first_byte' not in request:
                    request['first_byte'] = evt['timeStamp']
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
            elif message == 'onHeadersReceived':
                if 'first_byte' not in request:
                    request['first_byte'] = evt['timeStamp']
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
            elif message == 'onResponseStarted':
                if 'first_byte' not in request:
                    request['first_byte'] = evt['timeStamp']
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
            elif message == 'onCompleted':
                if 'first_byte' not in request:
                    request['first_byte'] = evt['timeStamp']
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
                if 'from_net' in request and request['from_net']:
                    self.request_count += 1
            elif message == 'onErrorOccurred':
                if 'end' not in request or evt['timeStamp'] > request['end']:
                    request['end'] = evt['timeStamp']
                if 'error' in evt:
                    request['error'] = evt['error']
                if 'status' not in request:
                    request['status'] = 12999

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
        # Mark the start point in the various log files
        self.log_pos = {}
        if self.moz_log is not None:
            files = sorted(glob.glob(self.moz_log + '*'))
            for path in files:
                self.log_pos[path] = os.path.getsize(path)
        self.recording = True
        now = monotonic()
        if not self.task['stop_at_onload']:
            self.last_activity = now
        if self.page_loaded is not None:
            self.page_loaded = now
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
        if self.connected:
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
            with gzip.open(long_tasks_file, GZIP_TEXT, 7) as f_out:
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
            with gzip.open(interactive_file, GZIP_TEXT, 7) as f_out:
                f_out.write(json.dumps(interactive_periods))
        except Exception:
            logging.exception("Error writing the interactive periods")
        # Run Axe before we close the browser
        if self.job.get('axe'):
            self.run_axe(task)
        # Close the browser if we are done testing (helps flush logs)
        if not len(task['script']):
            self.close_browser(self.job, task)
        # Copy the log files
        if self.moz_log is not None and not self.must_exit:
            task['moz_log'] = os.path.join(task['dir'], task['prefix'] + '_moz.log')
            files = sorted(glob.glob(self.moz_log + '*'))
            for path in files:
                try:
                    base_name = os.path.basename(path)
                    dest = os.path.join(task['dir'],
                                        task['prefix'] + '_' + base_name + '.gz')
                    start_pos = self.log_pos[path] if path in self.log_pos else 0
                    end_pos = os.path.getsize(path)
                    if end_pos > start_pos:
                        length = end_pos - start_pos
                        logging.debug('Preparing moz log %s (%d bytes from %d)',
                                      base_name, length, start_pos)
                        with open(path, 'rb') as f_in:
                            f_in.seek(start_pos)
                            with gzip.open(dest, 'wb', 7) as f_out:
                                while length > 0:
                                    read_bytes = min(length, 1024 * 1024)
                                    buff = f_in.read(read_bytes)
                                    read_bytes = len(buff)
                                    f_out.write(buff)
                                    length -= read_bytes
                except Exception:
                    logging.exception('Error copying log files')

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        DesktopBrowser.on_start_processing(self, task)
        if self.must_exit:
            return
        # Parse the moz log for the accurate request timings
        request_timings = []
        if 'moz_log' in task:
            from internal.support.firefox_log_parser import FirefoxLogParser
            parser = FirefoxLogParser()
            start_time = task['start_time'].strftime('%Y-%m-%d %H:%M:%S.%f')
            logging.debug('Parsing moz logs relative to %s start time', start_time)
            request_timings = parser.process_logs(task['moz_log'], start_time)
            files = sorted(glob.glob(task['moz_log'] + '*'))
            for path in files:
                try:
                    os.remove(path)
                except Exception:
                    pass
        # Build the request and page data
        if len(request_timings) and task['current_step'] >= 1:
            self.adjust_timings(request_timings)
        self.process_requests(request_timings, task)


    def adjust_timings(self, requests):
        """Adjust the request timings to start at zero for the earliest timestamp"""
        timestamps = ['dns_start', 'dns_end', 'connect_start', 'connect_end',
                      'ssl_start', 'ssl_end', 'start', 'first_byte', 'end']
        earliest = None
        for request in requests:
            for entry in timestamps:
                if entry in request and request[entry] >= 0:
                    if earliest is None or request[entry] < earliest:
                        earliest = request[entry]
        logging.debug("Adjusting request timings by %0.3f seconds", earliest)
        if earliest is not None and earliest > 0:
            self.start_offset = earliest
            for request in requests:
                for entry in timestamps:
                    if entry in request and request[entry] >= earliest:
                        request[entry] -= earliest
                if 'chunks' in request:
                    for chunk in request['chunks']:
                        if 'ts' in chunk and chunk['ts'] >= earliest:
                            chunk['ts'] -= earliest

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
        elif command['command'] == 'firefoxpref':
            if 'target' in command and 'value' in command:
                self.set_pref(command['target'], command['value'])
        elif command['command'] == 'setlocation':
            try:
                if 'target' in command and command['target'].find(',') > 0:
                    accuracy = 0
                    if 'value' in command and re.match(r'\d+', command['value']):
                        accuracy = int(re.search(r'\d+', str(command['value'])).group())
                    parts = command['target'].split(',')
                    lat = float(parts[0])
                    lng = float(parts[1])
                    location_uri = 'data:application/json,{{'\
                        '"status":"OK","accuracy":{2:d},'\
                        '"location":{{"lat":{0:f},"lng":{1:f}}}'\
                        '}}'.format(lat, lng, accuracy)
                    logging.debug('Setting location: %s', location_uri)
                    self.set_pref('geo.wifi.uri', location_uri)
            except Exception:
                logging.exception('Error setting location')
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
                logging.exception("Error navigating Firefox: %s", str(err))

    def prepare_prefs(self):
        """Load the prefs file and configure them through webdriver"""
        prefs = {}
        prefs_file = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support', 'Firefox', 'profile', 'prefs.js')
        with open(prefs_file) as f_in:
            for line in f_in:
                matches = re.search(r'user_pref\("([^"]+)",[\s]*([^\)]*)[\s]*\);', line)
                if matches:
                    key = matches.group(1).strip()
                    value = self.get_pref_value(matches.group(2).strip())
                    if value is not None:
                        prefs[key] = value
        return prefs

    def set_pref(self, key, value_str):
        """Set an individual pref value"""
        value = self.get_pref_value(value_str.strip())
        if value is not None:
            try:
                logging.debug('Setting Pref "%s" to %s', key, value_str)
                self.driver_set_pref(key, value)
            except Exception:
                logging.exception('Error setting pref')
    
    def grab_raw_screenshot(self):
        """Grab a screenshot using Marionette"""
        if self.must_exit:
            return
        return self.driver.get_screenshot_as_png()

    def grab_screenshot(self, path, png=True, resize=0):
        """Save the screen shot (png or jpeg)"""
        if self.connected:
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

    def process_requests(self, request_timings, task):
        """Convert all of the request and page events into the format needed for WPT"""
        result = {}
        result['requests'] = self.merge_requests(request_timings)
        result['pageData'] = self.calculate_page_stats(result['requests'])
        if 'metadata' in self.job:
            result['pageData']['metadata'] = self.job['metadata']
        devtools_file = os.path.join(task['dir'], task['prefix'] + '_devtools_requests.json.gz')
        with gzip.open(devtools_file, GZIP_TEXT, 7) as f_out:
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

    def get_header_value(self, headers, name):
        """Return the value for the given header"""
        value = ''
        name = name.lower()
        for header in headers:
            pos = header.find(':')
            if pos > 0:
                key = header[0:pos].lower()
                if key.startswith(name):
                    val = header[pos + 1:].strip()
                    if len(value):
                        value += '; '
                    value += val
        return value

    def merge_requests(self, request_timings):
        """Merge the requests from the extension and log files"""
        requests = []
        # Start with the requests reported from the extension
        for req_id in self.requests:
            try:
                req = self.requests[req_id]
                if req['from_net'] and 'start' in req and 'url' in req:
                    request = self.get_empty_request(req['id'], req['url'])
                    if 'ip' in req:
                        request['ip_addr'] = req['ip']
                    if 'method' in req:
                        request['method'] = req['method']
                    if 'status' in req:
                        request['responseCode'] = req['status']
                    if 'type' in req:
                        request['requestType'] = req['type']
                    if 'request_headers' in req:
                        for header in req['request_headers']:
                            if 'name' in header and 'value' in header:
                                header_text = '{0}: {1}'.format(header['name'], header['value'])
                                request['bytesOut'] += len(header_text) + 2
                                request['headers']['request'].append(header_text)
                    if 'status_line' in req:
                        request['bytesIn'] += len(req['status_line']) + 2
                        request['headers']['response'].append(req['status_line'])
                    if 'response_headers' in req:
                        for header in req['response_headers']:
                            if 'name' in header and 'value' in header:
                                try:
                                    header_text = '{0}: {1}'.format(header['name'], header['value'])
                                    request['bytesIn'] += len(header_text) + 2
                                    request['headers']['response'].append(header_text)
                                except Exception:
                                    logging.exception('Error appending response header')
                    if 'created' in req:
                        request['created'] = int(round(req['created'] * 1000.0))
                    request['load_start'] = int(round(req['start'] * 1000.0))
                    request['startTime'] = req['start'] * 1000.0
                    if 'first_byte' in req:
                        ttfb = int(round((req['first_byte'] - req['start']) * 1000.0))
                        request['ttfb_ms'] = max(0, ttfb)
                    if 'end' in req:
                        load_time = int(round((req['end'] - req['start']) * 1000.0))
                        request['load_ms'] = max(0, load_time)
                    size = self.get_header_value(request['headers']['response'], 'Content-Length')
                    if len(size):
                        request['bytesIn'] += int(re.search(r'\d+', str(size)).group())
                    requests.append(request)
            except Exception:
                logging.exception('Error merging request')
        # Overwrite them with the same requests from the logs
        for request in requests:
            for req in request_timings:
                try:
                    if 'claimed' not in req and 'url' in req and 'full_url' in request \
                            and 'start' in req and request['full_url'] == req['url']:
                        req['claimed'] = True
                        self.populate_request(request, req)
                        break
                except Exception:
                    logging.exception('Error populating request')
        # Add any events from the logs that weren't reported by the extension
        for req in request_timings:
            try:
                if 'claimed' not in req and 'url' in req and 'start' in req:
                    request = self.get_empty_request(req['id'], req['url'])
                    self.populate_request(request, req)
                    requests.append(request)
            except Exception:
                logging.exception('Error adding request from logs')
        # parse values out of the headers
        for request in requests:
            try:
                value = self.get_header_value(request['headers']['response'], 'Expires')
                if value:
                    request['expires'] = value
                value = self.get_header_value(request['headers']['response'], 'Cache-Control')
                if value:
                    request['cacheControl'] = value
                value = self.get_header_value(request['headers']['response'], 'Content-Type')
                if value:
                    request['contentType'] = value
                value = self.get_header_value(request['headers']['response'], 'Content-Encoding')
                if value:
                    request['contentEncoding'] = value
                value = self.get_header_value(request['headers']['response'], 'Content-Length')
                if value:
                    request['objectSize'] = value
            except Exception:
                logging.exception('Error processing headers')
        # Strip the headers if necessary
        noheaders = False
        if 'noheaders' in self.job and self.job['noheaders']:
            noheaders = True
        if noheaders:
            for request in requests:
                if 'headers' in request:
                    del request['headers']
        requests.sort(key=lambda x: x['startTime'] if 'startTime' in x else 0)
        return requests

    def populate_request(self, request, log_request):
        """Populate a request object from the log request values"""
        request['load_start'] = int(log_request['start'] * 1000)
        request['startTime'] = log_request['start'] * 1000.0
        request['created'] = log_request['start'] * 1000.0
        if 'status' in log_request:
            request['responseCode'] = log_request['status']
        if 'dns_start' in log_request and log_request['dns_start'] >= 0:
            request['dns_start'] = int(log_request['dns_start'] * 1000)
        if 'dns_end' in log_request and log_request['dns_end'] >= 0:
            request['dns_end'] = int(round(log_request['dns_end'] * 1000.0))
        if 'connect_start' in log_request and log_request['connect_start'] >= 0:
            request['connect_start'] = int(log_request['connect_start'] * 1000)
        if 'connect_end' in log_request and log_request['connect_end'] >= 0:
            request['connect_end'] = int(round(log_request['connect_end'] * 1000.0))
        if 'ssl_start' in log_request and log_request['ssl_start'] >= 0:
            request['ssl_start'] = int(log_request['ssl_start'] * 1000)
        if 'ssl_end' in log_request and log_request['ssl_end'] >= 0:
            request['ssl_end'] = int(round(log_request['ssl_end'] * 1000.0))
        if 'connection' in log_request:
            request['socket'] = log_request['connection']
        if 'first_byte' in log_request:
            request['ttfb_ms'] = int(round((log_request['first_byte'] -
                                            log_request['start']) * 1000.0))
        if 'end' in log_request:
            request['load_ms'] = int(round((log_request['end'] -
                                            log_request['start']) * 1000.0))
        if 'bytes_in' in log_request:
            request['bytesIn'] = log_request['bytes_in']
        if 'chunks' in log_request and len(log_request['chunks']):
            request['chunks'] = []
            for chunk in log_request['chunks']:
                ts = chunk['ts'] * 1000.0
                request['chunks'].append({'ts': ts, 'bytes': chunk['bytes']})
        if 'request_headers' in log_request:
            request['headers']['request'] = list(log_request['request_headers'])
        if 'response_headers' in log_request:
            request['headers']['response'] = list(log_request['response_headers'])
        if 'http2_stream_id' in log_request:
            request['http2_stream_id'] = log_request['http2_stream_id']
            request['protocol'] = 'HTTP/2'
        if 'http2_stream_dependency' in log_request:
            request['http2_stream_dependency'] = log_request['http2_stream_dependency']
        if 'http2_stream_weight' in log_request:
            request['http2_stream_weight'] = log_request['http2_stream_weight']

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
            try:
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
                if 'responseCode' in request and request['responseCode'] is not None:
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
                             request['contentType'] != 'application/pkix-crl' and 
                             request['contentType'] != 'application/pkix-cert' and
                             request['contentType'] != 'application/ca-cert')):
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
                            page['basePageSSLTime'] = int(round(request['ssl_end'] -
                                                                request['ssl_start']))
            except Exception:
                pass
        if page['responses_200'] == 0 and len(requests):
            if 'responseCode' in requests[0]:
                page['result'] = requests[0]['responseCode']
            else:
                page['result'] = 12999
        self.task['page_result'] = page['result']
        return page
