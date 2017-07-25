# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Logic for controlling a desktop Chrome browser"""
import gzip
import os
import platform
import subprocess
import shutil
import time
from .desktop_browser import DesktopBrowser
from .devtools_browser import DevtoolsBrowser

CHROME_COMMAND_LINE_OPTIONS = [
    '--disable-background-networking',
    '--no-default-browser-check',
    '--no-first-run',
    '--process-per-tab',
    '--new-window',
    '--disable-infobars',
    '--disable-translate',
    '--disable-notifications',
    '--disable-desktop-notifications',
    '--disable-save-password-bubble',
    '--allow-running-insecure-content',
    '--disable-component-update',
    '--disable-background-downloads',
    '--disable-add-to-shelf',
    '--disable-client-side-phishing-detection',
    '--disable-datasaver-prompt',
    '--disable-default-apps',
    '--disable-domain-reliability',
    '--safebrowsing-disable-auto-update',
    '--disable-background-timer-throttling'
]

HOST_RULES = [
    '"MAP cache.pack.google.com 127.0.0.1"',
    '"MAP clients1.google.com 127.0.0.1"'
]

START_PAGE = 'data:text/html,%3Chtml%3E%0D%0A%3Chead%3E%0D%0A%3Cstyle%3E%0D%0Abody%20%7B'\
             'background-color%3A%20white%3B%20margin%3A%200%3B%7D%0D%0A%23o%20%7Bwidth'\
             '%3A100%25%3B%20height%3A%20100%25%3B%20background-color%3A%20%23DE640D%3B'\
             '%7D%0D%0A%3C%2Fstyle%3E%0D%0A%3Cscript%3E%0D%0Awindow.addEventListener%28%27'\
             'beforeunload%27%2C%20function%28%29%20%7B%0D%0A%20%20var%20o%20%3D%20'\
             'document.getElementById%28%27o%27%29%0D%0A%20%20o.parentNode.removeChild'\
             '%28o%29%3B%0D%0A%7D%29%3B%0D%0A%3C%2Fscript%3E%0D%0A%3C%2Fhead%3E%0D%0A%3Cbody%3E%3C'\
             'div%20id%3D%27o%27%3E%3C%2Fdiv%3E%3C%2Fbody%3E%0D%0A%3C%2Fhtml%3E'

class ChromeDesktop(DesktopBrowser, DevtoolsBrowser):
    """Desktop Chrome"""
    def __init__(self, path, options, job):
        self.options = options
        DesktopBrowser.__init__(self, path, options, job)
        use_devtools_video = True if self.job['capture_display'] is None else False
        DevtoolsBrowser.__init__(self, options, job, use_devtools_video=use_devtools_video)
        self.start_page = 'about:blank' if use_devtools_video else START_PAGE
        self.connected = False

    def launch(self, job, task):
        """Launch the browser"""
        args = list(CHROME_COMMAND_LINE_OPTIONS)
        host_rules = list(HOST_RULES)
        if 'host_rules' in task:
            host_rules.extend(task['host_rules'])
        args.append('--host-resolver-rules=' + ','.join(host_rules))
        args.extend(['--window-position="0,0"',
                     '--window-size="{0:d},{1:d}"'.format(task['width'], task['height'])])
        args.append('--remote-debugging-port={0:d}'.format(task['port']))
        if 'ignoreSSL' in job and job['ignoreSSL']:
            args.append('--ignore-certificate-errors')
        if 'netlog' in job and job['netlog']:
            netlog_file = os.path.join(task['dir'], task['prefix']) + '_netlog.txt'
            args.append('--log-net-log="{0}"'.format(netlog_file))
        if 'profile' in task:
            args.append('--user-data-dir="{0}"'.format(task['profile']))
        if self.options.xvfb:
            args.append('--disable-gpu')
        if self.options.dockerized:
            args.append('--no-sandbox')
        if self.path.find(' ') > -1:
            command_line = '"{0}"'.format(self.path)
        else:
            command_line = self.path
        # JUST for testing
        args.append('--no-sandbox')
        command_line += ' ' + ' '.join(args)
        if 'addCmdLine' in job:
            command_line += ' ' + job['addCmdLine']
        command_line += ' ' + 'about:blank'
        # re-try launching and connecting a few times if necessary
        connected = False
        count = 0
        while not connected and count < 3:
            count += 1
            DesktopBrowser.launch_browser(self, command_line)
            if DevtoolsBrowser.connect(self, task):
                connected = True
            elif count < 3:
                DesktopBrowser.stop(self, job, task)
                if 'error' in task and task['error'] is not None:
                    task['error'] = None
                time.sleep(10)
        if connected:
            self.connected = True
            DevtoolsBrowser.prepare_browser(self, task)
            DevtoolsBrowser.navigate(self, self.start_page)
            DesktopBrowser.wait_for_idle(self)

    def run_task(self, task):
        """Run an individual test"""
        if self.connected:
            DevtoolsBrowser.run_task(self, task)

    def execute_js(self, script):
        """Run javascipt"""
        return DevtoolsBrowser.execute_js(self, script)

    def stop(self, job, task):
        if self.connected:
            DevtoolsBrowser.disconnect(self)
        DesktopBrowser.stop(self, job, task)
        # Make SURE the firefox processes are gone
        if platform.system() == "Linux":
            subprocess.call(['killall', '-9', 'chrome'])
        netlog_file = os.path.join(task['dir'], task['prefix']) + '_netlog.txt'
        if os.path.isfile(netlog_file):
            netlog_gzip = netlog_file + '.gz'
            with open(netlog_file, 'rb') as f_in:
                with gzip.open(netlog_gzip, 'wb', 7) as f_out:
                    shutil.copyfileobj(f_in, f_out)
            if os.path.isfile(netlog_gzip):
                os.remove(netlog_file)

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        DesktopBrowser.on_start_recording(self, task)
        DevtoolsBrowser.on_start_recording(self, task)

    def on_stop_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        DesktopBrowser.on_stop_recording(self, task)
        DevtoolsBrowser.on_stop_recording(self, task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        DesktopBrowser.on_start_processing(self, task)
        DevtoolsBrowser.on_start_processing(self, task)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DesktopBrowser.wait_for_processing(self, task)
