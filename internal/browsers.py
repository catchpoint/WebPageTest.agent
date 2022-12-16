# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Main entry point for controlling browsers"""
import logging
import os


class Browsers(object):
    """Controller for handling several browsers"""
    def __init__(self, options, browsers, adb, ios):
        try:
            import ujson as json
        except BaseException:
            import json
        self.options = options
        self.browsers = None
        if browsers is not None:
            self.browsers = {k.lower(): v for k, v in browsers.items()}
        self.adb = adb
        self.ios = ios
        self.needs_exit = False
        android_file = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                    'android_browsers.json')
        with open(android_file, 'r') as f_in:
            self.android_browsers = {k.lower(): v for k, v in json.load(f_in).items()}

    def should_exit(self):
        """Tell the agent if we have device issues and need a reboot"""
        return self.needs_exit

    def is_ready(self):
        """Check to see if the configured browsers are ready to go"""
        ready = True
        if self.options.android and self.adb is not None:
            ready = self.adb.is_device_ready()
        elif self.options.iOS and self.ios is not None:
            ready = self.ios.is_device_ready()
            if not ready and not self.ios.device_connected:
                self.needs_exit = True
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
        if name.startswith('ie '):
            name = 'ie'
        if self.options.android:
            if 'customBrowser_package' in job:
                name = "chrome"
            separator = name.rfind('-')
            if separator >= 0:
                name = name[separator + 1:].strip()
            mode = None
            separator = name.find('(')
            if separator >= 0:
                end = name.find(")", separator)
                if end >= 0:
                    mode = name[separator + 1:end].strip()
                name = name[:separator].strip()
            if name in self.android_browsers:
                config = self.android_browsers[name]
                config['all'] = self.android_browsers
                if mode is not None:
                    config['mode'] = mode
                if config['type'] == 'chrome':
                    from .chrome_android import ChromeAndroid
                    browser = ChromeAndroid(self.adb, config, self.options, job)
                if config['type'] == 'blackbox':
                    from .blackbox_android import BlackBoxAndroid
                    browser = BlackBoxAndroid(self.adb, config, self.options, job)
        elif self.options.iOS and self.ios is not None:
            from .safari_ios import iWptBrowser
            browser = iWptBrowser(self.ios, self.options, job)
        elif 'type' in job and job['type'] == 'traceroute':
            from .traceroute import Traceroute
            browser = Traceroute(self.options, job)
        elif name in self.browsers and 'type' in self.browsers[name] and self.browsers[name]['type'] == 'iOS Simulator':
            job['browser_info'] = self.browsers[name]
            from .safari_ios_simulator import SafariSimulator
            browser = SafariSimulator(self.browsers[name], self.options, job)
        elif name in self.browsers and 'exe' in self.browsers[name]:
            job['browser_info'] = self.browsers[name]
            if 'type' in self.browsers[name] and self.browsers[name]['type'] == 'Firefox':
                from .firefox import Firefox
                browser = Firefox(self.browsers[name]['exe'], self.options, job)
            elif 'type' in self.browsers[name] and self.browsers[name]['type'] == 'Edge':
                from .microsoft_edge import Edge
                browser = Edge(self.browsers[name]['exe'], self.options, job)
            elif 'type' in self.browsers[name] and self.browsers[name]['type'] == 'IE':
                from .internet_explorer import InternetExplorer
                browser = InternetExplorer(self.browsers[name]['exe'], self.options, job)
            elif 'type' in self.browsers[name] and self.browsers[name]['type'] == 'Safari':
                from .safari_webdriver import SafariWebDriver
                browser = SafariWebDriver(self.browsers[name]['exe'], self.options, job)
            elif 'type' in self.browsers[name] and self.browsers[name]['type'] == 'WebKitGTK':
                from .webkitgtk import WebKitGTK
                browser = WebKitGTK(self.browsers[name]['exe'], self.options, job)
            else:
                from .chrome_desktop import ChromeDesktop
                browser = ChromeDesktop(self.browsers[name]['exe'], self.options, job)
        return browser
