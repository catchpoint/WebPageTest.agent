# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for controlling browsers"""
import logging
import os

class Browsers(object):
    """Controller for handling several browsers"""
    def __init__(self, options, browsers):
        self.options = options
        self.browsers = browsers

    def is_ready(self):
        """Check to see if the configured browsers are ready to go"""
        ready = True
        for browser in self.browsers:
            if 'exe' in self.browsers[browser]:
                exe = self.browsers[browser]['exe']
                logging.debug("Checking '%s'", exe)
                if not os.path.isfile(exe):
                    logging.critical("Browser executable is missing for %s: '%s'", browser, exe)
                    ready = False
        return ready

    def get_browser(self, name, job):
        """Return an instance of the browser logic"""
        browser = None
        # only support desktop browsers for now
        if name in self.browsers and 'exe' in self.browsers[name]:
            from .chrome_desktop import ChromeBrowser
            browser = ChromeBrowser(self.browsers[name]['exe'], job)
        return browser
