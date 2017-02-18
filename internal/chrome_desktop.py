# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Logic for controlling a desktop Chrome browser"""
import logging
import os
import shutil
import time

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
START_BROWSER_TIME_LIMIT = 30
SCREEN_SHOT_SIZE = 400

class ChromeBrowser(object):
    """Desktop Chrome"""
    def __init__(self, path, job):
        self.path = path
        self.proc = None
        self.job = job

    def prepare(self, task):
        """Prepare the profile/OS for the browser"""
        try:
            from .os_util import kill_all
            from .os_util import flush_dns
            logging.debug("Preparing browser")
            kill_all(os.path.basename(self.path), True)
            flush_dns()
            if 'profile' in task:
                if not task['cached'] and os.path.isdir(task['profile']):
                    shutil.rmtree(task['profile'])
                if not os.path.isdir(task['profile']):
                    os.makedirs(task['profile'])
        except BaseException as err:
            logging.critical("Exception preparing ChromeBrowser: %s", err.__str__)

    def launch(self, task):
        """Launch the browser"""
        from .os_util import launch_process
        logging.debug("Launching browser")
        args = [self.path]
        args.extend(CHROME_COMMAND_LINE_OPTIONS)
        args.extend(['--window-position="0,0"',
                     '--window-size="{0:d},{1:d}"'.format(task['width'], task['height'])])
        args.append('--remote-debugging-port={0:d}'.format(task['port']))
        if 'profile' in task:
            args.append('--user-data-dir="{0}"'.format(task['profile']))
        args.append(START_PAGE)
        self.proc = launch_process(args)

    def stop(self):
        """Terminate the browser (gently at first but forced if needed)"""
        from .os_util import stop_process
        from .os_util import kill_all
        logging.debug("Stopping browser")
        if self.proc:
            kill_all(os.path.basename(self.path), False)
            stop_process(self.proc)
            self.proc = None

    def run_task(self, task):
        """Run an individual test"""
        from internal.devtools import DevTools
        devtools = DevTools(self.job, task)
        if devtools.connect(START_BROWSER_TIME_LIMIT):
            logging.debug("Devtools connected")
            end_time = time.clock() + task['time_limit']
            while len(task['script']) and time.clock() < end_time:
                command = task['script'].pop(0)
                if command['record']:
                    devtools.start_recording()
                self.process_command(devtools, command)
                if command['record']:
                    devtools.wait_for_page_load()
                    devtools.stop_recording()
                    if self.job['pngss']:
                        screen_shot = os.path.join(task['dir'], task['prefix'] + 'screen.png')
                        devtools.grab_screenshot(screen_shot, png=True)
                    else:
                        screen_shot = os.path.join(task['dir'], task['prefix'] + 'screen.jpg')
                        devtools.grab_screenshot(screen_shot, png=False)
            devtools.close()
        else:
            task['error'] = "Error connecting to dev tools interface"
            logging.critical(task.error)

    def process_command(self, devtools, command):
        """Process an individual script command"""
        if command['command'] == 'navigate':
            devtools.send_command('Page.navigate', {'url': command['target']})

        