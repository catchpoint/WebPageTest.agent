# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for browsers that speak the dev tools protocol"""
import logging
import os
import monotonic
import constants

class DevtoolsBrowser(object):
    """Devtools Browser base"""
    def __init__(self, job):
        self.devtools_job = job
        self.devtools = None

    def connect(self, task):
        """Connect to the dev tools interface"""
        ret = False
        from internal.devtools import DevTools
        self.devtools = DevTools(self.devtools_job, task)
        if self.devtools.connect(constants.START_BROWSER_TIME_LIMIT):
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
            self.devtools.close()

    def run_task(self, task):
        """Run an individual test"""
        if self.devtools is not None:
            logging.debug("Devtools connected")
            end_time = monotonic.monotonic() + task['time_limit']
            while len(task['script']) and monotonic.monotonic() < end_time:
                command = task['script'].pop(0)
                if command['record']:
                    self.devtools.start_recording()
                self.process_command(command)
                if command['record']:
                    self.devtools.wait_for_page_load()
                    self.devtools.stop_recording()
                    if self.devtools_job['pngss']:
                        screen_shot = os.path.join(task['dir'], task['prefix'] + 'screen.png')
                        self.devtools.grab_screenshot(screen_shot, png=True)
                    else:
                        screen_shot = os.path.join(task['dir'], task['prefix'] + 'screen.jpg')
                        self.devtools.grab_screenshot(screen_shot, png=False)

    def process_command(self, command):
        """Process an individual script command"""
        if command['command'] == 'navigate':
            self.devtools.send_command('Page.navigate', {'url': command['target']})
