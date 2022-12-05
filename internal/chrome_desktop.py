# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Logic for controlling a desktop Chrome browser"""
import gzip
import logging
import os
import platform
import subprocess
import shutil
import threading
import time
from .desktop_browser import DesktopBrowser
from .devtools_browser import DevtoolsBrowser
from .support.netlog import Netlog
try:
    import ujson as json
except BaseException:
    import json

CHROME_COMMAND_LINE_OPTIONS = [
    '--allow-running-insecure-content',
    '--disable-background-networking',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-breakpad',
    '--disable-client-side-phishing-detection',
    '--disable-component-update',
    '--disable-default-apps',
    '--disable-domain-reliability',
    '--disable-fetching-hints-at-navigation-start',
    '--disable-hang-monitor',
    '--disable-ipc-flooding-protection',
    '--disable-prompt-on-repost',
    '--disable-renderer-backgrounding',
    '--disable-site-isolation-trials',
    '--disable-sync',
    '--metrics-recording-only',
    '--mute-audio',
    '--new-window',
    '--no-default-browser-check',
    '--no-first-run',
    '--password-store=basic',
    '--use-mock-keychain',
]

HOST_RULES = [
    '"MAP cache.pack.google.com 127.0.0.1"',
    '"MAP clients1.google.com 127.0.0.1"',
    '"MAP edge.microsoft.com 127.0.0.1"',
    '"MAP laptop-updates.brave.com 127.0.0.1"',
    '"MAP offlinepages-pa.googleapis.com 127.0.0.1"',
    '"MAP optimizationguide-pa.googleapis.com 127.0.0.1"',
    '"MAP redirector.gvt1.com 127.0.0.1"',
    '"MAP update.googleapis.com 127.0.0.1"',
]

ENABLE_CHROME_FEATURES = [
]

DISABLE_CHROME_FEATURES = [
    'AutofillServerCommunication',
    'CalculateNativeWinOcclusion',
    'HeavyAdPrivacyMitigations',
    'InterestFeedContentSuggestions',
    'MediaRouter',
    'OfflinePagesPrefetching',
    'OptimizationHints',
    'Translate',
]

ENABLE_BLINK_FEATURES = [
]

class ChromeDesktop(DesktopBrowser, DevtoolsBrowser):
    """Desktop Chrome"""
    def __init__(self, path, options, job):
        self.options = options
        DesktopBrowser.__init__(self, path, options, job)
        use_devtools_video = True if self.job['capture_display'] is None else False
        DevtoolsBrowser.__init__(self, options, job, use_devtools_video=use_devtools_video)
        self.start_page = 'http://127.0.0.1:8888/orange.html'
        self.connected = False
        self.is_chrome = True
        self.netlog_fifo = None
        self.netlog_fp = None
        self.netlog_lock = threading.Lock()
        self.netlog_header = None
        self.netlog_thread = None
        self.netlog = None
        self.netlog_out = None
        self.netlog_event_count = 0

    def shutdown(self):
        """Shutdown the agent cleanly but mid-test"""
        DevtoolsBrowser.shutdown(self)
        DesktopBrowser.shutdown(self)

    def launch(self, job, task):
        """Launch the browser"""
        self.install_policy()
        args = list(CHROME_COMMAND_LINE_OPTIONS)
        features = list(ENABLE_CHROME_FEATURES)
        disable_features = list(DISABLE_CHROME_FEATURES)
        host_rules = list(HOST_RULES)
        if 'host_rules' in task:
            host_rules.extend(task['host_rules'])
        args.append('--host-resolver-rules=' + ','.join(host_rules))
        args.extend(['--window-position="0,0"',
                     '--window-size="{0:d},{1:d}"'.format(task['width'], task['height'])])
        args.append('--remote-debugging-port={0:d}'.format(task['port']))
        if 'ignoreSSL' in job and job['ignoreSSL']:
            args.append('--ignore-certificate-errors')
        using_fifo = False
        if platform.system() in ["Linux", "Darwin"]:
            # Stream the netlog to a pipe that we can read in realtime on Linux
            self.netlog_fifo = os.path.join(job['test_shared_dir'], 'netlog.fifo')
            try:            
                os.mkfifo(self.netlog_fifo, mode=0o777)
                self.netlog_thread = threading.Thread(target=self.stream_netlog)
                self.netlog_thread.start()
                args.append('--log-net-log="{0}"'.format(self.netlog_fifo))
                using_fifo = True
                job['streaming_netlog'] = True
            except Exception:
                logging.exception('Error creating netlog fifo')
        if using_fifo:
            args.append('--net-log-capture-mode=Everything')
        else:
            args.append('--net-log-capture-mode=IncludeSensitive')
        if not using_fifo and 'netlog' in job and job['netlog']:
            netlog_file = os.path.join(task['dir'], task['prefix']) + '_netlog.txt'
            args.append('--log-net-log="{0}"'.format(netlog_file))
        if 'profile' in task:
            args.append('--user-data-dir="{0}"'.format(task['profile']))
            self.setup_prefs(task['profile'])
        if self.options.xvfb:
            args.append('--disable-gpu')
        if self.options.dockerized:
            args.append('--no-sandbox')
        if platform.system() == "Linux":
            args.append('--disable-setuid-sandbox')
            args.append('--disable-dev-shm-usage')
        if len(features):
            args.append('--enable-features=' + ','.join(features))
        if len(ENABLE_BLINK_FEATURES):
            args.append('--enable-blink-features=' + ','.join(ENABLE_BLINK_FEATURES))
        if task['running_lighthouse']:
            args.append('--headless')
        
        if 'extensions' in job:
            extensions = job['extensions'].split(',')
            extensions_dir = os.path.join(job['persistent_dir'], 'extensions')
            paths = ''
            for extension in extensions:
                extension = extension.strip()
                if extension.isalnum():
                    extension_dir = os.path.join(extensions_dir, extension)
                    if os.path.exists(extension_dir):
                        if paths:
                            paths += ','
                        paths += extension_dir
                    else:
                        self.task['error'] = 'Missing extension: ' + extension
            if paths:
                args.append('--load-extension="{}"'.format(paths))

        # Disable site isolation if emulating mobile. It is disabled on
        # actual mobile Chrome (and breaks Chrome's CPU throttling)
        if 'mobile' in job and job['mobile']:
            disable_features.extend(['IsolateOrigins',
                                     'site-per-process'])
        elif 'throttle_cpu' in self.job and self.job['throttle_cpu'] > 1:
            disable_features.extend(['IsolateOrigins',
                                     'site-per-process'])
        args.append('--disable-features=' + ','.join(disable_features))

        if self.path.find(' ') > -1:
            command_line = '"{0}"'.format(self.path)
        else:
            command_line = self.path
        self.sanitize_shell_args(args)
        command_line += ' ' + ' '.join(args)
        if 'addCmdLine' in job:
            command_line += ' ' + self.sanitize_shell_string(job['addCmdLine'])
        command_line += ' ' + 'about:blank'
        # re-try launching and connecting a few times if necessary
        connected = False
        count = 0
        while not connected and count < 3:
            count += 1
            DesktopBrowser.launch_browser(self, command_line)
            if 'extensions' in job:
                DesktopBrowser.wait_for_idle(self)
            if DevtoolsBrowser.connect(self, task):
                connected = True
            elif count < 3:
                DesktopBrowser.stop(self, job, task)
                if 'error' in task and task['error'] is not None:
                    task['error'] = None
                # try launching the browser with no command-line options to
                # do any one-time startup initialization
                if count == 1:
                    bare_options = ['--disable-gpu']
                    if self.options.dockerized:
                        bare_options.append('--no-sandbox')
                    if platform.system() == "Linux":
                        bare_options.append('--disable-setuid-sandbox')
                    logging.debug('Launching browser with no options for configuration')
                    relaunch = '"{0}"'.format(self.path) + ' ' + ' '.join(bare_options)
                    DesktopBrowser.launch_browser(self, relaunch)
                    time.sleep(30)
                    DesktopBrowser.stop(self, job, task)
                time.sleep(10)
        if connected:
            self.connected = True
            self.profile_start('chrome.post_launch')
            self.profile_start('chrome.idle1')
            DesktopBrowser.wait_for_idle(self)
            self.profile_end('chrome.idle1')
            self.profile_start('chrome.prepare_browser')
            DevtoolsBrowser.prepare_browser(self, task)
            self.profile_end('chrome.prepare_browser')
            self.profile_start('chrome.start_page')
            DevtoolsBrowser.navigate(self, self.start_page)
            self.profile_end('chrome.start_page')
            # When throttling the CPU, Chrome sits in a busy loop so ony apply a short idle wait
            self.profile_start('chrome.idle2')
            DesktopBrowser.wait_for_idle(self, 2)
            self.profile_end('chrome.idle2')
            self.profile_end('chrome.post_launch')

    def stream_netlog(self):
        """Read the netlog fifo in a background thread"""
        with self.netlog_lock:
            self.netlog_fp = open(self.netlog_fifo, 'rt', encoding='utf-8')
        if self.netlog_fp:
            logging.debug('Netlog fifo connected...')
            with self.netlog_lock:
                self.netlog_header = []
            events_started = False
            for line in self.netlog_fp:
                line = line.strip()
                try:
                    with self.netlog_lock:
                        if events_started:
                            if self.recording and line.startswith('{'):
                                self.netlog_event_count += 1
                                line = line.strip(', ')
                                if self.netlog_out:
                                    if self.netlog_event_count > 1:
                                        self.netlog_out.write(",")
                                    self.netlog_out.write("\n")
                                    self.netlog_out.write(line)
                                if self.netlog:
                                    event = json.loads(line)
                                    self.netlog.add_event(event)
                        elif line.startswith('{"constants":'):
                            self.netlog_header.append(line)
                            if self.netlog_out:
                                self.netlog_out.write(line)
                                self.netlog_out.write("\n")
                            if self.netlog:
                                raw = json.loads(line.strip(', ') + '}')
                                if raw and 'constants' in raw:
                                    self.netlog.set_constants(raw['constants'])
                        elif line.startswith('"events": ['):
                            self.netlog_header.append(line)
                            if self.netlog_out:
                                self.netlog_out.write(line)
                            events_started = True
                except Exception:
                    logging.exception('Error processing netlog event')
            logging.debug('Netlog streaming thread exiting')

    def run_task(self, task):
        """Run an individual test"""
        if self.connected:
            DevtoolsBrowser.run_task(self, task)

    def alert_size(self, _alert_config, _task_dir, _prefix):
        '''Checks the agents file size and alert on certain percentage over avg byte size'''               
        self.alert_desktop_results(_alert_config, 'Chrome', _task_dir, _prefix)

    def execute_js(self, script):
        """Run javascipt"""
        return DevtoolsBrowser.execute_js(self, script)

    def stop(self, job, task):
        if self.connected:
            DevtoolsBrowser.disconnect(self)
        DesktopBrowser.stop(self, job, task)
        # Make SURE the chrome processes are gone
        if platform.system() == "Linux":
            subprocess.call(['killall', '-9', 'chrome'])
        with self.netlog_lock:
            if self.netlog_fp is not None:
                self.netlog_fp.close()
                self.netlog_fp = None
        if self.netlog_thread is not None:
            try:
                self.netlog_thread.join(30)
            except Exception:
                logging.exception('Error terminating netlog thread')
        self.netlog_thread = None
        if self.netlog_fifo is not None:
            try:
                os.unlink(self.netlog_fifo)
            except Exception:
                logging.debug('Error closing netlog fifo')
            self.netlog_fifo = None
        # Legacy netlog
        netlog_file = os.path.join(task['dir'], task['prefix']) + '_netlog.txt'
        if os.path.isfile(netlog_file):
            netlog_gzip = netlog_file + '.gz'
            with open(netlog_file, 'rb') as f_in:
                with gzip.open(netlog_gzip, 'wb', 7) as f_out:
                    shutil.copyfileobj(f_in, f_out)
            if os.path.isfile(netlog_gzip):
                os.remove(netlog_file)
        self.remove_policy()

    def setup_prefs(self, profile_dir):
        """Install our base set of preferences"""
        src = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                           'support', 'chrome', 'prefs.json')
        dest_dir = os.path.join(profile_dir, 'Default')
        try:
            os.makedirs(dest_dir)
            shutil.copy(src, os.path.join(dest_dir, 'Preferences'))
        except Exception:
            pass

    def install_policy(self):
        """Install the required policy list (Linux only right now)"""
        if platform.system() == "Linux":
            subprocess.call(['sudo', 'mkdir', '-p', '/etc/opt/chrome/policies/managed'])
            subprocess.call(['sudo', 'chmod', '-w', '/etc/opt/chrome/policies/managed'])
            subprocess.call(['sudo', 'mkdir', '-p', '/etc/chromium/policies/managed'])
            subprocess.call(['sudo', 'chmod', '-w', '/etc/chromium/policies/managed'])
            src = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                               'support', 'chrome', 'wpt_policy.json')
            subprocess.call(['sudo', 'cp', src,
                             '/etc/opt/chrome/policies/managed/wpt_policy.json'])
            subprocess.call(['sudo', 'cp', src,
                             '/etc/chromium/policies/managed/wpt_policy.json'])

    def remove_policy(self):
        """Remove the installed policy"""
        if platform.system() == "Linux":
            subprocess.call(['sudo', 'rm', '/etc/opt/chrome/policies/managed/wpt_policy.json'])
            subprocess.call(['sudo', 'rm', '/etc/chromium/policies/managed/wpt_policy.json'])

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        DesktopBrowser.on_start_recording(self, task)
        with self.netlog_lock:
            self.netlog_event_count = 0
            if self.netlog_fp:
                if 'netlog' in self.job and self.job['netlog']:
                    netlog_file = os.path.join(task['dir'], task['prefix']) + '_netlog.txt.gz'
                    self.netlog_out = gzip.open(netlog_file, 'wt', compresslevel=7, encoding='utf-8')
                self.netlog = Netlog()

                # set up the callbacks (these will happen on a background thread)
                if self.devtools is not None:
                    self.netlog.on_request_created = self.devtools.on_netlog_request_created                     # (request_id, request_info)
                    self.netlog.on_request_headers_sent = self.devtools.on_netlog_request_headers_sent           # (request_id, request_headers)
                    self.netlog.on_response_headers_received = self.devtools.on_netlog_response_headers_received # (request_id, response_headers)
                    self.netlog.on_response_bytes_received = self.devtools.on_netlog_response_bytes_received     # (request_id, filtered_bytes)
                    self.netlog.on_request_id_changed = self.devtools.on_request_id_changed                      # (request_id, new_request_id)

                if self.netlog_header:
                    for line in self.netlog_header:
                        if self.netlog_out:
                            self.netlog_out.write(line)
                            self.netlog_out.write("\n")
                        if line.startswith('{"constants":'):
                            raw = json.loads(line.strip(', ') + '}')
                            if raw and 'constants' in raw:
                                self.netlog.set_constants(raw['constants'])
        DevtoolsBrowser.on_start_recording(self, task)

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        DesktopBrowser.on_stop_capture(self, task)
        DevtoolsBrowser.on_stop_capture(self, task)

    def on_stop_recording(self, task):
        """Notification that we are about to stop an operation that needs to be recorded"""
        DesktopBrowser.on_stop_recording(self, task)
        with self.netlog_lock:
            # close out the netlog output
            if self.netlog_out:
                self.netlog_out.write("\n]}")
                self.netlog_out.close()
                self.netlog_out = None
            # Write out the netlog requests
            if self.netlog:
                requests = self.netlog.get_requests()
                self.netlog = None
                if requests is not None and len(requests):
                    netlog_requests = os.path.join(task['dir'], task['prefix']) + '_netlog_requests.json.gz'
                    with gzip.open(netlog_requests, 'wt', compresslevel=7, encoding='utf-8') as outfile:
                        json.dump(requests, outfile)
        DevtoolsBrowser.on_stop_recording(self, task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        DesktopBrowser.on_start_processing(self, task)
        DevtoolsBrowser.on_start_processing(self, task)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DevtoolsBrowser.wait_for_processing(self, task)
        DesktopBrowser.wait_for_processing(self, task)
