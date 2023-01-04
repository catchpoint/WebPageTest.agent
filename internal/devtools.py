# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Main entry point for interfacing with Chrome's remote debugging protocol"""
import base64
import gzip
import io
import logging
import multiprocessing
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import zipfile
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
from ws4py.client.threadedclient import WebSocketClient


class DevTools(object):
    """Interface into Chrome's remote dev tools protocol"""
    def __init__(self, options, job, task, use_devtools_video, is_webkit, is_ios):
        self.url = "http://localhost:{0:d}/json".format(task['port'])
        self.must_exit = False
        self.websocket = None
        self.options = options
        self.job = job
        self.task = task
        self.is_webkit = is_webkit
        self.is_ios = is_ios
        self.command_id = 0
        self.command_responses = {}
        self.pending_body_requests = {}
        self.pending_commands = []
        self.console_log = []
        self.audit_issues = []
        self.performance_timing = []
        self.workers = []
        self.page_loaded = None
        self.main_frame = None
        self.response_started = False
        self.is_navigating = False
        self.last_activity = monotonic()
        self.dev_tools_file = None
        self.trace_file = None
        self.trace_enabled = False
        self.requests = {}
        self.netlog_requests = {}
        self.netlog_urls = {}
        self.netlog_lock = threading.Lock()
        self.request_count = 0
        self.response_bodies = {}
        self.body_fail_count = 0
        self.body_index = 0
        self.bodies_zip_file = None
        self.nav_error = None
        self.nav_error_code = None
        self.main_request = None
        self.main_request_headers = None
        self.start_timestamp = None
        self.path_base = None
        self.support_path = None
        self.video_path = None
        self.video_prefix = None
        self.recording = False
        self.mobile_viewport = None
        self.tab_id = None
        self.use_devtools_video = use_devtools_video
        self.recording_video = False
        self.main_thread_blocked = False
        self.stylesheets = {}
        self.headers = {}
        self.execution_contexts = {}
        self.execution_context = None
        self.trace_parser = None
        self.prepare()
        self.html_body = False
        self.all_bodies = False
        self.request_sequence = 0
        self.default_target = None
        self.dom_tree = None
        self.key_definitions = {}
        self.wait_interval = 5.0
        self.wait_for_script = None
        keyfile = os.path.join(os.path.dirname(__file__), 'support', 'keys.json')
        try:
            with open(keyfile, 'rt') as f_in:
                self.key_definitions = json.load(f_in)
        except Exception:
            logging.exception('Error loading keyboard definitions')

    def shutdown(self):
        """The agent is dying NOW"""
        self.must_exit = True

    def prepare(self):
        """Set up the various paths and states"""
        self.requests = {}
        self.request_count = 0
        self.response_bodies = {}
        self.console_log = []
        self.audit_issues = []
        self.performance_timing = []
        self.nav_error = None
        self.nav_error_code = None
        self.start_timestamp = None
        self.path_base = os.path.join(self.task['dir'], self.task['prefix'])
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
        self.video_path = os.path.join(self.task['dir'], self.task['video_subdirectory'])
        self.video_prefix = os.path.join(self.video_path, 'ms_')
        if not os.path.isdir(self.video_path):
            os.makedirs(self.video_path)
        self.body_fail_count = 0
        self.body_index = 0
        if self.bodies_zip_file is not None:
            self.bodies_zip_file.close()
            self.bodies_zip_file = None
        self.dom_tree = None
        self.html_body = False
        self.all_bodies = False
        if 'bodies' in self.job and self.job['bodies']:
            self.all_bodies = True
        if 'htmlbody' in self.job and self.job['htmlbody']:
            self.html_body = True

    def start_navigating(self):
        """Indicate that we are about to start a known-navigation"""
        self.main_frame = None
        self.is_navigating = True
        self.response_started = False

    def wait_for_available(self, timeout):
        """Wait for the dev tools interface to become available (but don't connect)"""
        import requests
        self.profile_start('devtools_start')
        proxies = {"http": None, "https": None}
        ret = False
        end_time = monotonic() + timeout
        while not ret and monotonic() < end_time and not self.must_exit:
            try:
                response = requests.get(self.url, timeout=timeout, proxies=proxies)
                if len(response.text):
                    tabs = response.json()
                    logging.debug("Dev Tools tabs: %s", json.dumps(tabs))
                    if len(tabs):
                        for index in range(len(tabs)):
                            if 'type' in tabs[index] and \
                                    (tabs[index]['type'] == 'page' or tabs[index]['type'] == 'webview') and \
                                    'webSocketDebuggerUrl' in tabs[index] and \
                                    'id' in tabs[index]:
                                ret = True
                                logging.debug('Dev tools interface is available')
            except Exception as err:
                logging.exception("Connect to dev tools Error: %s", err.__str__())
                time.sleep(0.5)
        self.profile_end('devtools_start')
        return ret

    def connect(self, timeout):
        """Connect to the browser"""
        self.profile_start('connect')
        if self.is_webkit and not self.is_ios:
            ret = False
            end_time = monotonic() + timeout
            while not ret and monotonic() < end_time and not self.must_exit:
                try:
                    self.websocket = WebKitGTKInspector()
                    self.websocket.connect(self.task['port'], timeout)
                    # Wait to get the targetCreated message
                    while self.default_target is None and monotonic() < end_time:
                        self.pump_message()
                    if self.default_target is not None:
                        ret = True
                except Exception:
                    logging.exception("Error connecting to webkit inspector")
                    time.sleep(0.5)
        else:
            import requests
            session = requests.session()
            proxies = {"http": None, "https": None}
            ret = False
            end_time = monotonic() + timeout
            while not ret and monotonic() < end_time and not self.must_exit:
                try:
                    response = session.get(self.url, timeout=timeout, proxies=proxies)
                    if len(response.text):
                        tabs = response.json()
                        logging.debug("Dev Tools tabs: %s", json.dumps(tabs))
                        if len(tabs):
                            websocket_url = None
                            for index in range(len(tabs)):
                                if 'type' in tabs[index]:
                                    if (tabs[index]['type'] == 'page' or tabs[index]['type'] == 'webview') and \
                                            'webSocketDebuggerUrl' in tabs[index] and \
                                            'id' in tabs[index]:
                                        if websocket_url is None:
                                            websocket_url = tabs[index]['webSocketDebuggerUrl']
                                            self.tab_id = tabs[index]['id']
                                        else:
                                            # Close extra tabs
                                            try:
                                                session.get(self.url + '/close/' + tabs[index]['id'], proxies=proxies)
                                            except Exception:
                                                logging.exception('Error closing tabs')
                                elif 'title' in tabs[index] and 'webSocketDebuggerUrl' in tabs[index]:
                                    if websocket_url is None and tabs[index]['title'] == 'Orange':
                                        websocket_url = tabs[index]['webSocketDebuggerUrl']
                            if websocket_url is not None:
                                try:
                                    self.websocket = DevToolsClient(websocket_url)
                                    self.websocket.connect()
                                    self.job['shaper'].set_devtools(self)
                                    ret = True
                                except Exception as err:
                                    logging.exception("Connect to dev tools websocket Error: %s", err.__str__())
                                if not ret:
                                    # try connecting to 127.0.0.1 instead of localhost
                                    try:
                                        websocket_url = websocket_url.replace('localhost', '127.0.0.1')
                                        self.websocket = DevToolsClient(websocket_url)
                                        self.websocket.connect()
                                        ret = True
                                    except Exception as err:
                                        logging.exception("Connect to dev tools websocket Error: %s", err.__str__())
                            else:
                                time.sleep(0.5)
                        else:
                            time.sleep(0.5)
                except Exception as err:
                    logging.debug("Connect to dev tools Error: %s", err.__str__())
                    time.sleep(0.5)
            # Wait for the default target to be created for iOS
            if ret and self.is_ios:
                while self.default_target is None and monotonic() < end_time and not self.must_exit:
                    self.pump_message()
        self.profile_end('connect')
        return ret

    def _to_int(self, s):
        return int(re.search(r'\d+', str(s)).group())

    def enable_shaper(self, target_id=None):
        """Enable the Chromium dev tools traffic shaping"""
        if self.job['dtShaper']:
            in_Bps = -1
            if 'bwIn' in self.job:
                in_Bps = (self._to_int(self.job['bwIn']) * 1000) / 8
            out_Bps = -1
            if 'bwOut' in self.job:
                out_Bps = (self._to_int(self.job['bwOut']) * 1000) / 8
            rtt = 0
            if 'latency' in self.job:
                rtt = self._to_int(self.job['latency'])
            self.send_command('Network.emulateNetworkConditions', {
                'offline': False,
                'latency': rtt,
                'downloadThroughput': in_Bps,
                'uploadThroughput': out_Bps
                }, wait=True, target_id=target_id)

    def enable_webkit_events(self):
        if self.is_webkit:
            self.send_command('Inspector.enable', {})
            self.send_command('Network.enable', {})
            self.send_command('Runtime.enable', {})
            self.job['shaper'].apply()
            self.enable_shaper()
            if self.headers:
                self.send_command('Network.setExtraHTTPHeaders', {'headers': self.headers})
            if len(self.workers):
                for target in self.workers:
                    self.enable_target(target['targetId'])
            if 'user_agent_string' in self.job:
                self.send_command('Page.overrideUserAgent', {'value': self.job['user_agent_string']})
            if self.task['log_data']:
                self.send_command('Console.enable', {})
                self.send_command('Timeline.start', {}, wait=True)
            self.send_command('Page.enable', {}, wait=True)

    def prepare_browser(self):
        """Run any one-time startup preparation before testing starts"""
        if self.is_webkit:
            self.send_command('Target.setPauseOnStart', {'pauseOnStart': True}, wait=True)
        else:
            self.send_command('Target.setAutoAttach',
                            {'autoAttach': True, 'waitForDebuggerOnStart': True})
            response = self.send_command('Target.getTargets', {}, wait=True)
            if response is not None and 'result' in response and 'targetInfos' in response['result']:
                for target in response['result']['targetInfos']:
                    logging.debug(target)
                    if 'type' in target and 'targetId' in target:
                        if target['type'] == 'service_worker':
                            self.send_command('Target.attachToTarget', {'targetId': target['targetId']},
                                            wait=True)

    def close(self, close_tab=True):
        """Close the dev tools connection"""
        self.job['shaper'].set_devtools(None)
        if self.websocket:
            try:
                self.websocket.close()
            except Exception:
                logging.exception('Error closing websocket')
            self.websocket = None
        if close_tab and self.tab_id is not None:
            import requests
            proxies = {"http": None, "https": None}
            try:
                requests.get(self.url + '/close/' + self.tab_id, proxies=proxies)
            except Exception:
                logging.exception('Error closing tab')
        self.tab_id = None

    def start_recording(self):
        """Start capturing dev tools, timeline and trace data"""
        self.profile_start('prepare_chrome')
        self.prepare()
        if (self.bodies_zip_file is None and (self.html_body or self.all_bodies)):
            self.bodies_zip_file = zipfile.ZipFile(self.path_base + '_bodies.zip', 'w',
                                                   zipfile.ZIP_DEFLATED)
        self.recording = True
        if self.use_devtools_video and self.job['video'] and self.task['log_data']:
            self.grab_screenshot(self.video_prefix + '000000.jpg', png=False)
        self.flush_pending_messages()
        self.send_command('Page.enable', {})
        self.send_command('Inspector.enable', {})
        self.send_command('Debugger.enable', {})
        self.send_command('Debugger.setSkipAllPauses', {'skip': True})
        self.send_command('ServiceWorker.enable', {})
        self.send_command('DOMSnapshot.enable', {})
        inject_file_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support', 'chrome', 'inject.js')
        if os.path.isfile(inject_file_path):
            with io.open(inject_file_path, 'r', encoding='utf-8') as inject_file:
                inject_script = inject_file.read()
                self.send_command('Page.addScriptToEvaluateOnNewDocument', {'source': inject_script})
        self.enable_webkit_events()
        self.enable_target()
        if len(self.workers):
            for target in self.workers:
                self.enable_target(target['targetId'])
        if self.task['log_data']:
            self.send_command('Security.enable', {})
            if 'coverage' in self.job and self.job['coverage']:
                self.send_command('DOM.enable', {})
                self.send_command('CSS.enable', {})
                self.send_command('CSS.startRuleUsageTracking', {})
                self.send_command('Profiler.enable', {})
                self.send_command('Profiler.setSamplingInterval', {'interval': 100})
                self.send_command('Profiler.start', {})
            trace_config = {"recordMode": "recordAsMuchAsPossible",
                            "includedCategories": []}
            if 'trace' in self.job and self.job['trace']:
                self.job['keep_netlog'] = True
                if 'traceCategories' in self.job:
                    categories = self.job['traceCategories'].split(',')
                    for category in categories:
                        if category.find("*") < 0 and category not in trace_config["includedCategories"]:
                            trace_config["includedCategories"].append(category)
                else:
                    trace_config["includedCategories"] = [
                        "toplevel",
                        "blink",
                        "v8",
                        "cc",
                        "gpu",
                        "blink.net",
                        "blink.resource",
                        "disabled-by-default-v8.runtime_stats"
                    ]
            else:
                self.job['keep_netlog'] = False
            if 'netlog' in self.job and self.job['netlog']:
                self.job['keep_netlog'] = True
            if 'timeline' in self.job and self.job['timeline']:
                if self.is_webkit:
                    from internal.support.trace_parser import Trace
                    self.trace_parser = Trace()
                    self.trace_parser.cpu['main_thread'] = '0'
                    self.trace_parser.threads['0'] = {}
                if "blink.console" not in trace_config["includedCategories"]:
                    trace_config["includedCategories"].append("blink.console")
                if "devtools.timeline" not in trace_config["includedCategories"]:
                    trace_config["includedCategories"].append("devtools.timeline")
                if 'timeline_fps' in self.job and self.job['timeline_fps']:
                    if "disabled-by-default-devtools.timeline" not in trace_config["includedCategories"]:
                        trace_config["includedCategories"].append("disabled-by-default-devtools.timeline")
                    if "disabled-by-default-devtools.timeline.frame" not in trace_config["includedCategories"]:
                        trace_config["includedCategories"].append("disabled-by-default-devtools.timeline.frame")
                if 'profiler' in self.job and self.job['profiler']:
                    trace_config["enableSampling"] = True
                    if "disabled-by-default-v8.cpu_profiler" not in trace_config["includedCategories"]:
                        trace_config["includedCategories"].append("disabled-by-default-v8.cpu_profiler")
                    if "disabled-by-default-devtools.timeline" not in trace_config["includedCategories"]:
                        trace_config["includedCategories"].append("disabled-by-default-devtools.timeline")
                    if "disabled-by-default-devtools.timeline.frame" not in trace_config["includedCategories"]:
                        trace_config["includedCategories"].append("disabled-by-default-devtools.timeline.frame")
            if 'v8rcs' in self.job and self.job['v8rcs']:
                if "v8" not in trace_config["includedCategories"]:
                    trace_config["includedCategories"].append("v8")
                if "disabled-by-default-v8.runtime_stats" not in trace_config["includedCategories"]:
                    trace_config["includedCategories"].append("disabled-by-default-v8.runtime_stats")
            if self.use_devtools_video and self.job['video']:
                if "disabled-by-default-devtools.screenshot" not in trace_config["includedCategories"]:
                    trace_config["includedCategories"].append("disabled-by-default-devtools.screenshot")
                self.recording_video = True
            # Add the required trace events
            if "rail" not in trace_config["includedCategories"]:
                trace_config["includedCategories"].append("rail")
            if "content" not in trace_config["includedCategories"]:
                trace_config["includedCategories"].append("content")
                self.job['discard_trace_content'] = True
            if "loading" not in trace_config["includedCategories"]:
                trace_config["includedCategories"].append("loading")
            if "blink.user_timing" not in trace_config["includedCategories"]:
                trace_config["includedCategories"].append("blink.user_timing")
            if "netlog" not in trace_config["includedCategories"] and not self.job.get('streaming_netlog'):
                trace_config["includedCategories"].append("netlog")
            if "disabled-by-default-netlog" not in trace_config["includedCategories"] and not self.job.get('streaming_netlog'):
                trace_config["includedCategories"].append("disabled-by-default-netlog")
            if "blink.resource" not in trace_config["includedCategories"]:
                trace_config["includedCategories"].append("blink.resource")
            if "disabled-by-default-blink.feature_usage" not in trace_config["includedCategories"]:
                trace_config["includedCategories"].append("disabled-by-default-blink.feature_usage")
            if not self.is_webkit:
                self.trace_enabled = True
                self.send_command('Tracing.start', {'traceConfig': trace_config}, wait=True)
        now = monotonic()
        if not self.task['stop_at_onload']:
            self.last_activity = now
        if self.page_loaded is not None:
            self.page_loaded = now
        self.profile_end('prepare_chrome')

    def stop_capture(self):
        """Do any quick work to stop things that are capturing data"""
        if self.must_exit:
            return
        self.start_collecting_trace()
        # Process messages for up to 10 seconds in case we still have some pending async commands
        self.wait_for_pending_commands(10)

    def stop_recording(self):
        """Stop capturing dev tools, timeline and trace data"""
        if self.must_exit:
            return
        self.profile_start('stop_recording')
        if self.task['log_data']:
            if 'coverage' in self.job and self.job['coverage']:
                try:
                    coverage = {}
                    # process the JS coverage
                    self.send_command('Profiler.stop', {})
                    response = self.send_command('Profiler.getBestEffortCoverage', {}, wait=True, timeout=30)
                    if 'result' in response and 'result' in response['result']:
                        for script in response['result']['result']:
                            if 'url' in script and script['url'] and 'functions' in script:
                                if script['url'] not in coverage:
                                    coverage[script['url']] = {}
                                if 'JS' not in coverage[script['url']]:
                                    coverage[script['url']]['JS'] = []
                                for function in script['functions']:
                                    if 'ranges' in function:
                                        for chunk in function['ranges']:
                                            coverage[script['url']]['JS'].append({
                                                'startOffset': chunk['startOffset'],
                                                'endOffset': chunk['endOffset'],
                                                'count': chunk['count'],
                                                'used': True if chunk['count'] else False
                                            })
                    self.send_command('Profiler.disable', {})
                    # Process the css coverage
                    response = self.send_command('CSS.stopRuleUsageTracking', {}, wait=True, timeout=30)
                    if 'result' in response and 'ruleUsage' in response['result']:
                        rule_usage = response['result']['ruleUsage']
                        for rule in rule_usage:
                            if 'styleSheetId' in rule and rule['styleSheetId'] in self.stylesheets:
                                sheet_id = rule['styleSheetId']
                                url = self.stylesheets[sheet_id]
                                if url not in coverage:
                                    coverage[url] = {}
                                if 'CSS' not in coverage[url]:
                                    coverage[url]['CSS'] = []
                                coverage[url]['CSS'].append({
                                    'startOffset': rule['startOffset'],
                                    'endOffset': rule['endOffset'],
                                    'used': rule['used']
                                })
                    if coverage:
                        summary = {}
                        categories = ['JS', 'CSS']
                        for url in coverage:
                            for category in categories:
                                if category in coverage[url]:
                                    total_bytes = 0
                                    used_bytes = 0
                                    for chunk in coverage[url][category]:
                                        range_bytes = chunk['endOffset'] - chunk['startOffset']
                                        if range_bytes > 0:
                                            total_bytes += range_bytes
                                            if chunk['used']:
                                                used_bytes += range_bytes
                                    used_pct = 100.0
                                    if total_bytes > 0:
                                        used_pct = float((used_bytes * 10000) / total_bytes) / 100.0
                                        if url not in summary:
                                            summary[url] = {}
                                        summary[url]['{0}_bytes'.format(category)] = total_bytes
                                        summary[url]['{0}_bytes_used'.format(category)] = used_bytes
                                        summary[url]['{0}_percent_used'.format(category)] = used_pct
                        path = self.path_base + '_coverage.json.gz'
                        with gzip.open(path, GZIP_TEXT, 7) as f_out:
                            json.dump(summary, f_out)
                    self.send_command('CSS.disable', {})
                    self.send_command('DOM.disable', {})
                except Exception:
                    logging.exception('Error stopping devtools')
        self.recording = False
        # Process messages for up to 10 seconds in case we still have some pending async commands
        self.wait_for_pending_commands(10)
        self.flush_pending_messages()
        if self.task['log_data']:
            self.send_command('Security.disable', {})
            self.send_command('Audits.disable', {})
            self.send_command('Log.disable', {})
            self.send_command('Log.stopViolationsReport', {})
            self.send_command('Console.disable', {})
            self.send_command('Timeline.stop', {})
            self.get_response_bodies()
        if self.bodies_zip_file is not None:
            self.bodies_zip_file.close()
            self.bodies_zip_file = None
        self.send_command('Network.disable', {})
        if len(self.workers):
            for target in self.workers:
                self.send_command('Network.disable', {}, target_id=target['targetId'])
        self.send_command('ServiceWorker.disable', {})
        if self.dev_tools_file is not None:
            self.dev_tools_file.write("\n]")
            self.dev_tools_file.close()
            self.dev_tools_file = None
        # Save the console logs
        log_file = self.path_base + '_console_log.json.gz'
        with gzip.open(log_file, GZIP_TEXT, 7) as f_out:
            json.dump(self.console_log, f_out)
        self.send_command('Inspector.disable', {})
        self.send_command('Page.disable', {})
        self.send_command('Debugger.disable', {})
        # Add the audit issues to the page data
        if len(self.audit_issues):
            self.task['page_data']['audit_issues'] = self.audit_issues
        # Add the list of execution contexts
        contexts = []
        for id in self.execution_contexts:
            contexts.append(self.execution_contexts[id])
        if len(contexts):
            self.task['page_data']['execution_contexts'] = contexts
        # Process the timeline data
        if self.trace_parser is not None:
            start = monotonic()
            logging.debug("Processing the trace timeline events")
            self.trace_parser.ProcessTimelineEvents()
            self.trace_parser.WriteCPUSlices(self.path_base + '_timeline_cpu.json.gz')
            self.trace_parser.WriteScriptTimings(self.path_base + '_script_timing.json.gz')
            self.trace_parser.WriteInteractive(self.path_base + '_interactive.json.gz')
            self.trace_parser.WriteLongTasks(self.path_base + '_long_tasks.json.gz')
            elapsed = monotonic() - start
            logging.debug("Done processing the trace events: %0.3fs", elapsed)
            self.trace_parser = None
        self.profile_end('stop_recording')

    def wait_for_pending_commands(self, timeout):
        """Wait for any queued commands"""
        end_time = monotonic() + timeout
        while monotonic() < end_time and (len(self.pending_body_requests) or len(self.pending_commands))and not self.must_exit:
            try:
                self.pump_message()
            except Exception:
                pass

    def pump_message(self):
        """ Run the message pump """
        try:
            raw = self.websocket.get_message(1)
            try:
                if raw is not None and len(raw):
                    if raw.find("Timeline.eventRecorded") == -1 and raw.find("Target.dispatchMessageFromTarget") == -1 and raw.find("Target.receivedMessageFromTarget") == -1:
                        logging.debug('<- %s', raw[:200])
                    msg = json.loads(raw)
                    self.process_message(msg)
            except Exception:
                logging.exception('Error processing websocket message')
        except Exception:
            pass

    def start_collecting_trace(self):
        """Kick off the trace processing asynchronously"""
        if self.trace_enabled and not self.must_exit:
            keep_timeline = True
            if 'discard_timeline' in self.job and self.job['discard_timeline']:
                keep_timeline = False
            video_prefix = self.video_prefix if self.recording_video else None
            self.snapshot_dom()
            self.websocket.start_processing_trace(self.path_base, video_prefix,
                                                  self.options, self.job, self.task,
                                                  self.start_timestamp, keep_timeline, self.dom_tree, self.performance_timing)
            self.send_command('Tracing.end', {})

    def snapshot_dom(self):
        """Grab a snapshot of the DOM to use for processing element locations"""
        if self.dom_tree is not None:
            return self.dom_tree
        if self.must_exit:
            return
        try:
            self.profile_start('snapshot_dom')
            styles = ['background-image']
            response = self.send_command('DOMSnapshot.captureSnapshot', {'computedStyles': styles, 'includePaintOrder': False, 'includeDOMRects': True}, wait=True)
            if response and 'result' in response:
                self.dom_tree = response['result']
                self.dom_tree['style_names'] = styles
            self.profile_end('snapshot_dom')
        except Exception:
            logging.exception("Error capturing DOM snapshot")
        return self.dom_tree

    def collect_trace(self):
        """Stop tracing and collect the results"""
        if self.must_exit:
            return
        if self.trace_enabled:
            self.trace_enabled = False
            self.profile_start('collect_trace')
            start = monotonic()
            try:
                # Keep pumping messages until we get tracingComplete or
                # we get a gap of 30 seconds between messages
                if self.websocket:
                    logging.info('Collecting trace events')
                    no_message_count = 0
                    while not self.websocket.trace_done and no_message_count < 30 and monotonic() - start < 600:
                        try:
                            raw = self.websocket.get_message(1)
                            try:
                                if raw is not None and len(raw):
                                    no_message_count = 0
                                else:
                                    no_message_count += 1
                            except Exception:
                                no_message_count += 1
                                logging.exception('Error processing devtools message')
                        except Exception:
                            no_message_count += 1
                            time.sleep(1)
                self.websocket.stop_processing_trace(self.job)
            except Exception:
                logging.exception('Error processing trace events')
            elapsed = monotonic() - start
            self.profile_end('collect_trace')
            logging.debug("Time to collect trace: %0.3f sec", elapsed)
            self.recording_video = False

    def get_response_body(self, request_id, wait):
        """Retrieve and store the given response body (if necessary)"""
        if request_id not in self.response_bodies and self.body_fail_count < 3 and not self.is_ios and not self.must_exit:
            request = self.get_request(request_id, True)
            # See if we have a netlog-based response body
            found = False
            if request is not None and 'url' in request:
                try:
                    path = os.path.join(self.task['dir'], 'netlog_bodies')
                    with self.netlog_lock:
                        if request['url'] in self.netlog_urls:
                            for netlog_id in self.netlog_urls[request['url']]:
                                if netlog_id in self.netlog_requests and 'body_claimed' not in self.netlog_requests[netlog_id]:
                                    body_file_path = os.path.join(path, netlog_id)
                                    if os.path.exists(body_file_path):
                                        self.netlog_requests[netlog_id]['body_claimed'] = True
                                        found = True
                                        logging.debug('Matched netlog response body %s to %s for %s', netlog_id, request_id, request['url'])
                                        # For text-based responses, ignore any utf-8 decode errors so we can err on the side of getting more text bodies
                                        errors=None
                                        try:
                                            if 'response_headers' in self.netlog_requests[netlog_id]:
                                                headers = self.extract_headers(self.netlog_requests[netlog_id]['response_headers'])
                                                content_type = self.get_header_value(headers, 'Content-Type')
                                                if content_type is not None:
                                                    content_type = content_type.lower()
                                                    text_types = ['application/json',
                                                                  'application/xhtml+xml',
                                                                  'application/xml',
                                                                  'application/ld+json',
                                                                  'application/javascript']
                                                    if content_type.startswith('text/') or content_type in text_types:
                                                        errors = 'ignore'
                                        except Exception:
                                            logging.exception('Error processing content type for response body')
                                        self.process_response_body(request_id, None, body_file_path, errors)
                    if not found and len(self.netlog_requests):
                        logging.debug('Unable to match netlog response body for %s', request['url'])
                except Exception:
                    logging.exception('Error matching netlog response body')
            if not found and request is not None and 'status' in request and request['status'] == 200 and \
                    'response_headers' in request and 'url' in request and request['url'].startswith('http'):
                content_length = self.get_header_value(request['response_headers'], 'Content-Length')
                if content_length is not None:
                    content_length = int(re.search(r'\d+', str(content_length)).group())
                elif 'transfer_size' in request:
                    content_length = request['transfer_size']
                else:
                    content_length = 0
                logging.debug('Getting body for %s (%d) - %s', request_id,
                            content_length, request['url'])
                path = os.path.join(self.task['dir'], 'bodies')
                if not os.path.isdir(path):
                    os.makedirs(path)
                body_file_path = os.path.join(path, request_id)
                if not os.path.exists(body_file_path):
                    # Only grab bodies needed for optimization checks
                    # or if we are saving full bodies
                    need_body = True
                    content_type = self.get_header_value(request['response_headers'], 'Content-Type')
                    if content_type is not None:
                        content_type = content_type.lower()
                        # Ignore video files over 10MB
                        if content_type[:6] == 'video/' and content_length > 10000000:
                            need_body = False
                    optimization_checks_disabled = bool('noopt' in self.job and self.job['noopt'])
                    if optimization_checks_disabled and self.bodies_zip_file is None:
                        need_body = False
                    if need_body:
                        target_id = None
                        if request_id in self.requests and 'targetId' in self.requests[request_id]:
                            target_id = self.requests[request_id]['targetId']
                        response = self.send_command("Network.getResponseBody", {'requestId': request_id}, wait=wait, target_id=target_id)
                        if wait:
                            self.process_response_body(request_id, response)

    def process_response_body(self, request_id, response, netlog_body_file=None, errors=None):
        try:
            request = self.get_request(request_id, True)
            path = os.path.join(self.task['dir'], 'bodies')
            if not os.path.isdir(path):
                os.makedirs(path)
            body_file_path = os.path.join(path, request_id)
            is_text = False
            body = None
            if netlog_body_file is not None:
                try:
                    with open(netlog_body_file, 'r', encoding='utf-8', errors=errors) as f:
                        body = f.read()
                        body = body.encode('utf-8')
                        is_text = True
                except Exception:
                    pass
                if body is None:
                    with open(netlog_body_file, 'rb') as f:
                        body = f.read()
            elif not os.path.exists(body_file_path):
                is_text = False
                if request is not None and 'status' in request and request['status'] == 200 and 'response_headers' in request:
                    content_type = self.get_header_value(request['response_headers'], 'Content-Type')
                    if content_type is not None:
                        content_type = content_type.lower()
                        if content_type.startswith('text/') or \
                                content_type.find('javascript') >= 0 or \
                                content_type.find('json') >= 0 or \
                                content_type.find('/svg+xml'):
                            is_text = True
                if response is None:
                    self.body_fail_count += 1
                    logging.warning('No response to body request for request %s',
                                    request_id)
                elif 'result' not in response or \
                        'body' not in response['result']:
                    self.body_fail_count = 0
                    logging.warning('Missing response body for request %s',
                                    request_id)
                elif len(response['result']['body']):
                    try:
                        self.body_fail_count = 0
                        # Write the raw body to a file (all bodies)
                        if 'base64Encoded' in response['result'] and \
                                response['result']['base64Encoded']:
                            body = base64.b64decode(response['result']['body'])
                            is_text = False
                        else:
                            body = response['result']['body'].encode('utf-8')
                            is_text = True
                    except Exception:
                        logging.exception('Exception retrieving body')
                else:
                    self.body_fail_count = 0
                    self.response_bodies[request_id] = response['result']['body']
            # Store the actual body for processing
            if body is not None and not os.path.exists(body_file_path):
                if 'request_headers' in request:
                    fetch_dest = self.get_header_value(request['request_headers'], 'Sec-Fetch-Dest')
                    if fetch_dest is not None and fetch_dest in ['audio', 'audioworklet', 'font', 'image', 'object', 'track', 'video']:
                        is_text = False
                # Add text bodies to the zip archive
                store_body = self.all_bodies
                if self.html_body and request_id == self.main_request:
                    store_body = True
                if store_body and self.bodies_zip_file is not None and is_text:
                    self.body_index += 1
                    name = '{0:03d}-{1}-body.txt'.format(self.body_index, request_id)
                    self.bodies_zip_file.writestr(name, body)
                    logging.debug('%s: Stored body in zip', request_id)
                logging.debug('%s: Body length: %d', request_id, len(body))
                self.response_bodies[request_id] = body
                with open(body_file_path, 'wb') as body_file:
                    body_file.write(body)
        except Exception:
            logging.exception('Error processing response body')

    def get_response_bodies(self):
        """Retrieve all of the response bodies for the requests that we know about"""
        if self.must_exit:
            return
        self.profile_start('get_response_bodies')
        requests = self.get_requests(True)
        if self.task['error'] is None and requests:
            for request_id in requests:
                self.get_response_body(request_id, True)
        self.profile_end('get_response_bodies')

    def get_request(self, request_id, include_bodies):
        """Get the given request details if it is a real request"""
        request = None
        if request_id in self.requests and 'fromNet' in self.requests[request_id] and self.requests[request_id]['fromNet']:
            events = self.requests[request_id]
            request = {'id': request_id}
            if 'sequence' not in events:
                self.request_sequence += 1
                events['sequence'] = self.request_sequence
            request['sequence'] = events['sequence']
            # See if we have a body
            if include_bodies:
                body_path = os.path.join(self.task['dir'], 'bodies')
                body_file_path = os.path.join(body_path, request_id)
                if os.path.isfile(body_file_path):
                    request['body'] = body_file_path
                if request_id in self.response_bodies:
                    request['response_body'] = self.response_bodies[request_id]
            # Get the headers from responseReceived
            if 'response' in events:
                response = events['response'][-1]
                if 'response' in response:
                    fields = ['url', 'status', 'connectionId', 'protocol', 'connectionReused',
                              'fromServiceWorker', 'timing', 'fromDiskCache', 'remoteIPAddress',
                              'remotePort', 'securityState', 'securityDetails', 'fromPrefetchCache']
                    for field in fields:
                        if field in response['response']:
                            request[field] = response['response'][field]
                    if 'headers' in response['response']:
                        request['response_headers'] = response['response']['headers']
                    if 'requestHeaders' in response['response']:
                        request['request_headers'] = response['response']['requestHeaders']
            if 'requestExtra' in events:
                extra = events['requestExtra']
                if 'headers' in extra:
                    request['request_headers'] = extra['headers']
            if 'responseExtra' in events:
                extra = events['responseExtra']
                if 'headers' in extra:
                    request['response_headers'] = extra['headers']
            # Fill in any missing details from the requestWillBeSent event
            if 'request' in events:
                req = events['request'][-1]
                fields = ['initiator', 'documentURL', 'timestamp', 'frameId', 'hasUserGesture',
                          'type', 'wallTime']
                for field in fields:
                    if field in req and field not in request:
                        request[field] = req[field]
                if 'request' in req:
                    if 'url' not in request and 'url' in req['request']:
                        request['url'] = req['request']['url']
                    if 'request_headers' not in request and 'headers' in req['request']:
                        request['request_headers'] = req['request']['headers']
            # Get the response length from the data events
            if 'finished' in events and 'encodedDataLength' in events['finished']:
                request['transfer_size'] = events['finished']['encodedDataLength']
            elif 'data' in events:
                transfer_size = 0
                for data in events['data']:
                    if 'encodedDataLength' in data:
                        transfer_size += data['encodedDataLength']
                    elif 'dataLength' in data:
                        transfer_size += data['dataLength']
                request['transfer_size'] = transfer_size
        return request

    def extract_headers(self, raw_headers):
        """Convert flat headers into a keyed dictionary"""
        headers = {}
        for header in raw_headers:
            key_len = header.find(':', 1)
            if key_len >= 0:
                key = header[:key_len].strip(' :')
                value = header[key_len + 1:].strip()
                if key in headers:
                    headers[key] += ',' + value
                else:
                    headers[key] = value
        return headers

    def get_requests(self, include_bodies):
        """Get a dictionary of all of the requests and the details (headers, body file)"""
        requests = None
        if self.requests:
            for request_id in self.requests:
                request = self.get_request(request_id, include_bodies)
                if request is not None:
                    if requests is None:
                        requests = {}
                    requests[request_id] = request
        # Patch-in any netlog requests that were not seen through dev tools
        # This is only used for optimization checks and custom metrics, not the
        # actual waterfall.
        with self.netlog_lock:
            try:
                path = os.path.join(self.task['dir'], 'netlog_bodies')
                for netlog_id in self.netlog_requests:
                    netlog_request = self.netlog_requests[netlog_id]
                    if 'url' in netlog_request:
                        url = netlog_request['url']
                        found = False
                        for request_id in requests:
                            request = requests[request_id]
                            if 'url' in request and request['url'] == url:
                                found = True
                        if not found:
                            self.request_sequence += 1
                            request = {'id': netlog_id, 'sequence': self.request_sequence, 'url': url}
                            if 'request_headers' in netlog_request:
                                request['request_headers'] = self.extract_headers(netlog_request['request_headers'])
                            if 'response_headers' in netlog_request:
                                request['response_headers'] = self.extract_headers(netlog_request['response_headers'])
                            body_file_path = os.path.join(path, netlog_id)
                            if os.path.exists(body_file_path):
                                request['body'] = body_file_path
                                body = None
                                with open(body_file_path, 'rb') as f:
                                    body = f.read()
                                if body is not None and len(body):
                                    request['response_body'] = body
                            requests[netlog_id] = request
            except Exception:
                logging.exception('Error adding netlog requests')
        return requests

    def flush_pending_messages(self):
        """Clear out any pending websocket messages"""
        if self.websocket:
            try:
                while True:
                    raw = self.websocket.get_message(0)
                    try:
                        if raw is not None and len(raw):
                            if self.recording:
                                if raw.find("Timeline.eventRecorded") == -1 and raw.find("Target.dispatchMessageFromTarget") == -1 and raw.find("Target.receivedMessageFromTarget") == -1:
                                    logging.debug('<- %s', raw[:200])
                                msg = json.loads(raw)
                                self.process_message(msg)
                        if not raw:
                            break
                    except Exception:
                        logging.exception('Error flushing websocket messages')
            except Exception:
                pass

    def send_command(self, method, params, wait=False, timeout=10, target_id=None):
        """Send a raw dev tools message and optionally wait for the response"""
        ret = None
        if target_id is None and self.default_target is not None and \
                not method.startswith('Target.') and \
                not method.startswith('Automation.') and \
                not method.startswith('Tracing.'):
            target_id = self.default_target
        if target_id is not None:
            self.command_id += 1
            command_id = int(self.command_id)
            msg = {'id': command_id, 'method': method, 'params': params}
            if wait:
                self.pending_commands.append(command_id)
            end_time = monotonic() + timeout
            target_response = self.send_command('Target.sendMessageToTarget',
                              {'targetId': target_id, 'message': json.dumps(msg)},
                              wait=wait, timeout=timeout)
            if wait:
                if command_id in self.command_responses:
                    ret = self.command_responses[command_id]
                    del self.command_responses[command_id]
                elif target_response is None or 'error' in target_response:
                    ret = target_response
                else:
                    while ret is None and monotonic() < end_time:
                        try:
                            raw = self.websocket.get_message(1)
                            try:
                                if raw is not None and len(raw):
                                    if raw.find("Timeline.eventRecorded") == -1 and raw.find("Target.dispatchMessageFromTarget") == -1 and raw.find("Target.receivedMessageFromTarget") == -1:
                                        logging.debug('<- %s', raw[:200])
                                    msg = json.loads(raw)
                                    self.process_message(msg)
                                    if command_id in self.command_responses:
                                        ret = self.command_responses[command_id]
                                        del self.command_responses[command_id]
                            except Exception:
                                logging.exception('Error processing websocket message')
                        except Exception:
                            pass
            elif method == 'Network.getResponseBody' and 'requestId' in params:
                self.pending_body_requests[command_id] = params['requestId']

        elif self.websocket:
            self.command_id += 1
            command_id = int(self.command_id)
            if wait:
                self.pending_commands.append(command_id)
            msg = {'id': command_id, 'method': method, 'params': params}
            try:
                out = json.dumps(msg)
                logging.debug("-> %s", out[:1000])
                self.websocket.send(out)
                if wait:
                    end_time = monotonic() + timeout
                    while ret is None and monotonic() < end_time:
                        try:
                            raw = self.websocket.get_message(1)
                            try:
                                if raw is not None and len(raw):
                                    if raw.find("Timeline.eventRecorded") == -1 and raw.find("Target.dispatchMessageFromTarget") == -1 and raw.find("Target.receivedMessageFromTarget") == -1:
                                        logging.debug('<- %s', raw[:200])
                                    msg = json.loads(raw)
                                    self.process_message(msg)
                                    if command_id in self.command_responses:
                                        ret = self.command_responses[command_id]
                                        del self.command_responses[command_id]
                            except Exception as err:
                                logging.error('Error processing websocket message: %s', err.__str__())
                        except Exception:
                            pass
                elif method == 'Network.getResponseBody' and 'requestId' in params:
                    self.pending_body_requests[command_id] = params['requestId']
            except Exception as err:
                logging.exception("Websocket send error: %s", err.__str__())
        return ret

    def wait_for_page_load(self):
        """Wait for the page load and activity to finish"""
        self.profile_start('wait_for_page_load')
        if self.websocket:
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
                    raw = self.websocket.get_message(interval)
                    try:
                        if raw is not None and len(raw):
                            if raw.find("Timeline.eventRecorded") == -1 and raw.find("Target.dispatchMessageFromTarget") == -1 and raw.find("Target.receivedMessageFromTarget") == -1:
                                logging.debug('<- %s', raw[:200])
                            msg = json.loads(raw)
                            self.process_message(msg)
                    except Exception:
                        logging.exception('Error processing message while waiting for page load')
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
                    done = True
                    if self.page_loaded is None or 'minimumTestSeconds' in self.task:
                        self.task['error'] = self.nav_error
                        if self.nav_error_code is not None:
                            self.task['page_data']['result'] = self.nav_error_code
                        else:
                            self.task['page_data']['result'] = 12999
                elif now >= end_time:
                    logging.debug('Test step timed out.')
                    done = True
                    # only consider it an error if we didn't get a page load event
                    if self.page_loaded is None:
                        self.task['error'] = "Page Load Timeout"
                        self.task['page_data']['result'] = 99997
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
                        ret = self.execute_js(self.wait_for_script)
                        if ret == True:
                            done = True
                else:
                    elapsed_activity = now - self.last_activity
                    elapsed_page_load = now - self.page_loaded if self.page_loaded else 0
                    if elapsed_page_load >= 1 and elapsed_activity >= self.task['activity_time']:
                        done = True
                    elif self.task['error'] is not None:
                        done = True
        self.profile_end('wait_for_page_load')
    
    def grab_screenshot(self, path, png=True, resize=0):
        """Save the screen shot (png or jpeg)"""
        logging.debug('Grabbing Screenshot')
        if not self.main_thread_blocked and not self.must_exit:
            self.profile_start('screenshot')
            response = None
            data = None
            if self.is_webkit:
                # Get the current viewport (depends on css scaling)
                size = self.execute_js("[window.innerWidth, window.innerHeight]")
                if size is not None and len(size) == 2:
                    width = size[0]
                    height = size[1]
                    response = self.send_command("Page.snapshotRect", {"x": 0, "y": 0, "width": width, "height": height, "coordinateSystem": "Viewport"}, wait=True, timeout=30)
                    if response is not None and 'result' in response and 'dataURL' in response['result'] and response['result']['dataURL'].startswith('data:image/png;base64,'):
                        data = response['result']['dataURL'][22:]
                        logging.debug("Image Data: %s", data[:200])
                else:
                    logging.debug('Viewport dimensions not available for capturing screenshot')
            else:
                response = self.send_command("Page.captureScreenshot", {}, wait=True, timeout=30)
                if response is not None and 'result' in response and 'data' in response['result']:
                    data = response['result']['data']
            if data is not None:
                resize_string = '' if not resize else '-resize {0:d}x{0:d} '.format(resize)
                if png:
                    with open(path, 'wb') as image_file:
                        image_file.write(base64.b64decode(data))
                    # Fix png issues
                    cmd = '{0} -format png -define png:color-type=2 '\
                        '-depth 8 {1}"{2}"'.format(self.job['image_magick']['mogrify'],
                                                   resize_string, path)
                    logging.debug(cmd)
                    subprocess.call(cmd, shell=True)
                else:
                    tmp_file = path + '.png'
                    with open(tmp_file, 'wb') as image_file:
                        image_file.write(base64.b64decode(data))
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
            self.profile_end('screenshot')
        else:
            logging.debug('Skipping screenshot because the main thread is blocked')

    def colors_are_similar(self, color1, color2, threshold=15):
        """See if 2 given pixels are of similar color"""
        similar = True
        delta_sum = 0
        for value in range(3):
            delta = abs(color1[value] - color2[value])
            delta_sum += delta
            if delta > threshold:
                similar = False
        if delta_sum > threshold:
            similar = False
        return similar

    def execute_js(self, script, use_execution_context=False):
        """Run the provided JS in the browser and return the result"""
        if self.must_exit:
            return
        ret = None
        if self.task['error'] is None and not self.main_thread_blocked:
            if self.is_webkit:
                response = self.send_command('Runtime.evaluate', {'expression': script, 'returnByValue': True}, timeout=30, wait=True)
            else:
                params = {'expression': script,
                          'awaitPromise': True,
                          'returnByValue': True,
                          'timeout': 30000}
                if use_execution_context and self.execution_context is not None:
                    params['contextId'] = self.execution_context
                response = self.send_command("Runtime.evaluate", params, wait=True, timeout=30)
            if response is not None and 'result' in response and\
                    'result' in response['result'] and\
                    'value' in response['result']['result']:
                ret = response['result']['result']['value']
        return ret

    def set_execution_context(self, target):
        """ Set the js execution context by matching id, origin or name """
        if len(target):
            parts = target.split('=', 1)
            if len(parts) == 2:
                key = parts[0].strip()
                value = parts[1].strip()
                if key in ['id', 'name', 'origin'] and len(value):
                    for id in self.execution_contexts:
                        context = self.execution_contexts[id]
                        if key in context and context[key] == value:
                            self.execution_context = id
                            break
        else:
            self.execution_context = None

    def set_header(self, header):
        """Add/modify a header on the outbound requests"""
        if header is not None and len(header):
            separator = header.find(':')
            if separator > 0:
                name = header[:separator].strip()
                value = header[separator + 1:].strip()
                self.headers[name] = value
                self.send_command('Network.setExtraHTTPHeaders', {'headers': self.headers}, wait=True)
                if len(self.workers):
                    for target in self.workers:
                        self.send_command('Network.setExtraHTTPHeaders', {'headers': self.headers}, target_id=target['targetId'])

    def reset_headers(self):
        """Add/modify a header on the outbound requests"""
        self.headers = {}
        self.send_command('Network.setExtraHTTPHeaders', {'headers': self.headers}, wait=True)
        if len(self.workers):
            for target in self.workers:
                self.send_command('Network.setExtraHTTPHeaders', {'headers': self.headers}, target_id=target['targetId'])

    def clear_cache(self):
        """Clear the browser cache"""
        self.send_command('Network.clearBrowserCache', {}, wait=True)

    def disable_cache(self, disable):
        """Disable the browser cache"""
        if self.is_webkit:
            self.send_command('Network.setResourceCachingDisabled', {'disabled': disable}, wait=True)
        else:
            self.send_command('Network.setCacheDisabled', {'cacheDisabled': disable}, wait=True)

    def send_character(self, char):
        """Send a non-keyboard character directly to the page"""
        self.send_command('Input.insertText', {'text': char}, wait=True)

    def key_info(self, key):
        """Build the details needed for the keypress commands for the given key"""
        info = {
            'key': '',
            'keyCode': 0,
            'code': '',
            'location': 0
        }
        if key in self.key_definitions:
            definition = self.key_definitions[key]
            if 'key' in definition:
                info['key'] = definition['key']
                if len(definition['key']) == 1:
                    info['text'] = definition['key']
            if 'keyCode' in definition:
                info['keyCode'] = definition['keyCode']
            if 'code' in definition:
                info['code'] = definition['code']
            if 'location' in definition:
                info['location'] = definition['location']
            if 'text' in definition:
                info['text'] = definition['text']
        return info

    def key_down(self, key):
        """Press down a key"""
        info = self.key_info(key)
        params = {
            'type': 'rawKeyDown',
            'key': info['key'],
            'windowsVirtualKeyCode': info['keyCode'],
            'code': info['code'],
            'location': info['location']
        }
        if 'text' in info:
            params['type'] = 'keyDown'
            params['text'] = info['text']
            params['unmodifiedText'] = info['text']
        if info['location'] == 3:
            params['isKeypad'] = True
        self.send_command('Input.dispatchKeyEvent', params)

    def key_up(self, key):
        """Let up a key"""
        info = self.key_info(key)
        self.send_command('Input.dispatchKeyEvent', {
            'type': 'keyUp',
            'key': info['key'],
            'windowsVirtualKeyCode': info['keyCode'],
            'code': info['code'],
            'location': info['location']
        })

    def keypress(self, key):
        """Simulate pressing a keyboard key"""
        try:
            self.key_down(key)
            self.key_up(key)
        except Exception:
            logging.exception('Error running keypress command')

    def type_text(self, string):
        """Simulate typing text input"""
        try:
            for char in string:
                if char in self.key_definitions:
                    self.keypress(char)
                else:
                    self.send_character(char)
        except Exception:
            logging.exception('Error running type command')

    def enable_target(self, target_id=None):
        """Hook up the necessary network (or other) events for the given target"""
        try:
            self.send_command('Network.enable', {}, target_id=target_id)
            self.send_command('Console.enable', {}, target_id=target_id)
            self.send_command('Log.enable', {}, target_id=target_id)
            self.send_command('Runtime.enable', {}, target_id=target_id)
            self.send_command('Log.startViolationsReport', {'config': [{'name': 'discouragedAPIUse', 'threshold': -1}]}, target_id=target_id)
            self.send_command('Audits.enable', {}, target_id=target_id)
            self.job['shaper'].apply(target_id=target_id)
            self.enable_shaper(target_id=target_id)
            if self.headers:
                self.send_command('Network.setExtraHTTPHeaders', {'headers': self.headers}, target_id=target_id, wait=True)
            if 'user_agent_string' in self.job:
                ua = self.job['user_agent_string']
                browser_version = "0"
                full_version = "0"
                main_version = "0"
                try:
                    if self.is_ios:
                        match = re.search(r'Version\/(\d+\.\d+\.\d+)', ua)
                    elif self.is_webkit:
                        match = re.search(r'WebKit\/(\d+\.\d+\.\d+)', ua)
                    else:
                        match = re.search(r'Chrome\/(\d+\.\d+\.\d+\.\d+)', ua)
                    if match:
                        browser_version = match.group(1)
                    full_version = browser_version
                    main_version = str(int(re.search(r'\d+', browser_version).group()))
                except Exception:
                    logging.exception('Error extracting browser version')
                metadata = {
                    'brands': [
                        {'brand': ' Not A;Brand', 'version': '99'},
                        {'brand': 'Chromium', 'version': main_version},
                        {'brand': 'Google Chrome', 'version': main_version}
                    ],
                    'fullVersionList': [
                        {'brand': ' Not A;Brand', 'version': '99'},
                        {'brand': 'Chromium', 'version': full_version},
                        {'brand': 'Google Chrome', 'version': full_version}
                    ],
                    'platform': 'Unknown',
                    'platformVersion': '0',
                    'architecture': 'x86',
                    'model': 'Model',
                    'mobile': bool('mobile' in self.job and self.job['mobile'])
                    }
                try:
                    if ua.find('Android') >= 0:
                        metadata['platform'] = 'Android'
                        metadata['platformVersion'] = '10'
                        metadata['architecture'] = 'arm'
                    elif ua.find('iPhone') >= 0:
                        metadata['platform'] = 'iOS'
                        metadata['platformVersion'] = '15'
                        metadata['architecture'] = 'arm'
                        metadata['brands'] = [
                            {'brand': ' Not A;Brand', 'version': '99'},
                            {'brand': 'Safari', 'version': main_version},
                        ]
                        metadata['fullVersionList'] = [
                            {'brand': ' Not A;Brand', 'version': '99'},
                            {'brand': 'Safari', 'version': full_version},
                        ]
                    elif ua.find('(Windows') >= 0:
                        metadata['platform'] = 'Windows'
                        metadata['platformVersion'] = '10'
                    elif ua.find('(Macintosh') >= 0:
                        metadata['platform'] = 'macOS'
                    elif ua.find('(Linux') >= 0:
                        metadata['platform'] = 'Linux'
                    if self.options.android or self.is_ios:
                        metadata['mobile'] = True
                except Exception:
                    logging.exception('Error generating UA metadata')
                self.send_command('Network.setUserAgentOverride', {'userAgent': self.job['user_agent_string'], 'userAgentMetadata': metadata}, target_id=target_id, wait=True)
            if len(self.task['block']):
                for block in self.task['block']:
                    self.send_command('Network.addBlockedURL', {'url': block}, target_id=target_id)
                self.send_command('Network.setBlockedURLs', {'urls': self.task['block']}, target_id=target_id)
            if 'overrideHosts' in self.task and self.task['overrideHosts']:
                patterns = []
                for host in self.task['overrideHosts']:
                    if host == '*':
                        patterns.append({'urlPattern': 'http://*'})
                        patterns.append({'urlPattern': 'https://*'})
                    else:
                        patterns.append({'urlPattern': 'http://{0}*'.format(host)})
                        patterns.append({'urlPattern': 'https://{0}*'.format(host)})
                        # to handle redirects, let's intercept the host match as well
                        patterns.append({'urlPattern': 'http://{0}*'.format(self.task['overrideHosts'][host])})
                        patterns.append({'urlPattern': 'https://{0}*'.format(self.task['overrideHosts'][host])})
                self.send_command('Network.setRequestInterception', {'patterns': patterns}, target_id=target_id)
        except Exception:
            logging.exception("Error enabling target")

    def process_message(self, msg, target_id=None):
        """Process an inbound dev tools message"""
        if 'method' in msg:
            parts = msg['method'].split('.')
            if len(parts) >= 2:
                category = parts[0]
                event = parts[1]
                log_event = bool(self.recording)
                if category == 'Page' and self.recording:
                    self.process_page_event(event, msg)
                elif category == 'Network' and self.recording:
                    self.process_network_event(event, msg, target_id)
                elif category == 'Console' and self.recording:
                    self.process_console_event(event, msg)
                elif category == 'Log' and self.recording:
                    self.process_console_event(event, msg)
                elif category == 'Audits' and self.recording:
                    self.process_audit_event(event, msg)
                elif category == 'Inspector' and target_id is None:
                    self.process_inspector_event(event)
                elif category == 'CSS' and self.recording:
                    self.process_css_event(event, msg)
                elif category == 'Debugger':
                    self.process_debugger_event(event, msg)
                elif category == 'Runtime':
                    self.process_runtime_event(event, msg)
                elif category == 'Target':
                    log_event = False
                    self.process_target_event(event, msg)
                elif category == 'Timeline' and self.recording and self.is_webkit:
                    self.process_timeline_event(event, msg)
                if log_event:
                    self.log_dev_tools_event(msg)
        if 'id' in msg:
            response_id = int(re.search(r'\d+', str(msg['id'])).group())
            if response_id in self.pending_body_requests:
                request_id = self.pending_body_requests[response_id]
                self.process_response_body(request_id, msg)
                del(self.pending_body_requests[response_id])
            if response_id in self.pending_commands:
                self.pending_commands.remove(response_id)
                self.command_responses[response_id] = msg

    def process_console_event(self, event, msg):
        """Handle Console.* and Log.* events"""
        message = None
        if event == 'messageAdded' and 'message' in msg['params']:
            message = msg['params']['message']
        elif event == 'entryAdded' and 'entry' in msg['params']:
            message = msg['params']['entry']
        
        if message is not None:
            if 'text' in message and message['text'].startswith('wptagent_message:'):
                try:
                    # Throw away messages over 1MB to prevent things from spiraling too badly
                    if len(message['text']) < 1000000:
                        wpt_message = json.loads(message['text'][17:])
                        if 'name' in wpt_message:
                            if wpt_message['name'] == 'perfentry' and 'data' in wpt_message:
                                self.performance_timing.append(wpt_message['data'])
                except Exception:
                    logging.exception('Error decoding console log message')
            else:
                self.console_log.append(message)

    def process_audit_event(self, event, msg):
        """Handle Audits.* events"""
        if event == 'issueAdded' and 'issue' in msg['params']:
            self.audit_issues.append(msg['params']['issue'])

    def process_page_event(self, event, msg):
        """Process Page.* dev tools events"""
        # Handle permissions for frame navigations
        if 'params' in msg and 'url' in msg['params'] and not self.is_webkit:
            try:
                parts = urlsplit(msg['params']['url'])
                origin = parts.scheme + '://' + parts.netloc
                self.send_command('Browser.grantPermissions',
                                  {'origin': origin,
                                   'permissions': ['geolocation',
                                                   'videoCapture',
                                                   'audioCapture',
                                                   'sensors',
                                                   'idleDetection',
                                                   'wakeLockScreen']})
            except Exception:
                logging.exception('Error setting permissions for origin')

        # Event-specific logic
        if self.is_webkit and self.start_timestamp is None and 'params' in msg and 'timestamp' in msg['params']:
            self.start_timestamp = float(msg['params']['timestamp'])
        if event == 'loadEventFired':
            self.page_loaded = monotonic()
        elif event == 'frameStartedLoading' and 'params' in msg and 'frameId' in msg['params']:
            if self.is_navigating and self.main_frame is None:
                self.is_navigating = False
                self.main_frame = msg['params']['frameId']
            if self.main_frame == msg['params']['frameId']:
                logging.debug("Navigating main frame")
                self.last_activity = monotonic()
                self.page_loaded = None
        elif event == 'frameNavigated' and 'params' in msg and \
                'frame' in msg['params'] and 'id' in msg['params']['frame']:
            if self.main_frame is not None and \
                    self.main_frame == msg['params']['frame']['id'] and\
                    'injectScript' in self.job and \
                    not self.job.get('injectScriptAllFrames'):
                self.execute_js(self.job['injectScript'])
        elif event == 'frameStoppedLoading' and 'params' in msg and 'frameId' in msg['params']:
            if self.main_frame is not None and \
                    not self.page_loaded and \
                    self.main_frame == msg['params']['frameId']:
                if self.nav_error is not None:
                    self.task['error'] = self.nav_error
                    logging.debug("Page load failed: %s", self.nav_error)
                    if self.nav_error_code is not None:
                        self.task['page_data']['result'] = self.nav_error_code
                    else:
                        self.task['page_data']['result'] = 12999
                self.page_loaded = monotonic()
        elif event == 'javascriptDialogOpening':
            result = self.send_command("Page.handleJavaScriptDialog", {"accept": False}, wait=True)
            if result is not None and 'error' in result:
                result = self.send_command("Page.handleJavaScriptDialog",
                                           {"accept": True}, wait=True)
                if result is not None and 'error' in result:
                    self.task['error'] = "Page opened a modal dailog"
        elif event == 'interstitialShown':
            self.main_thread_blocked = True
            logging.debug("Page opened a modal interstitial")
            self.nav_error = "Page opened a modal interstitial"
            self.nav_error_code = 405

    def process_css_event(self, event, msg):
        """Handle CSS.* events"""
        if event == 'styleSheetAdded':
            if 'params' in msg and 'header' in msg['params']:
                entry = msg['params']['header']
                if 'styleSheetId' in entry and \
                        entry['styleSheetId'] not in self.stylesheets and \
                        'sourceURL' in entry and entry['sourceURL']:
                    self.stylesheets[entry['styleSheetId']] = entry['sourceURL']

    def process_debugger_event(self, event, msg):
        """Handle Debugger.* events"""
        if event == 'paused':
            self.send_command('Debugger.resume', {})

    def process_runtime_event(self, event, msg):
        """Handle Runtime.* events"""
        if event == 'executionContextCreated':
            if 'params' in msg and 'context' in msg['params'] and 'id' in msg['params']['context']:
                context = msg['params']['context']
                id = context['id']
                ctx = {'id': id}
                if 'origin' in context:
                    ctx['origin'] = context['origin']
                if 'name' in context:
                    ctx['name'] = context['name']
                self.execution_contexts[id] = ctx
                logging.debug('Execution context created: %s', json.dumps(context))
        elif event == 'executionContextDestroyed':
            if 'params' in msg and 'executionContextId' in msg['params']:
                id = msg['params']['executionContextId']
                if id in self.execution_contexts:
                    del self.execution_contexts[id]
                    logging.debug('Execution context %d deleted', id)

    def process_network_event(self, event, msg, target_id=None):
        """Process Network.* dev tools events"""
        if event == 'requestIntercepted':
            params = {'interceptionId': msg['params']['interceptionId']}
            if 'overrideHosts' in self.task:
                url = msg['params']['request']['url']
                parts = urlsplit(url).netloc.split(':')
                host = parts[0]
                # go through the override list and find the first match (supporting wildcards)
                try:
                    from fnmatch import fnmatch
                    for host_match in self.task['overrideHosts']:
                        headers = msg['params']['request']['headers']
                        if 'x-host' not in headers:
                            if fnmatch(host, host_match):
                                # Overriding to * is just a passthrough, don't actually modify anything
                                if self.task['overrideHosts'][host_match] != '*':
                                    headers['x-host'] = host
                                    params['headers'] = headers
                                    params['url'] = url.replace(host, self.task['overrideHosts'][host_match], 1)
                                    # We need to add the new URL to our event for parsing later
                                    # let's use an underscore to indicate to ourselves that we're adding this
                                    msg['params']['_overwrittenURL'] =  url.replace(host, self.task['overrideHosts'][host_match], 1)
                                    break
                            # check the new host to handle redirects
                            if fnmatch(self.task['overrideHosts'][host_match], host):
                                # in this case, we simply want to modify the header, everything else is fine
                                headers = msg['params']['request']['headers']
                                headers['x-host'] = host_match
                                params['headers'] = headers
                                break
                except Exception:
                    logging.exception('Error processing host override')
                self.send_command('Network.continueInterceptedRequest', params, target_id=target_id)
        elif 'requestId' in msg['params']:
            timestamp = None
            if 'params' in msg and 'timestamp' in msg['params']:
                timestamp = msg['params']['timestamp']
            request_id = msg['params']['requestId']
            if request_id not in self.requests:
                self.request_sequence += 1
                self.requests[request_id] = {'id': request_id, 'sequence': self.request_sequence}
            request = self.requests[request_id]
            if target_id is not None:
                request['targetId'] = target_id
            ignore_activity = request['is_video'] if 'is_video' in request else False
            if event == 'requestWillBeSent':
                if self.is_webkit and self.start_timestamp is None and 'params' in msg and 'timestamp' in msg['params']:
                    self.start_timestamp = float(msg['params']['timestamp'])
                if self.is_navigating and self.main_frame is None and 'frameId' in msg['params']:
                    self.is_navigating = False
                    self.main_frame = msg['params']['frameId']
                if 'request' not in request:
                    request['request'] = []
                request['request'].append(msg['params'])
                if 'url' in msg['params'] and msg['params']['url'].endswith('.mp4'):
                    request['is_video'] = True
                request['fromNet'] = True
                if self.main_frame is not None and self.main_request is None and 'frameId' in msg['params'] and msg['params']['frameId'] == self.main_frame:
                    logging.debug('Main request detected')
                    self.main_request = request_id
                    if 'timestamp' in msg['params']:
                        self.start_timestamp = float(msg['params']['timestamp'])
                if 'params' in msg and 'request' in msg['params'] and 'headers' in msg['params']['request']:
                    request['request_headers'] = msg['params']['request']['headers']
            elif event == 'requestWillBeSentExtraInfo':
                request['requestExtra'] = msg['params']
            elif event == 'resourceChangedPriority':
                if 'priority' not in request:
                    request['priority'] = []
                request['priority'].append(msg['params'])
            elif event == 'requestServedFromCache':
                self.response_started = True
                request['fromNet'] = False
            elif event == 'responseReceived':
                self.response_started = True
                if 'response' not in request:
                    request['response'] = []
                request['response'].append(msg['params'])
                if 'response' in msg['params']:
                    response = msg['params']['response']
                    if 'fromDiskCache' in response and response['fromDiskCache']:
                        request['fromNet'] = False
                    if 'fromServiceWorker' in response and response['fromServiceWorker']:
                        request['fromNet'] = False
                    if 'mimeType' in response and response['mimeType'].startswith('video/'):
                        request['is_video'] = True
                    if self.main_request is not None and \
                            request_id == self.main_request and \
                            'headers' in response:
                        self.main_request_headers = response['headers']
                    if self.main_request is not None and \
                            request_id == self.main_request and \
                            'status' in response and response['status'] >= 400:
                        self.nav_error_code = response['status']
                        if 'statusText' in response and response['statusText']:
                            self.nav_error = response['statusText']
                        else:
                            self.nav_error = '{0:d} Navigation error'.format(self.nav_error_code)
                        logging.debug('Main resource Navigation error: %s', self.nav_error)
            elif event == 'responseReceivedExtraInfo':
                self.response_started = True
                request['responseExtra'] = msg['params']
            elif event == 'dataReceived':
                self.response_started = True
                if 'data' not in request:
                    request['data'] = []
                request['data'].append(msg['params'])
            elif event == 'loadingFinished':
                self.response_started = True
                request['finished'] = msg['params']
                if 'fromNet' in request and request['fromNet']:
                    self.request_count += 1
                self.get_response_body(request_id, False)
            elif event == 'loadingFailed':
                request['failed'] = msg['params']
                if not self.response_started:
                    if 'errorText' in msg['params']:
                        self.nav_error = msg['params']['errorText']
                    else:
                        self.nav_error = 'Unknown navigation error'
                    self.nav_error_code = 404
                    logging.debug('Navigation error: %s', self.nav_error)
                elif self.main_request is not None and \
                        request_id == self.main_request and \
                        'errorText' in msg['params'] and \
                        'canceled' in msg['params'] and \
                        not msg['params']['canceled']:
                    self.nav_error = msg['params']['errorText']
                    self.nav_error_code = 404
                    logging.debug('Navigation error: %s', self.nav_error)
            else:
                ignore_activity = True
            if not self.task['stop_at_onload'] and not ignore_activity:
                self.last_activity = monotonic()

    def process_inspector_event(self, event):
        """Process Inspector.* dev tools events"""
        if event == 'detached':
            self.task['error'] = 'Inspector detached, possibly crashed.'
            self.task['page_data']['result'] = 12999
        elif event == 'targetCrashed':
            self.task['error'] = 'Browser crashed.'
            self.task['page_data']['result'] = 12999

    def process_target_event(self, event, msg):
        """Process Target.* dev tools events"""
        if event == 'attachedToTarget':
            if 'targetInfo' in msg['params'] and 'targetId' in msg['params']['targetInfo']:
                target = msg['params']['targetInfo']
                if 'type' in target and target['type'] == 'service_worker':
                    self.workers.append(target)
                if self.recording:
                    self.enable_target(target['targetId'])
                self.send_command('Runtime.runIfWaitingForDebugger', {},
                                  target_id=target['targetId'])
        if event == 'receivedMessageFromTarget' or event == 'dispatchMessageFromTarget':
            target_id = None
            if 'targetId' in msg['params']:
                target_id = msg['params']['targetId']
            if 'message' in msg['params'] and target_id is not None:
                if msg['params']['message'].find("Timeline.eventRecorded") == -1:
                    logging.debug('<- %s', msg['params']['message'][:200])
                target_message = json.loads(msg['params']['message'])
                self.process_message(target_message, target_id=target_id)
        if event == 'targetCreated':
            if 'targetInfo' in msg['params'] and 'targetId' in msg['params']['targetInfo']:
                target = msg['params']['targetInfo']
                target_id = target['targetId']
                if 'type' in target and target['type'] == 'page':
                    if self.is_webkit:
                        self.default_target = target_id
                    if self.recording:
                        self.enable_webkit_events()
                else:
                    self.workers.append(target)
                    if self.recording:
                        self.enable_target(target_id)
                self.send_command('Target.resume', {'targetId': target_id})

    def process_timeline_event(self, event, msg):
        """Handle Timeline.* events"""
        if self.trace_parser is not None and 'params' in msg and 'record' in msg['params']:
            if self.start_timestamp is None:
                return
            if self.trace_parser.start_time is None:
                self.trace_parser.start_time = self.start_timestamp * 1000000.0
                self.trace_parser.end_time = self.start_timestamp * 1000000.0
            if 'timestamp' in msg['params']:
                timestamp = msg['params']['timestamp'] * 1000000.0
                if timestamp > self.trace_parser.end_time:
                    self.trace_parser.end_time = timestamp
            processed = self.trace_parser.ProcessOldTimelineEvent(msg['params']['record'], None)
            if processed is not None:
                self.trace_parser.timeline_events.append(processed)

    def log_dev_tools_event(self, msg):
        """Log the dev tools events to a file"""
        if self.task['log_data']:
            if self.dev_tools_file is None:
                path = self.path_base + '_devtools.json.gz'
                self.dev_tools_file = gzip.open(path, GZIP_TEXT, 7)
                self.dev_tools_file.write("[{}")
            if self.dev_tools_file is not None:
                self.dev_tools_file.write(",\n")
                self.dev_tools_file.write(json.dumps(msg))

    def get_header_value(self, headers, name):
        """Get the value for the requested header"""
        value = None
        if headers:
            if name in headers:
                value = headers[name]
            else:
                find = name.lower()
                for header_name in headers:
                    check = header_name.lower()
                    if check == find or (check[0] == ':' and check[1:] == find):
                        value = headers[header_name]
                        break
        return value

    def bytes_from_range(self, text, range_info):
        """Convert a line/column start and end into a byte count"""
        byte_count = 0
        try:
            lines = text.splitlines()
            line_count = len(lines)
            start_line = range_info['startLine']
            end_line = range_info['endLine']
            if start_line > line_count or end_line > line_count:
                return 0
            start_column = range_info['startColumn']
            end_column = range_info['endColumn']
            if start_line == end_line:
                byte_count = end_column - start_column + 1
            else:
                # count the whole lines between the partial start and end lines
                if end_line > start_line + 1:
                    for row in range(start_line + 1, end_line):
                        byte_count += len(lines[row])
                byte_count += len(lines[start_line][start_column:])
                byte_count += end_column
        except Exception:
            logging.exception('Error in bytes_from_range')
        return byte_count

    def profile_start(self, event_name):
        event_name = 'dt.' + event_name
        if self.task is not None and 'profile_data' in self.task:
            with self.task['profile_data']['lock']:
                self.task['profile_data'][event_name] = {'s': round(monotonic() - self.task['profile_data']['start'], 3)}

    def profile_end(self, event_name):
        event_name = 'dt.' + event_name
        if self.task is not None and 'profile_data' in self.task:
            with self.task['profile_data']['lock']:
                if event_name in self.task['profile_data']:
                    self.task['profile_data'][event_name]['e'] = round(monotonic() - self.task['profile_data']['start'], 3)
                    self.task['profile_data'][event_name]['d'] = round(self.task['profile_data'][event_name]['e'] - self.task['profile_data'][event_name]['s'], 3)

    def on_netlog_request_created(self, request_id, request_info):
        """Callbacks from streamed netlog processing (these will come in on a background thread)"""
        try:
            with self.netlog_lock:
                if request_id not in self.netlog_requests:
                    self.netlog_requests[request_id] = request_info
                if 'url' in request_info:
                    url = request_info['url']
                    self.netlog_requests[request_id]['url'] = url
                    if url not in self.netlog_urls:
                        self.netlog_urls[url] = []
                    if request_id not in self.netlog_urls[url]:
                        self.netlog_urls[url].append(request_id)
            logging.debug("Netlog request %s created: %s", request_id, json.dumps(request_info))
        except Exception:
            logging.exception('Error handling on_netlog_request_created')

    def on_netlog_request_headers_sent(self, request_id, request_headers):
        """Callbacks from streamed netlog processing (these will come in on a background thread)"""
        try:
            with self.netlog_lock:
                if request_id not in self.netlog_requests:
                    self.netlog_requests[request_id] = {}
                self.netlog_requests[request_id]['request_headers'] = request_headers
            logging.debug("Netlog request headers for %s: %s", request_id, json.dumps(request_headers))
        except Exception:
            logging.exception('Error handling on_netlog_request_headers_sent')

    def on_netlog_response_headers_received(self, request_id, response_headers):
        """Callbacks from streamed netlog processing (these will come in on a background thread)"""
        try:
            with self.netlog_lock:
                if request_id not in self.netlog_requests:
                    self.netlog_requests[request_id] = {}
                self.netlog_requests[request_id]['response_headers'] = response_headers
            logging.debug("Netlog response headers for %s: %s", request_id, json.dumps(response_headers))
        except Exception:
            logging.exception('Error handling on_netlog_response_headers_received')

    def on_netlog_response_bytes_received(self, request_id, filtered_bytes):
        """Callbacks from streamed netlog processing (these will come in on a background thread)"""
        try:
            if filtered_bytes is not None and len(filtered_bytes):
                with self.netlog_lock:
                    path = os.path.join(self.task['dir'], 'netlog_bodies')
                    if not os.path.isdir(path):
                        os.makedirs(path)
                    body_file_path = os.path.join(path, request_id)
                    with open(body_file_path, 'a+b') as body_file:
                        body_file.write(filtered_bytes)
            logging.debug("Netlog response bytes for %s: %d bytes", request_id, len(filtered_bytes))
        except Exception:
            logging.exception('Error handling on_netlog_response_bytes_received')

    def on_request_id_changed(self, request_id, new_request_id):
        """Callbacks from streamed netlog processing (these will come in on a background thread)"""
        try:
            with self.netlog_lock:
                if request_id in self.netlog_requests and new_request_id not in self.netlog_requests:
                    self.netlog_requests[new_request_id] = self.netlog_requests[request_id]
                    del self.netlog_requests[request_id]
            logging.debug("Netlog request ID changed from %s to %s", request_id, new_request_id)
        except Exception:
            logging.exception('Error handling on_request_id_changed')


class DevToolsClient(WebSocketClient):
    """DevTools WebSocket client"""
    def __init__(self, url, protocols=None, extensions=None, heartbeat_freq=None,
                 ssl_options=None, headers=None):
        WebSocketClient.__init__(self, url, protocols, extensions, heartbeat_freq,
                                 ssl_options, headers)
        self.connected = False
        self.messages = multiprocessing.JoinableQueue()
        self.trace_file = None
        self.video_prefix = None
        self.trace_ts_start = None
        self.options = None
        self.job = None
        self.task = None
        self.last_image = None
        self.pending_image = None
        self.video_viewport = None
        self.path_base = None
        self.trace_parser = None
        self.trace_event_counts = {}
        self.processed_event_count = 0
        self.last_data = None
        self.keep_timeline = True
        self.trace_done = True

    def opened(self):
        """WebSocket interface - connection opened"""
        logging.debug("DevTools websocket connected")
        self.connected = True

    def closed(self, code, reason=None):
        """WebSocket interface - connection closed"""
        logging.debug("DevTools websocket disconnected")
        self.connected = False

    def received_message(self, raw):
        """WebSocket interface - message received"""
        try:
            if raw.is_text:
                message = raw.data.decode(raw.encoding) if raw.encoding is not None else raw.data
                compare = message[:50]
                if self.path_base is not None and compare.find('"Tracing.dataCollected') > -1:
                    now = monotonic()
                    msg = json.loads(message)
                    message = None
                    if msg is not None:
                        self.process_trace_event(msg)
                    if self.last_data is None or now - self.last_data >= 1.0:
                        self.last_data = now
                        self.messages.put('{"method":"got_message"}')
                        logging.debug('Processed %d trace events', self.processed_event_count)
                        self.processed_event_count = 0
                elif self.trace_file is not None and compare.find('"Tracing.tracingComplete') > -1:
                    if self.processed_event_count:
                        logging.debug('Processed %d trace events', self.processed_event_count)
                    self.trace_file.write("\n]}")
                    self.trace_file.close()
                    self.trace_file = None
                    self.trace_done = True
                if message is not None:
                    self.messages.put(message)
        except Exception:
            logging.exception('Error processing received websocket message')

    def get_message(self, timeout):
        """Wait for and return a message from the queue"""
        message = None
        try:
            if timeout is None or timeout <= 0:
                message = self.messages.get_nowait()
            else:
                message = self.messages.get(True, timeout)
            self.messages.task_done()
        except Exception:
            pass
        return message

    def start_processing_trace(self, path_base, video_prefix, options, job, task, start_timestamp, keep_timeline, dom_tree, performance_timing):
        """Write any trace events to the given file"""
        self.last_image = None
        self.trace_ts_start = None
        if start_timestamp is not None:
            self.trace_ts_start = int(start_timestamp * 1000000)
        self.path_base = path_base
        self.video_prefix = video_prefix
        self.task = task
        self.options = options
        self.job = job
        self.video_viewport = None
        self.keep_timeline = keep_timeline
        self.dom_tree = dom_tree
        self.performance_timing = performance_timing
        self.trace_done = False

    def stop_processing_trace(self, job):
        """All done"""
        if self.pending_image is not None and self.last_image is not None and\
                self.pending_image["image"] != self.last_image["image"]:
            with open(self.pending_image["path"], 'wb') as image_file:
                image_file.write(base64.b64decode(self.pending_image["image"]))
        self.pending_image = None
        self.trace_ts_start = None
        if self.trace_file is not None:
            self.trace_file.write("\n]}")
            self.trace_file.close()
            self.trace_file = None
        self.options = None
        self.job = None
        self.task = None
        self.video_viewport = None
        self.last_image = None
        if self.trace_parser is not None and self.path_base is not None:
            start = monotonic()
            logging.debug("Post-Processing the trace netlog events")
            self.trace_parser.post_process_netlog_events()
            logging.debug("Processing the trace timeline events")
            self.trace_parser.ProcessTimelineEvents()
            self.trace_parser.WriteUserTiming(self.path_base + '_user_timing.json.gz', self.dom_tree, self.performance_timing)
            if 'timeline' in job and job['timeline']:
                self.trace_parser.WriteCPUSlices(self.path_base + '_timeline_cpu.json.gz')
                self.trace_parser.WriteScriptTimings(self.path_base + '_script_timing.json.gz')
                self.trace_parser.WriteInteractive(self.path_base + '_interactive.json.gz')
                self.trace_parser.WriteLongTasks(self.path_base + '_long_tasks.json.gz')
                self.trace_parser.WriteTimelineRequests(self.path_base + '_timeline_requests.json.gz')
            self.trace_parser.WriteFeatureUsage(self.path_base + '_feature_usage.json.gz')
            self.trace_parser.WritePageData(self.path_base + '_trace_page_data.json.gz')
            if not job.get('streaming_netlog'):
                self.trace_parser.WriteNetlog(self.path_base + '_netlog_requests.json.gz')
            self.trace_parser.WriteV8Stats(self.path_base + '_v8stats.json.gz')
            elapsed = monotonic() - start
            logging.debug("Done processing the trace events: %0.3fs", elapsed)
        self.trace_parser = None
        self.path_base = None
        logging.debug("Trace event counts:")
        for cat in self.trace_event_counts:
            logging.debug('    %s: %s', cat, self.trace_event_counts[cat])
        self.trace_event_counts = {}

    def process_trace_event(self, msg):
        """Process Tracing.* dev tools events"""
        if 'params' in msg and 'value' in msg['params'] and len(msg['params']['value']):
            if self.trace_file is None and self.keep_timeline:
                self.trace_file = gzip.open(self.path_base + '_trace.json.gz',
                                            GZIP_TEXT, compresslevel=7)
                self.trace_file.write('{"traceEvents":[{}')
            if self.trace_parser is None:
                from internal.support.trace_parser import Trace
                self.trace_parser = Trace()
            # write out the trace events one-per-line but pull out any
            # devtools screenshots as separate files.
            trace_events = msg['params']['value']
            out = ''
            for _, trace_event in enumerate(trace_events):
                self.processed_event_count += 1
                keep_event = self.keep_timeline
                process_event = True
                if self.video_prefix is not None and 'cat' in trace_event and \
                        'name' in trace_event and 'ts' in trace_event:
                    if self.trace_ts_start is None and \
                            (trace_event['name'] == 'navigationStart' or
                                trace_event['name'] == 'fetchStart') and \
                            trace_event['cat'].find('blink.user_timing') > -1:
                        logging.debug("Trace start detected: %d", trace_event['ts'])
                        self.trace_ts_start = trace_event['ts']
                    if self.trace_ts_start is None and \
                            (trace_event['name'] == 'navigationStart' or
                                trace_event['name'] == 'fetchStart') and \
                            trace_event['cat'].find('rail') > -1:
                        logging.debug("Trace start detected: %d", trace_event['ts'])
                        self.trace_ts_start = trace_event['ts']
                    if trace_event['name'] == 'Screenshot' and \
                            trace_event['cat'].find('devtools.screenshot') > -1:
                        keep_event = False
                        process_event = False
                        self.process_screenshot(trace_event)
                if 'cat' in trace_event:
                    if trace_event['cat'] not in self.trace_event_counts:
                        self.trace_event_counts[trace_event['cat']] = 0
                    self.trace_event_counts[trace_event['cat']] += 1
                    if not self.job['keep_netlog'] and trace_event['cat'] == 'netlog':
                        keep_event = False
                    if 'discard_trace_content' in self.job and self.job['discard_trace_content'] and trace_event['cat'] == 'content':
                        keep_event = False
                    if process_event and self.trace_parser is not None:
                        self.trace_parser.ProcessTraceEvent(trace_event)
                if keep_event:
                    out += ",\n" + json.dumps(trace_event)
            if self.trace_file is not None and len(out):
                self.trace_file.write(out)

    def process_screenshot(self, trace_event):
        """Process an individual screenshot event"""
        if self.trace_ts_start is not None and 'args' in trace_event and \
                'snapshot' in trace_event['args']:
            ms_elapsed = int(round(float(trace_event['ts'] - self.trace_ts_start) / 1000.0))
            if ms_elapsed >= 0:
                img = trace_event['args']['snapshot']
                path = '{0}{1:06d}.jpg'.format(self.video_prefix, ms_elapsed)
                logging.debug("Video frame (%f): %s", trace_event['ts'], path)
                # Sample frames at at 100ms intervals for the first 20 seconds,
                # 500ms for 20-40seconds and 2 second intervals after that
                min_interval = 100
                if ms_elapsed > 40000:
                    min_interval = 2000
                elif ms_elapsed > 20000:
                    min_interval = 500
                keep_image = True
                if self.last_image is not None:
                    elapsed_interval = ms_elapsed - self.last_image["time"]
                    if elapsed_interval < min_interval:
                        keep_image = False
                        if self.pending_image is not None:
                            logging.debug("Discarding pending image: %s",
                                          self.pending_image["path"])
                        self.pending_image = {"image": str(img),
                                              "time": int(ms_elapsed),
                                              "path": str(path)}
                if keep_image:
                    is_duplicate = False
                    if self.pending_image is not None:
                        if self.pending_image["image"] == img:
                            is_duplicate = True
                    elif self.last_image is not None and \
                            self.last_image["image"] == img:
                        is_duplicate = True
                    if is_duplicate:
                        logging.debug('Dropping duplicate image: %s', path)
                    else:
                        # write both the pending image and the current one if
                        # the interval is double the normal sampling rate
                        if self.last_image is not None and self.pending_image is not None and \
                                self.pending_image["image"] != self.last_image["image"]:
                            elapsed_interval = ms_elapsed - self.last_image["time"]
                            if elapsed_interval > 2 * min_interval:
                                pending = self.pending_image["path"]
                                with open(pending, 'wb') as image_file:
                                    image_file.write(base64.b64decode(self.pending_image["image"]))
                        self.pending_image = None
                        with open(path, 'wb') as image_file:
                            self.last_image = {"image": str(img),
                                               "time": int(ms_elapsed),
                                               "path": str(path)}
                            image_file.write(base64.b64decode(img))

class WebKitGTKInspector():
    """Interface for communicating with the WebKitGTK remote inspector protocol"""
    def __init__(self):
        self.connection = None
        self.background_thread = None
        self.backend_hash = '3EEFCD78DEE7F8F2E5348226F4B5DD9AFE917947'.encode('ascii') + b'\x00'
        self.lock = threading.Lock()
        self.targets_updated = threading.Event()
        self.targets = []
        self.automation_id = str(uuid.uuid4()).encode('ascii') + b"\x00\x00\x00\x00\x25"
        self.client_id = None
        self.target = None
        self.automation_client = None
        self.automation_target = None
        self.messages = multiprocessing.JoinableQueue()

    def read_thread(self):
        while self.connection is not None:
            fail = False
            try:
                # Read the data length
                data = self.read_bytes(5)
                flag = data[4]
                if flag == 1:
                    size = struct.unpack('!L', data[:4])[0]
                    if size > 0:
                        payload = self.read_bytes(size)
                        # parse the message name from the beginning of the buffer
                        message_name, payload = self.parse_string(payload)
                        if message_name is not None:
                            try:
                                if message_name == 'SetTargetList':
                                    self.SetTargetList(payload)
                                elif message_name == 'SendMessageToFrontend':
                                    self.SendMessageToFrontend(payload)
                            except Exception:
                                logging.exception("Error processing message")
                        else:
                            logging.critical('Invalid message')
                            fail = True
                else:
                    logging.critical('Invalid message flag')
                    fail = True
            except socket.timeout:
                pass
            except Exception:
                time.sleep(0.1)
            if fail:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.connection = None

    def read_bytes(self, size):
        """Keep reading until we get len bytes"""
        remaining = size
        data = b''
        while self.connection is not None and remaining > 0:
            chunk = self.connection.recv(remaining)
            remaining -= len(chunk)
            data += chunk
        return data
    
    def connect(self, port, timeout):
        self.connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connection.settimeout(1)
        logging.debug('WebKitGTKInspector Connecting...')
        self.connection.connect(('127.0.0.1', port))
        logging.debug('WebKitGTKInspector socket Connected')
        self.background_thread = threading.Thread(target=self.read_thread)
        self.background_thread.start()
        self.targets_updated.clear()
        self.send_raw_message("StartAutomationSession", self.automation_id)
        # Wait up to 10 seconds to get the target list
        self.targets_updated.wait(timeout)
        self.start()
        logging.debug('WebKitGTKInspector Connected')
    
    def close(self):
        if self.connection is not None:
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            self.connection = None
        if self.background_thread is not None:
            self.background_thread.join(10)
        # Flush all of the pending messages
        try:
            while self.messages.get_nowait():
                self.messages.task_done()
        except Exception:
            pass
        self.messages.close()

    def start_processing_trace(self, path_base, video_prefix, options, job, task, start_timestamp, keep_timeline):
        """ Not Implemented """
        pass

    def stop_processing_trace(self, job):
        """ Not Implemented """
        pass

    def start(self):
        """Send the Setup command to get the list of json targets"""
        if self.target is not None and self.client_id is not None:
            self.send_raw_message("Setup", self.client_id + self.target)
        if self.automation_target is not None and self.automation_client is not None:
            self.send_raw_message("Setup", self.automation_client + self.automation_target)

    def send(self, command):
        """Send the JSON command to the browser"""
        if command is not None:
            msg = None
            if command.find('"method":"Automation.') >= 0:
                if self.automation_target is not None and self.automation_client is not None:
                    msg = self.automation_client
                    msg += self.automation_target
                else:
                    logging.debug('No automation target available')
            elif self.target is not None and self.client_id is not None:
                msg = self.client_id
                msg += self.target
            if msg is not None:
                msg += command.encode('utf-8')
                msg += b'\x00'
                self.send_raw_message("SendMessageToBackend", msg)

    def send_raw_message(self, name, data=None):
        """Send a raw DBUS message over the TCP connection"""
        """ | message size - 4 bytes | byte order 0x1 - 1 byte | Null-terminated message name | raw message payload | """
        if self.connection is not None:
            size = len(name) + 1
            if data is not None:
                size += len(data)
            msg = bytearray()
            msg.extend(struct.pack('!L', size)) # Size of the payload
            msg.extend(b'\x01')                 # Network byte order
            msg.extend(name.encode('ascii'))    # The message name
            msg.extend(b'\x00')                 # Null-terminate the string
            if data is not None:
                msg.extend(data)                # Payload
            while len(msg):
                sent_bytes = 0
                try:
                    sent_bytes = self.connection.send(msg)
                except socket.timeout:
                    # keep looping send until we send it all
                    pass
                except Exception:
                    logging.exception("Error sending message")
                    break
                if sent_bytes > 0:
                    msg = msg[sent_bytes:]
    
    def parse_string(self, data):
        """Extract a gvariant string from the front of the data buffer"""
        string = None
        separator = data.find(b'\x00')
        if separator == 0:
            string = ''
            data = data[separator + 1:]
        elif separator > 0:
            string = data[:separator].decode('ascii')
            data = data[separator + 1:]
        return string, data

    def SetTargetList(self, msg):
        """Handle an inbound SetTargetList message"""
        # only handle the target list messages that have contents
        if len(msg) > 8:
            client_id = msg[:8]
            msg = msg[8:]
            # extract the individual targets
            self.lock.acquire()
            self.targets = []
            while (len(msg) >= 17):
                target_id = msg[:8]
                msg = msg[8:]
                target_type, msg = self.parse_string(msg)
                target_title, msg = self.parse_string(msg)
                target_url, msg = self.parse_string(msg)
                # 4 bytes + padding
                msg = msg[4:]
                record_size = 8 + len(target_type) + 1 + len(target_title) + 1 + len(target_url) + 1 + 4
                padding = 0
                remainder = record_size % 8
                if remainder > 0:
                    padding = 8 - remainder
                    msg = msg[padding:]
                if target_type is not None and target_title is not None and target_url is not None:
                    self.targets.append({
                        'id': target_id,
                        'type': target_type,
                        'title': target_title,
                        'url': target_url
                    })
                    # Default to the first WebPage target we find
                    if self.target is None and target_type == 'WebPage':
                        self.client_id = client_id
                        self.target = target_id
                    if self.automation_target is None and target_type == 'Automation':
                        self.automation_client = client_id
                        self.automation_target = target_id
            logging.debug(self.targets)
            self.lock.release()
            self.targets_updated.set()

    def SendMessageToFrontend(self, msg):
        """Incoming json message"""
        if len(msg) > 16:
            client_id = msg[:8]
            msg = msg[8:]
            target_id = msg[:8]
            if client_id in [self.client_id, self.automation_client] and target_id in [self.target, self.automation_target]:
                msg = msg[8:-1]
                command_string = msg.decode('utf-8')
                self.messages.put(command_string)

    def get_message(self, timeout):
        """Wait for and return a message from the queue"""
        message = None
        try:
            if timeout is None or timeout <= 0:
                message = self.messages.get_nowait()
            else:
                message = self.messages.get(True, timeout)
            self.messages.task_done()
        except Exception:
            pass
        return message
