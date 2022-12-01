# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Base class support for browsers that speak the dev tools protocol"""
import glob
import gzip
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
if (sys.version_info >= (3, 0)):
    from time import monotonic
    from urllib.parse import urlsplit # pylint: disable=import-error
    unicode = str
    GZIP_TEXT = 'wt'
else:
    from monotonic import monotonic
    from urlparse import urlsplit # pylint: disable=import-error
    GZIP_TEXT = 'w'
try:
    import ujson as json
except BaseException:
    import json
from .optimization_checks import OptimizationChecks


class DevtoolsBrowser(object):
    """Devtools Browser base"""
    CONNECT_TIME_LIMIT = 120

    def __init__(self, options, job, use_devtools_video=True, is_webkit=False, is_ios=False):
        self.options = options
        self.job = job
        self.is_webkit = is_webkit
        self.is_ios = is_ios
        self.devtools = None
        self.task = None
        self.event_name = None
        self.browser_version = None
        self.use_devtools_video = use_devtools_video
        self.lighthouse_command = None
        self.devtools_screenshot = True
        self.must_exit_now = False
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support')
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')
        self.webkit_context = None
        self.total_sleep = 0
        self.document_domain = None

    def shutdown(self):
        """Agent is dying NOW"""
        self.must_exit_now = True
        if self.devtools is not None:
            self.devtools.shutdown()

    def connect(self, task):
        """Connect to the dev tools interface"""
        ret = False
        from internal.devtools import DevTools
        self.devtools = DevTools(self.options, self.job, task, self.use_devtools_video, self.is_webkit, self.is_ios)
        if task['running_lighthouse']:
            ret = self.devtools.wait_for_available(self.CONNECT_TIME_LIMIT)
        else:
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
            # Always navigate to about:blank after finishing in case the tab is
            # remembered across sessions
            if self.task is not None and self.task['error'] is None:
                self.devtools.send_command('Page.navigate', {'url': 'about:blank'}, wait=True)
            if self.webkit_context is not None:
                self.devtools.send_command('Automation.closeBrowsingContext', {'handle': self.webkit_context}, wait=True)
            self.devtools.close()
            self.devtools = None

    def prepare_browser(self, task):
        """Prepare the running browser (mobile emulation, UA string, etc"""
        if self.devtools is not None and not self.must_exit_now:
            # Move the WebKit window
            if self.is_webkit and not self.is_ios:
                result = self.devtools.send_command('Automation.getBrowsingContexts', {}, wait=True)
                if result and 'result' in result and 'contexts' in result['result']:
                    for context in result['result']['contexts']:
                        if 'handle' in context:
                            self.webkit_context = context['handle']
                            break
                if self.webkit_context is None:
                    result = self.devtools.send_command('Automation.createBrowsingContext', {}, wait=True)
                    if result is not None and 'result' in result and 'handle' in result['result']:
                        self.webkit_context = result['result']['handle']
                if self.webkit_context is not None:
                    self.devtools.send_command('Automation.switchToBrowsingContext', {'browsingContextHandle': self.webkit_context}, wait=True)
                    self.devtools.send_command('Automation.setWindowFrameOfBrowsingContext', {'handle': self.webkit_context, 'origin': {'x': 0, 'y': 0}, 'size': {'width': task['width'], 'height': task['height']}}, wait=True)
            # Figure out the native viewport size
            if not self.options.android:
                size = self.devtools.execute_js("[window.innerWidth, window.innerHeight]")
                if size is not None and len(size) == 2:
                    task['actual_viewport'] = {"width": size[0], "height": size[1]}
            # Clear the caches. Only needed on Android since we start with a completely clear profile for desktop
            if self.options.android:
                self.profile_start('dtbrowser.clear_cache')
                if not task['cached']:
                    self.devtools.send_command("Network.clearBrowserCache", {}, wait=True)
                    self.devtools.send_command("Network.clearBrowserCookies", {}, wait=True)
                self.profile_end('dtbrowser.clear_cache')
            
            # Disable image types
            disable_images = []
            if self.job.get('disableAVIF'):
                disable_images.append('avif')
            if self.job.get('disableWEBP'):
                disable_images.append('webp')
            if self.job.get('disableJXL'):
                disable_images.append('jxl')
            if len(disable_images):
                self.devtools.send_command("Emulation.setDisabledImageTypes", {"imageTypes": disable_images}, wait=True)
            
            # Mobile Emulation
            if not self.options.android and not self.is_webkit and \
                    'mobile' in self.job and self.job['mobile'] and \
                    'width' in self.job and 'height' in self.job and \
                    'dpr' in self.job:
                width = int(re.search(r'\d+', str(self.job['width'])).group())
                height = int(re.search(r'\d+', str(self.job['height'])).group())
                self.devtools.send_command("Emulation.setDeviceMetricsOverride",
                                           {"width": width,
                                            "height": height,
                                            "screenWidth": width,
                                            "screenHeight": height,
                                            "scale": 1,
                                            "positionX": 0,
                                            "positionY": 0,
                                            "deviceScaleFactor": float(self.job['dpr']),
                                            "mobile": True,
                                            "screenOrientation":
                                                {"angle": 0, "type": "portraitPrimary"}},
                                           wait=True)
                self.devtools.send_command("Emulation.setTouchEmulationEnabled",
                                           {"enabled": True,
                                            "configuration": "mobile"},
                                           wait=True)
                self.devtools.send_command("Emulation.setScrollbarsHidden",
                                           {"hidden": True},
                                           wait=True)

            # DevTools-based CPU throttling for desktop and emulated mobile tests
            # Lighthouse will provide the throttling directly
            if not self.options.android and not self.is_webkit and not task['running_lighthouse'] and 'throttle_cpu' in self.job:
                logging.debug('DevTools CPU Throttle target: %0.3fx', self.job['throttle_cpu'])
                if self.job['throttle_cpu'] > 1:
                    self.devtools.send_command("Emulation.setCPUThrottlingRate",
                                                {"rate": self.job['throttle_cpu']},
                                                wait=True)

            # Location
            if not self.is_webkit and 'lat' in self.job and 'lng' in self.job:
                try:
                    lat = float(str(self.job['lat']))
                    lng = float(str(self.job['lng']))
                    self.devtools.send_command(
                        'Emulation.setGeolocationOverride',
                        {'latitude': lat, 'longitude': lng,
                         'accuracy': 0})
                except Exception:
                    logging.exception('Error overriding location')

            # UA String
            ua_string = self.devtools.execute_js("navigator.userAgent")
            if ua_string is not None:
                if self.is_ios:
                    match = re.search(r'Version\/(\d+\.\d+\.\d+)', ua_string)
                elif self.is_webkit:
                    match = re.search(r'WebKit\/(\d+\.\d+\.\d+)', ua_string)
                else:
                    match = re.search(r'Chrome\/(\d+\.\d+\.\d+\.\d+)', ua_string)
                if match:
                    self.browser_version = match.group(1)
            if 'uastring' in self.job:
                if 'mobile' in self.job and self.job['mobile']:
                    # Replace the requested Chrome version with the actual Chrome version so Mobile emulation is always up to date
                    original_version = None
                    if ua_string is not None:
                        match = re.search(r'(Chrome\/\d+\.\d+\.\d+\.\d+)', ua_string)
                        if match:
                            original_version = match.group(1)
                    ua_string = self.job['uastring']
                    if original_version is not None:
                        match = re.search(r'(Chrome\/\d+\.\d+\.\d+\.\d+)', ua_string)
                        if match:
                            ua_string = ua_string.replace(match.group(1), original_version)
                else:
                    ua_string = self.job['uastring']
            if ua_string is not None and 'AppendUA' in task:
                ua_string += ' ' + task['AppendUA']
            if ua_string is not None:
                self.job['user_agent_string'] = ua_string

            # Global script injection (server-provided as well as any locally-defined scripts)
            if 'injectScript' in self.job and self.job.get('injectScriptAllFrames'):
                self.devtools.send_command("Page.addScriptToEvaluateOnNewDocument", {"source": self.job['injectScript']}, wait=True)
            inject_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'custom', 'inject')
            if (os.path.isdir(inject_dir)):
                files = glob.glob(inject_dir + '/*.js')
                for file in files:
                    try:
                        with open(file, 'rt') as f:
                            inject_script = f.read()
                            if inject_script:
                                self.devtools.send_command("Page.addScriptToEvaluateOnNewDocument", {"source": inject_script}, wait=True)
                    except Exception:
                        pass

            # Disable js
            if self.job['noscript'] and not self.is_webkit:
                self.devtools.send_command("Emulation.setScriptExecutionDisabled",
                                           {"value": True}, wait=True)
            self.devtools.prepare_browser()

    def on_start_recording(self, task):
        """Start recording"""
        if self.must_exit_now:
            return
        if 'page_data' not in task:
            task['page_data'] = {}
        task['page_data']['date'] = time.time()
        task['page_result'] = None
        task['run_start_time'] = monotonic()
        if self.browser_version is not None and 'browserVersion' not in task['page_data']:
            task['page_data']['browserVersion'] = self.browser_version
            task['page_data']['browser_version'] = self.browser_version
        if 'throttle_cpu' in self.job and not self.is_webkit:
            task['page_data']['throttle_cpu_requested'] = self.job['throttle_cpu_requested']
            if self.job['throttle_cpu'] > 1:
                task['page_data']['throttle_cpu'] = self.job['throttle_cpu']
        if self.devtools is not None:
            self.devtools.start_recording()

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        if self.devtools is not None:
            self.devtools.stop_capture()

    def on_stop_recording(self, task):
        """Stop recording"""
        if self.devtools is not None and not self.must_exit_now:
            self.devtools.collect_trace()
            if self.devtools_screenshot:
                if self.job['pngScreenShot']:
                    screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.png')
                    self.devtools.grab_screenshot(screen_shot, png=True)
                else:
                    screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
                    self.devtools.grab_screenshot(screen_shot, png=False, resize=600)
            # Stop recording dev tools
            self.devtools.stop_recording()
            # Collect end of test data from the browser
            self.collect_browser_metrics(task)

    def run_task(self, task):
        """Run an individual test"""
        if self.devtools is not None:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic() + task['test_time_limit']
            task['current_step'] = 1
            recording = False
            while len(task['script']) and task['error'] is None and monotonic() < end_time and not self.must_exit_now:
                self.prepare_task(task)
                command = task['script'].pop(0)
                if not recording and command['record']:
                    recording = True
                    self.on_start_recording(task)
                self.process_command(command)
                if command['record']:
                    self.devtools.wait_for_page_load()
                    if not task['combine_steps'] or not len(task['script']):
                        self.on_stop_capture(task)
                        self.on_stop_recording(task)
                        recording = False
                        self.on_start_processing(task)
                        self.wait_for_processing(task)
                        self.process_devtools_requests(task)
                        self.step_complete(task) #pylint: disable=no-member
                        if task['log_data']:
                            # Move on to the next step
                            task['current_step'] += 1
                            self.event_name = None
                    task['navigated'] = True
            self.task = None

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        if self.must_exit_now:
            return
        if task['log_data']:
            # Start the processing that can run in a background thread
            optimization = OptimizationChecks(self.job, task, self.get_requests(True))
            optimization.start()
            # Run the video post-processing
            if self.use_devtools_video and self.job['video']:
                self.process_video()
            if self.job.get('wappalyzer'):
                self.wappalyzer_detect(task, self.devtools.main_request_headers)
            if self.job.get('axe'):
                self.run_axe(task)

            # wait for the background optimization checks
            optimization.join()

    def wait_for_processing(self, task):
        """Wait for the background processing (if any)"""
        pass

    def execute_js(self, script):
        """Run javascipt"""
        ret = None
        if self.devtools is not None:
            ret = self.devtools.execute_js(script)
        return ret

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

    def process_video(self):
        """Post process the video"""
        if self.must_exit_now:
            return
        from internal.video_processing import VideoProcessing
        self.profile_start('dtbrowser.process_video')
        video = VideoProcessing(self.options, self.job, self.task)
        video.process()
        self.profile_end('dtbrowser.process_video')

    def process_devtools_requests(self, task):
        """Process the devtools log and pull out the requests information"""
        if self.must_exit_now:
            return
        self.profile_start('dtbrowser.process_devtools_requests')
        path_base = os.path.join(self.task['dir'], self.task['prefix'])
        devtools_file = path_base + '_devtools.json.gz'
        if os.path.isfile(devtools_file):
            from internal.support.devtools_parser import DevToolsParser
            out_file = path_base + '_devtools_requests.json.gz'
            options = {'devtools': devtools_file, 'cached': task['cached'], 'out': out_file}
            netlog = path_base + '_netlog_requests.json.gz'
            options['netlog'] = netlog if os.path.isfile(netlog) else None
            timeline_requests = path_base + '_timeline_requests.json.gz'
            options['requests'] = timeline_requests if os.path.isfile(timeline_requests) else None
            optimization = path_base + '_optimization.json.gz'
            options['optimization'] = optimization if os.path.isfile(optimization) else None
            user_timing = path_base + '_user_timing.json.gz'
            options['user'] = user_timing if os.path.isfile(user_timing) else None
            coverage = path_base + '_coverage.json.gz'
            options['coverage'] = coverage if os.path.isfile(coverage) else None
            cpu = path_base + '_timeline_cpu.json.gz'
            options['cpu'] = cpu if os.path.isfile(cpu) else None
            v8stats = path_base + '_v8stats.json.gz'
            options['v8stats'] = v8stats if os.path.isfile(v8stats) else None
            options['noheaders'] = False
            if 'noheaders' in self.job and self.job['noheaders']:
                options['noheaders'] = True
            parser = DevToolsParser(options)
            if 'metadata' in self.job:
                parser.metadata = self.job['metadata']
            parser.process()
            # Cleanup intermediate files that are not needed
            if 'debug' not in self.job or not self.job['debug']:
                if os.path.isfile(netlog):
                    os.remove(netlog)
                if os.path.isfile(timeline_requests):
                    os.remove(timeline_requests)
                if os.path.isfile(optimization):
                    os.remove(optimization)
                if os.path.isfile(coverage):
                    os.remove(coverage)
                if os.path.isfile(devtools_file):
                    os.remove(devtools_file)
            # remove files that might contain sensitive data
            if options['noheaders']:
                if os.path.isfile(netlog):
                    os.remove(netlog)
                if os.path.isfile(devtools_file):
                    os.remove(devtools_file)
                trace_file = path_base + '_trace.json.gz'
                if os.path.isfile(trace_file):
                    os.remove(trace_file)
            if 'page_data' in parser.result and 'result' in parser.result['page_data']:
                self.task['page_result'] = parser.result['page_data']['result']
        self.profile_end('dtbrowser.process_devtools_requests')

    def run_js_file(self, file_name):
        """Execute one of our js scripts"""
        if self.must_exit_now:
            return
        ret = None
        script = None
        script_file_path = os.path.join(self.script_dir, file_name)
        if os.path.isfile(script_file_path):
            with io.open(script_file_path, 'r', encoding='utf-8') as script_file:
                script = script_file.read()
        if script is not None:
            ret = self.devtools.execute_js(script)
        return ret

    def strip_non_text(self, data):
        """Strip any non-text fields"""
        if isinstance(data, dict):
            for key in data:
                entry = data[key]
                if isinstance(entry, dict) or isinstance(entry, list):
                    self.strip_non_text(entry)
                elif isinstance(entry, str) or isinstance(entry, unicode):
                    try:
                        if (sys.version_info >= (3, 0)):
                            entry.encode('utf-8').decode('utf-8')
                        else:
                            entry.decode('utf-8')
                    except Exception:
                        data[key] = None
                elif isinstance(entry, bytes):
                    try:
                        data[key] = str(entry.decode('utf-8'))
                    except Exception:
                        data[key] = None
        elif isinstance(data, list):
            for key in range(len(data)):
                entry = data[key]
                if isinstance(entry, dict) or isinstance(entry, list):
                    self.strip_non_text(entry)
                elif isinstance(entry, str) or isinstance(entry, unicode):
                    try:
                        if (sys.version_info >= (3, 0)):
                            entry.encode('utf-8').decode('utf-8')
                        else:
                            entry.decode('utf-8')
                    except Exception:
                        data[key] = None
                elif isinstance(entry, bytes):
                    try:
                        data[key] = str(entry.decode('utf-8'))
                    except Exception:
                        data[key] = None

    def get_sorted_requests_json(self, include_bodies):
        requests_json = None
        try:
            requests = []
            raw_requests = self.get_requests(include_bodies)
            for request_id in raw_requests:
                self.strip_non_text(raw_requests[request_id])
                requests.append(raw_requests[request_id])
            requests = sorted(requests, key=lambda request: request['sequence'])
            requests_json = json.dumps(requests)
        except Exception:
            logging.exception('Error getting json request data')
        if requests_json is None:
            requests_json = '[]'
        return requests_json

    def find_dom_node_info(self, dom_tree, node_id):
        """Get the information for the given DOM node"""
        info = None
        try:
            if dom_tree is not None:
                if 'documents' in dom_tree:
                    node_index = None
                    for document in dom_tree['documents']:
                        if 'nodes' in document and node_index is None:
                            if 'backendNodeId' in document['nodes']:
                                for index in range(len(document['nodes']['backendNodeId'])):
                                    if document['nodes']['backendNodeId'][index] == node_id:
                                        node_index = index
                                        break
                            if node_index is not None:
                                info = {}
                                if 'strings' in dom_tree:
                                    if 'nodeName' in document['nodes'] and node_index < len(document['nodes']['nodeName']):
                                        string_index = document['nodes']['nodeName'][node_index]
                                        if string_index >= 0 and string_index < len(dom_tree['strings']):
                                            info['nodeType'] = dom_tree['strings'][string_index]
                                    if 'nodeValue' in document['nodes'] and node_index < len(document['nodes']['nodeValue']):
                                        string_index = document['nodes']['nodeValue'][node_index]
                                        if string_index >= 0 and string_index < len(dom_tree['strings']):
                                            info['nodeValue'] = dom_tree['strings'][string_index]
                                    if 'attributes' in document['nodes'] and node_index < len(document['nodes']['attributes']):
                                        attribute = None
                                        for string_index in document['nodes']['attributes'][node_index]:
                                            string_value = ''
                                            if string_index >= 0 and string_index < len(dom_tree['strings']):
                                                string_value = dom_tree['strings'][string_index]
                                            if attribute is None:
                                                attribute = string_value
                                            else:
                                                if attribute:
                                                    if 'attributes' not in info:
                                                        info['attributes'] = {}
                                                    if attribute in info['attributes']:
                                                        info['attributes'][attribute] += ' ' + string_value
                                                    else:
                                                        info['attributes'][attribute] = string_value
                                                attribute = None
                                    if 'currentSourceURL' in document['nodes']:
                                        if 'index' in document['nodes']['currentSourceURL'] and 'value' in document['nodes']['currentSourceURL']:
                                            for index in range(len(document['nodes']['currentSourceURL']['index'])):
                                                if document['nodes']['currentSourceURL']['index'][index] == node_index:
                                                    if index < len(document['nodes']['currentSourceURL']['value']):
                                                        string_index = document['nodes']['currentSourceURL']['value'][index]
                                                        if string_index >= 0 and string_index < len(dom_tree['strings']):
                                                            info['sourceURL'] = dom_tree['strings'][string_index]
                                                    break
                                if 'layout' in document:
                                    if 'nodeIndex' in document['layout']:
                                        for index in range(len(document['layout']['nodeIndex'])):
                                            if document['layout']['nodeIndex'][index] == node_index:
                                                if 'bounds' in document['layout'] and index < len(document['layout']['bounds']):
                                                    info['bounds'] = document['layout']['bounds'][index]
                                                if 'text' in document['layout'] and index < len(document['layout']['text']):
                                                    string_index = document['layout']['text'][index]
                                                    if string_index >= 0 and string_index < len(dom_tree['strings']):
                                                        info['layoutText'] = dom_tree['strings'][string_index]
                                                if 'style_names' in dom_tree and 'styles' in document['layout'] and index < len(document['layout']['styles']) and len(document['layout']['styles'][index]) == len(dom_tree['style_names']):
                                                    if 'styles' not in info:
                                                        info['styles'] = {}
                                                    for style_index in range(len(document['layout']['styles'][index])):
                                                        string_index = document['layout']['styles'][index][style_index]
                                                        if string_index >= 0 and string_index < len(dom_tree['strings']):
                                                            info['styles'][dom_tree['style_names'][style_index]] = dom_tree['strings'][string_index]
                                return info
                                            
        except Exception:
            logging.exception("Error looking up DOM Node")
        return info

    def collect_browser_metrics(self, task):
        """Collect all of the in-page browser metrics that we need"""
        if self.must_exit_now:
            return
        if 'customMetrics' in self.job:
            custom_metrics = {}
            requests = None
            bodies = None
            accessibility_tree = None
            for name in sorted(self.job['customMetrics']):
                logging.debug('Collecting custom metric %s', name)
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
                if custom_script.find('$WPT_ACCESSIBILITY_TREE') >= 0:
                    if accessibility_tree is None:
                        try:
                            self.devtools.send_command('Accessibility.enable', {}, wait=True, timeout=30)
                            result = self.devtools.send_command('Accessibility.getFullAXTree', {}, wait=True, timeout=30)
                            if result is not None and 'result' in result and 'nodes' in result['result']:
                                tree = result['result']['nodes']
                                dom_tree = self.devtools.snapshot_dom()
                                # Populate the node details
                                if dom_tree is not None:
                                    for node in tree:
                                        if 'backendDOMNodeId' in node:
                                            node['node_info'] = self.find_dom_node_info(dom_tree, node['backendDOMNodeId'])
                                accessibility_tree = json.dumps(tree)
                            self.devtools.send_command('Accessibility.disable', {}, wait=True, timeout=30)
                        except Exception:
                            logging.exception('Error processing accessibility tree')
                    try:
                        custom_script = custom_script.replace('$WPT_ACCESSIBILITY_TREE', accessibility_tree)
                    except Exception:
                        logging.exception('Error substituting request data with bodies into custom script')
                script = 'var wptCustomMetric = function() {' + custom_script + '};try{wptCustomMetric();}catch(e){};'
                custom_metrics[name] = self.devtools.execute_js(script)
            path = os.path.join(task['dir'], task['prefix'] + '_metrics.json.gz')
            with gzip.open(path, GZIP_TEXT, 7) as outfile:
                outfile.write(json.dumps(custom_metrics))
        user_timing = self.run_js_file('user_timing.js')
        if user_timing is not None:
            path = os.path.join(task['dir'], task['prefix'] + '_timed_events.json.gz')
            with gzip.open(path, GZIP_TEXT, 7) as outfile:
                outfile.write(json.dumps(user_timing))
        page_data = self.run_js_file('page_data.js')
        self.document_domain = None
        if page_data is not None:
            if 'document_hostname' in page_data:
                self.document_domain = page_data['document_hostname']
            task['page_data'].update(page_data)

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.task['page_data']['URL'] = command['target']
            url = str(command['target']).replace('"', '\"')
            script = 'window.location="{0}";'.format(url)
            script = self.prepare_script_for_record(script) #pylint: disable=no-member
            # Set up permissions for the origin
            if not self.is_webkit:
                try:
                    parts = urlsplit(url)
                    origin = parts.scheme + '://' + parts.netloc
                    self.devtools.send_command('Browser.grantPermissions',
                                            {'origin': origin,
                                            'permissions': ['geolocation',
                                                            'videoCapture',
                                                            'audioCapture',
                                                            'sensors',
                                                            'idleDetection',
                                                            'wakeLockScreen']},
                                            wait=True)
                except Exception:
                    logging.exception('Error setting permissions for origin')
            self.devtools.start_navigating()
            self.devtools.execute_js(script)
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
                needs_mark = True
                if self.task['combine_steps']:
                    needs_mark = False
                if self.devtools.execution_context is not None:
                    # Clear the orange frame as a separate step to make sure it is done in the correct context
                    clear_script = self.prepare_script_for_record('', needs_mark)
                    self.devtools.execute_js(clear_script)
                else:
                    script = self.prepare_script_for_record(script, needs_mark) #pylint: disable=no-member
                self.devtools.start_navigating()
            self.devtools.execute_js(script, True)
        elif command['command'] == 'setexecutioncontext':
            self.devtools.set_execution_context(command['target'])
        elif command['command'] == 'sleep':
            available_sleep = 60 - self.total_sleep
            delay = min(available_sleep, max(0, int(re.search(r'\d+', str(command['target'])).group())))
            if delay > 0:
                self.total_sleep += delay
                time.sleep(delay)
        elif command['command'] == 'setabm':
            self.task['stop_at_onload'] = bool('target' in command and
                                               int(re.search(r'\d+',
                                                             str(command['target'])).group()) == 0)
        elif command['command'] == 'setactivitytimeout':
            if 'target' in command:
                milliseconds = int(re.search(r'\d+', str(command['target'])).group())
                self.task['activity_time'] = max(0, min(30, float(milliseconds) / 1000.0))
        elif command['command'] == 'setminimumstepseconds':
            self.task['minimumTestSeconds'] = int(re.search(r'\d+', str(command['target'])).group())
        elif command['command'] == 'setuseragent':
            self.job['user_agent_string'] = command['target']
        elif command['command'] == 'setcookie' and not self.is_webkit:
            if 'target' in command and 'value' in command:
                try:
                    url = command['target'].strip()
                    cookie = command['value']
                    pos = cookie.find(';')
                    if pos > 0:
                        cookie = cookie[:pos]
                    pos = cookie.find('=')
                    if pos > 0:
                        name = cookie[:pos].strip()
                        value = cookie[pos + 1:].strip()
                        if len(name) and len(value) and len(url):
                            self.devtools.send_command('Network.setCookie',
                                                    {'url': url, 'name': name, 'value': value})
                except Exception:
                    logging.exception('Error setting cookie')
        elif command['command'] == 'setlocation' and not self.is_webkit:
            try:
                if 'target' in command and command['target'].find(',') > 0:
                    accuracy = 0
                    if 'value' in command and re.match(r'\d+', command['value']):
                        accuracy = int(re.search(r'\d+', str(command['value'])).group())
                    parts = command['target'].split(',')
                    lat = float(parts[0])
                    lng = float(parts[1])
                    self.devtools.send_command(
                        'Emulation.setGeolocationOverride',
                        {'latitude': lat, 'longitude': lng,
                         'accuracy': accuracy})
            except Exception:
                logging.exception('Error setting location')
        elif command['command'] == 'addheader':
            self.devtools.set_header(command['target'])
        elif command['command'] == 'setheader':
            self.devtools.set_header(command['target'])
        elif command['command'] == 'resetheaders':
            self.devtools.reset_headers()
        elif command['command'] == 'clearcache':
            self.devtools.clear_cache()
        elif command['command'] == 'disablecache':
            disable_cache = bool('target' in command and \
                                 int(re.search(r'\d+',
                                               str(command['target'])).group()) == 1)
            self.devtools.disable_cache(disable_cache)
        elif command['command'] == 'type':
            self.devtools.type_text(command['target'])
        elif command['command'] == 'keypress':
            self.devtools.keypress(command['target'])
        elif command['command'] == 'waitfor':
            try:
                self.devtools.wait_for_script = command['target'] if command['target'] else None
            except Exception:
                logging.exception('Error processing waitfor command')
        elif command['command'] == 'waitinterval':
            try:
                interval = float(command['target'])
                if interval > 0:
                    self.devtools.wait_interval = interval
            except Exception:
                logging.exception('Error processing waitfor command')

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.devtools is not None:
            self.devtools.send_command('Page.navigate', {'url': url}, wait=True)

    def get_requests(self, include_bodies):
        """Get the request details for running an optimization check"""
        requests = None
        if self.devtools is not None:
            requests = self.devtools.get_requests(include_bodies)
        return requests

    def lighthouse_thread(self):
        """Run lighthouse in a thread so we can kill it if it times out"""
        cmd = self.lighthouse_command
        self.task['lighthouse_log'] = cmd + "\n"
        logging.debug(cmd)
        proc = subprocess.Popen(cmd, shell=True, stderr=subprocess.PIPE)
        for line in iter(proc.stderr.readline, b''):
            try:
                line = unicode(line,errors='ignore')
                logging.debug(line.rstrip())
                self.task['lighthouse_log'] += line
            except Exception:
                logging.exception('Error recording lighthouse log line %s', line.rstrip())
        proc.communicate()

    def run_lighthouse_test(self, task):
        if self.must_exit_now:
            return
        self.profile_start('dtbrowser.run_lighthouse_test')
        """Run a lighthouse test against the current browser session"""
        task['lighthouse_log'] = ''
        if 'url' in self.job and self.job['url'] is not None and not self.is_webkit:
            if not self.job['lighthouse_config'] and not self.job['dtShaper']:
                self.job['shaper'].configure(self.job, task)
            output_path = os.path.join(task['dir'], 'lighthouse.json')
            json_file = os.path.join(task['dir'], 'lighthouse.report.json')
            json_gzip = os.path.join(task['dir'], 'lighthouse.json.gz')
            html_file = os.path.join(task['dir'], 'lighthouse.report.html')
            html_gzip = os.path.join(task['dir'], 'lighthouse.html.gz')
            time_limit = min(int(task['time_limit']), 80)
            # see what version of lighthouse we are running
            lighthouse_version = 1
            try:
                out = subprocess.check_output('lighthouse --version', shell=True, universal_newlines=True)
                if out is not None and len(out):
                    match = re.search(r'^\d+', out)
                    if match:
                        lighthouse_version = int(match.group())
                logging.debug("Lighthouse version %d", lighthouse_version)
            except Exception:
                logging.exception('Error getting lighthouse version')
            command = ['lighthouse',
                       '"{0}"'.format(self.job['url']),
                       '--channel', 'wpt',
                       '--enable-error-reporting',
                       '--max-wait-for-load', str(int(time_limit * 1000)),
                       '--port', str(task['port']),
                       '--output', 'html',
                       '--output', 'json',
                       '--output-path', '"{0}"'.format(output_path)]
            if self.job['lighthouse_config']:
                try:
                    lighthouse_config_file = os.path.join(task['dir'], 'lighthouse-config.json')
                    with open(lighthouse_config_file, 'wt') as f_out:
                        json.dump(json.loads(self.job['lighthouse_config']), f_out)
                    command.extend(['--config-path', lighthouse_config_file])
                except Exception:
                    logging.exception('Error adding custom config for lighthouse test')
            else:
                cpu_throttle = '{:.3f}'.format(self.job['throttle_cpu']) if 'throttle_cpu' in self.job else '1'
                if self.job['dtShaper']:
                    command.extend(['--throttling-method', 'devtools', '--throttling.requestLatencyMs', '150', '--throttling.downloadThroughputKbps', '1600', '--throttling.uploadThroughputKbps', '768', '--throttling.cpuSlowdownMultiplier', cpu_throttle])
                elif 'throttle_cpu_requested' in self.job and self.job['throttle_cpu_requested'] > 1:
                    command.extend(['--throttling-method', 'devtools', '--throttling.requestLatencyMs', '0', '--throttling.downloadThroughputKbps', '0', '--throttling.uploadThroughputKbps', '0', '--throttling.cpuSlowdownMultiplier', cpu_throttle])
                else:
                    command.extend(['--throttling-method', 'provided'])
            if 'debug' in self.job and self.job['debug']:
                command.extend(['--verbose'])
            if self.job['keep_lighthouse_trace']:
                command.append('--save-assets')
            if not self.job['keep_lighthouse_screenshots']:
                command.extend(['--skip-audits', 'screenshot-thumbnails,final-screenshot,full-page-screenshot'])
            form_factor_command = '--form-factor'
            if self.options.android:
                command.extend([form_factor_command, 'mobile'])
                command.extend(['--screenEmulation.disabled'])
            elif 'mobile' not in self.job or not self.job['mobile']:
                command.extend([form_factor_command, 'desktop'])
                command.extend(['--screenEmulation.disabled'])
            if 'user_agent_string' in self.job:
                sanitized_user_agent = re.sub(r'[^a-zA-Z0-9_\-.;:/()\[\] ]+', '', self.job['user_agent_string'])
                command.append('--chrome-flags="--user-agent=\'{0}\'"'.format(sanitized_user_agent))
            if len(task['block']):
                for pattern in task['block']:
                    pattern = "'" + pattern.replace("'", "'\\''") + "'"
                    command.extend(['--blocked-url-patterns', pattern])
            if 'headers' in task:
                try:
                    headers_file = os.path.join(task['dir'], 'lighthouse-headers.json')
                    with open(headers_file, 'wt') as f_out:
                        json.dump(task['headers'], f_out)
                    command.extend(['--extra-headers', '"{0}"'.format(headers_file)])
                except Exception:
                    logging.exception('Error adding custom headers for lighthouse test')
            cmd = ' '.join(command)
            self.lighthouse_command = cmd
            # Give lighthouse up to 10 minutes to run all of the audits
            try:
                lh_thread = threading.Thread(target=self.lighthouse_thread)
                lh_thread.start()
                lh_thread.join(600)
            except Exception:
                logging.exception('Error running lighthouse audits')
            from .os_util import kill_all
            kill_all('node', True)
            self.job['shaper'].reset()
            # Rename and compress the trace file, delete the other assets
            if self.job['keep_lighthouse_trace']:
                try:
                    lh_trace_src = os.path.join(task['dir'], 'lighthouse-0.trace.json')
                    if os.path.isfile(lh_trace_src):
                        # read the JSON in and re-write it line by line to match the other traces
                        with io.open(lh_trace_src, 'r', encoding='utf-8') as f_in:
                            trace = json.load(f_in)
                            if trace is not None and 'traceEvents' in trace:
                                lighthouse_trace = os.path.join(task['dir'],
                                                                'lighthouse_trace.json.gz')
                            with gzip.open(lighthouse_trace, GZIP_TEXT, 7) as f_out:
                                f_out.write('{"traceEvents":[{}')
                                for trace_event in trace['traceEvents']:
                                    f_out.write(",\n")
                                    f_out.write(json.dumps(trace_event))
                                f_out.write("\n]}")
                except Exception:
                    logging.exception('Error processing lighthouse trace')
            # Delete all the left-over lighthouse assets
            files = glob.glob(os.path.join(task['dir'], 'lighthouse-*'))
            for file_path in files:
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            if os.path.isfile(json_file):
                lh_report = None
                with io.open(json_file, 'r', encoding='utf-8') as f_in:
                    lh_report = json.load(f_in)

                with open(json_file, 'rb') as f_in:
                    with gzip.open(json_gzip, 'wb', 7) as f_out:
                        shutil.copyfileobj(f_in, f_out)
                try:
                    os.remove(json_file)
                except Exception:
                    pass
                # Extract the audit scores
                if lh_report is not None:
                    audits = {}
                    # v1.x
                    if 'aggregations' in lh_report:
                        for entry in lh_report['aggregations']:
                            if 'name' in entry and 'total' in entry and \
                                    'scored' in entry and entry['scored']:
                                name = entry['name'].replace(' ', '')
                                audits[name] = entry['total']
                    # v2.x
                    elif 'reportCategories' in lh_report:
                        for category in lh_report['reportCategories']:
                            if 'name' in category and 'score' in category:
                                category_name = category['name'].replace(' ', '')
                                score = float(category['score']) / 100.0
                                audits[category_name] = score
                                if category['name'] == 'Performance' and 'audits' in category:
                                    for audit in category['audits']:
                                        if 'id' in audit and 'group' in audit and \
                                                audit['group'] == 'perf-metric' and \
                                                'result' in audit and \
                                                'rawValue' in audit['result']:
                                            name = category_name + '.' + \
                                                audit['id'].replace(' ', '')
                                            audits[name] = audit['result']['rawValue']
                    # v3.x
                    elif 'categories' in lh_report:
                        for categoryId in lh_report['categories']:
                            category = lh_report['categories'][categoryId]
                            if 'title' not in category or 'score' not in category:
                                continue

                            category_title = category['title'].replace(' ', '')
                            audits[category_title] = category['score']

                            if categoryId != 'performance' or 'auditRefs' not in category:
                                continue

                            for auditRef in category['auditRefs']:
                                if auditRef['id'] not in lh_report['audits']:
                                    continue
                                if 'group' not in auditRef or auditRef['group'] != 'metrics':
                                    continue
                                audit = lh_report['audits'][auditRef['id']]
                                name = category_title + '.' + audit['id']
                                if 'rawValue' in audit:
                                    audits[name] = audit['rawValue']
                                elif 'numericValue' in audit:
                                    audits[name] = audit['numericValue']
                    audits_gzip = os.path.join(task['dir'], 'lighthouse_audits.json.gz')
                    with gzip.open(audits_gzip, GZIP_TEXT, 7) as f_out:
                        json.dump(audits, f_out)
            # Compress the HTML lighthouse report
            if os.path.isfile(html_file):
                try:
                    with open(html_file, 'rb') as f_in:
                        with gzip.open(html_gzip, 'wb', 7) as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    os.remove(html_file)
                except Exception:
                    logging.exception('Error compressing lighthouse report')
        self.profile_end('dtbrowser.run_lighthouse_test')
    def run_axe(self, task):
        """Build the axe script to run in-browser"""
        self.profile_start('dtbrowser.axe_run')
        start = monotonic()
        script = None
        try:
            with open(os.path.join(self.support_path, 'axe', 'axe-core', 'axe.min.js')) as f_in:
                script = f_in.read()
            if script is not None:
                script += 'axe.run({runOnly:['
                axe_cats = self.job.get('axe_categories').split(',')
                script += "'" + "', '".join(axe_cats) + "'"
                script += ']}).then(results=>{return results;});'
        except Exception as err:
            logging.exception("Exception running Axe: %s", err.__str__())
        if self.must_exit_now:
            return
        completed = False
        if self.devtools is not None:
            try:
                logging.debug('run_axe')
                # Run the axe library (give it 30 seconds at most)
                response = self.devtools.send_command("Runtime.evaluate",
                                                {'expression': script,
                                                'awaitPromise': True,
                                                'returnByValue': True,
                                                'timeout': 30000},
                                                wait=True, timeout=30)
                if response is not None and 'result' in response and\
                        'result' in response['result'] and\
                        'value' in response['result']['result']:
                    result = response['result']['result']['value']
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

    def wappalyzer_detect(self, task, request_headers):
        """Run the wappalyzer detection"""
        if self.must_exit_now:
            return
        self.profile_start('dtbrowser.wappalyzer_detect')
        # Run the Wappalyzer detection (give it 30 seconds at most)
        completed = False
        if self.devtools is not None:
            try:
                logging.debug('wappalyzer_detect')
                cookies = {}
                response = self.devtools.send_command("Storage.getCookies", {}, wait=True, timeout=30)
                if response is not None and 'result' in response and 'cookies' in response['result']:
                    for cookie in response['result']['cookies']:
                        name = cookie['name'].lower()
                        if name not in cookies:
                            cookies[name] = []
                        cookies[name].append(cookie['value'])
                # Get the relavent DNS records for the origin
                dns = {}
                dns_types = ['cname', 'ns', 'mx', 'txt', 'soa', 'https', 'svcb']
                if self.document_domain is not None:
                    dns_domain = str(self.document_domain)
                    while dns_domain.find('.') > 0:
                        logging.debug('Wappalyzer resolving %s', dns_domain)
                        try:
                            from dns import resolver
                            dns_resolver = resolver.Resolver()
                            dns_resolver.timeout = 1
                            dns_resolver.lifetime = 1
                            for dns_type in dns_types:
                                if dns_type not in dns:
                                    try:
                                        result = []
                                        answer = dns_resolver.query(dns_domain, dns_type.upper(), raise_on_no_answer=False)
                                        for a in answer:
                                            result.append(str(a))
                                        if len(result):
                                            dns[dns_type] = result
                                            logging.debug('Wappalyzer DNS %s for %s: %s', dns_type, dns_domain, json.dumps(result))
                                    except Exception:
                                        logging.exception('Error doing wappalyzer DNS %s lookup for %s', dns_type, self.document_domain)
                        except Exception:
                            logging.exception('Error doing wappalyzer DNS lookup')
                        # Walk up a step in case we need to look up a parent-domain record
                        pos = dns_domain.find('.')
                        dns_domain = dns_domain[pos + 1:]
                task['page_data']['origin_dns'] = dns
                for dns_type in dns_types:
                    if dns_type not in dns:
                        dns[dns_type] = []
                logging.debug('Wappalyzer DNS for %s: %s', self.document_domain, json.dumps(dns))
                # Generate the wappalyzer script
                detect_script = self.wappalyzer_script(request_headers, cookies, dns)
                response = self.devtools.send_command("Runtime.evaluate",
                                                      {'expression': detect_script,
                                                       'awaitPromise': True,
                                                       'returnByValue': True,
                                                       'timeout': 30000},
                                                      wait=True, timeout=30)
                if response is not None and 'result' in response and\
                        'result' in response['result'] and\
                        'value' in response['result']['result']:
                    result = response['result']['result']['value']
                    if result:
                        completed = True
                        logging.debug(result)
                        detected = json.loads(result)
                        if 'categories' in detected:
                            task['page_data']['detected'] = dict(detected['categories'])
                        if 'apps' in detected:
                            task['page_data']['detected_apps'] = dict(detected['apps'])
                        if 'technologies' in detected:
                            task['page_data']['detected_technologies'] = dict(detected['technologies'])
                        if 'resolved' in detected:
                            task['page_data']['detected_raw'] = list(detected['resolved'])
            except Exception as err:
                logging.exception("Exception running Wappalyzer: %s", err.__str__())
        if not completed:
            task['page_data']['wappalyzer_failed'] = 1
        self.profile_end('dtbrowser.wappalyzer_detect')

    def wappalyzer_script(self, response_headers, cookies, dns):
        """Build the wappalyzer script to run in-browser"""
        script = None
        try:
            with open(os.path.join(self.support_path, 'Wappalyzer', 'script.js')) as f_in:
                script = f_in.read()
            if script is not None:
                wappalyzer = None
                with open(os.path.join(self.support_path, 'Wappalyzer', 'wappalyzer.js')) as f_in:
                    wappalyzer = f_in.read()
                if wappalyzer is not None:
                    technologies = {}
                    categories = {}
                    with io.open(os.path.join(self.support_path, 'Wappalyzer', 'categories.json'), 'r', encoding='utf-8') as f_in:
                        categories = json.load(f_in)
                    for filename in sorted(glob.glob(os.path.join(self.support_path, 'Wappalyzer', 'technologies', '*.json'))):
                        with io.open(filename, 'r', encoding='utf-8') as f_in:
                            technologies.update(json.load(f_in))
                    if technologies and categories:
                        # Format the headers as a dictionary of lists
                        headers = {}
                        if response_headers is not None:
                            if isinstance(response_headers, dict):
                                for key in response_headers:
                                    values = []
                                    entry = response_headers[key]
                                    if isinstance(entry, list):
                                        values = entry
                                    elif isinstance(entry, (str, unicode)):
                                        entries = entry.split('\n')
                                        for value in entries:
                                            values.append(value.strip())
                                    if values:
                                        headers[key.lower()] = values
                            elif isinstance(response_headers, list):
                                for pair in response_headers:
                                    if isinstance(pair, (str, unicode)):
                                        parts = pair.split(':', 1)
                                        key = parts[0].strip(' :\n\t').lower()
                                        value = parts[1].strip(' :\n\t')
                                        if key not in headers:
                                            headers[key] = []
                                        headers[key].append(value)
                        script = script.replace('%WAPPALYZER%', wappalyzer)
                        script = script.replace('%COOKIES%', json.dumps(cookies))
                        script = script.replace('%DNS%', json.dumps(dns))
                        script = script.replace('%RESPONSE_HEADERS%', json.dumps(headers))
                        script = script.replace('%CATEGORIES%', json.dumps(categories))
                        script = script.replace('%TECHNOLOGIES%', json.dumps(technologies))
        except Exception:
            logging.exception('Error building wappalyzer script')
        return script

    def profile_start(self, event_name):
        if self.task is not None and 'profile_data' in self.task:
            with self.task['profile_data']['lock']:
                self.task['profile_data'][event_name] = {'s': round(monotonic() - self.task['profile_data']['start'], 3)}

    def profile_end(self, event_name):
        if self.task is not None and 'profile_data' in self.task:
            with self.task['profile_data']['lock']:
                if event_name in self.task['profile_data']:
                    self.task['profile_data'][event_name]['e'] = round(monotonic() - self.task['profile_data']['start'], 3)
                    self.task['profile_data'][event_name]['d'] = round(self.task['profile_data'][event_name]['e'] - self.task['profile_data'][event_name]['s'], 3)
