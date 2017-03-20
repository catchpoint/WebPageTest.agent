# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for controlling browsers"""
import logging
import os

class Browsers(object):
    """Controller for handling several browsers"""
    def __init__(self, options, browsers, adb):
        self.options = options
        self.browsers = browsers
        self.adb = adb

    def is_ready(self):
        """Check to see if the configured browsers are ready to go"""
        ready = True
        if self.options.android and self.adb is not None:
            ready = self.adb.is_device_ready()
        else:
            for browser in self.browsers:
                if 'exe' in self.browsers[browser]:
                    exe = self.browsers[browser]['exe']
                    if not os.path.isfile(exe):
                        logging.critical("Browser executable is missing for %s: '%s'", browser, exe)
                        ready = False
        return ready

    def get_browser(self, name, job):
        """Return an instance of the browser logic"""
        browser = None
        if 'type' in job and job['type'] == 'traceroute':
            from .traceroute import Traceroute
            browser = Traceroute(self.options, job)
        elif name in self.browsers and 'exe' in self.browsers[name]:
            from .chrome_desktop import ChromeDesktop
            browser = ChromeDesktop(self.browsers[name]['exe'], self.options, job)
        return browser
