# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Logic for controlling a desktop Chrome browser"""
from .desktop_browser import DesktopBrowser
from .devtools_browser import DevtoolsBrowser

CHROME_COMMAND_LINE_OPTIONS = [
    '--disable-background-networking',
    '--no-default-browser-check',
    '--no-first-run',
    '--process-per-tab',
    '--new-window',
    '--silent-debugger-extension-api',
    '--disable-infobars',
    '--disable-translate',
    '--disable-notifications',
    '--disable-desktop-notifications',
    '--allow-running-insecure-content',
    '--disable-component-update',
    '--disable-background-downloads',
    '--disable-add-to-shelf',
    '--disable-client-side-phishing-detection',
    '--disable-datasaver-prompt',
    '--disable-default-apps',
    '--disable-domain-reliability',
    '--safebrowsing-disable-auto-update',
    '--disable-background-timer-throttling',
    '--host-rules="MAP cache.pack.google.com 127.0.0.1"'
]

START_PAGE = 'about:blank'

class ChromeDesktop(DesktopBrowser, DevtoolsBrowser):
    """Desktop Chrome"""
    def __init__(self, path, options, job):
        self.options = options
        DesktopBrowser.__init__(self, path, job)
        DevtoolsBrowser.__init__(self, job)

    def launch(self, task):
        """Launch the browser"""
        args = CHROME_COMMAND_LINE_OPTIONS
        args.extend(['--window-position="0,0"',
                     '--window-size="{0:d},{1:d}"'.format(task['width'], task['height'])])
        args.append('--remote-debugging-port={0:d}'.format(task['port']))
        if 'profile' in task:
            args.append('--user-data-dir="{0}"'.format(task['profile']))
        if self.options.xvfb:
            args.append('--disable-gpu')
        if self.path.find(' ') > -1:
            command_line = '"{0}"'.format(self.path)
        else:
            command_line = self.path
        command_line += ' ' + ' '.join(args)
        DesktopBrowser.launch_browser(self, command_line)

    def run_task(self, task):
        """Run an individual test"""
        if DevtoolsBrowser.connect(self, task):
            DevtoolsBrowser.prepare_browser(self)
            DevtoolsBrowser.navigate(self, START_PAGE)
            DesktopBrowser.wait_for_idle(self)
            DevtoolsBrowser.run_task(self, task)
            DevtoolsBrowser.disconnect(self)

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        DevtoolsBrowser.on_start_recording(self, task)
        DesktopBrowser.on_start_recording(self, task)

    def on_stop_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        DesktopBrowser.on_stop_recording(self, task)
        DevtoolsBrowser.on_stop_recording(self, task)
