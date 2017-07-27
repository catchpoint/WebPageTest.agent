# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for controlling browsers"""
import logging
import os

class Browsers(object):
    """Controller for handling several browsers"""
    def __init__(self, options, browsers, adb):
        import ujson as json
        self.options = options
        self.browsers = {k.lower(): v for k, v in browsers.items()}
        self.adb = adb
        android_file = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                    'android_browsers.json')
        with open(android_file, 'rb') as f_in:
            self.android_browsers = {k.lower(): v for k, v in json.load(f_in).items()}

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
        name = name.lower()
        if self.options.android:
            if 'customBrowser_package' in job:
                name = "chrome"
            separator = name.find('-')
            if separator >= 0:
                name = name[separator + 1:].strip()
            if name in self.android_browsers:
                config = self.android_browsers[name]
                config['all'] = self.android_browsers
                if config['type'] == 'chrome':
                    from .chrome_android import ChromeAndroid
                    browser = ChromeAndroid(self.adb, config, self.options, job)
                if config['type'] == 'blackbox':
                    from .blackbox_android import BlackBoxAndroid
                    browser = BlackBoxAndroid(self.adb, config, self.options, job)
        elif 'type' in job and job['type'] == 'traceroute':
            from .traceroute import Traceroute
            browser = Traceroute(self.options, job)
        elif name in self.browsers and 'exe' in self.browsers[name]:
            if 'type' in self.browsers[name] and self.browsers[name]['type'] == 'Firefox':
                from .firefox import Firefox
                browser = Firefox(self.browsers[name]['exe'], self.options, job)
            else:
                from .chrome_desktop import ChromeDesktop
                browser = ChromeDesktop(self.browsers[name]['exe'], self.options, job)
        return browser
