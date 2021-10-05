# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Support for Firefox"""
import logging
import os
import re
import sys

if (sys.version_info >= (3, 0)):
    unicode = str
try:
    import ujson as json
except BaseException:
    import json

from .desktop_browser import DesktopBrowser
from .firefox import Firefox

class FirefoxWebDriver(Firefox):
    """Firefox using the WebDriver interface"""
    def __init__(self, path, options, job):
        Firefox.__init__(self, path, options, job)
        self.driver = None

    def start_firefox(self, job, task):
        """Start Firefox using WebDriver"""
        if self.must_exit:
            return
        from selenium import webdriver # pylint: disable=import-error

        capabilities = webdriver.DesiredCapabilities.FIREFOX.copy()
        if 'ignoreSSL' in job and job['ignoreSSL']:
            capabilities['acceptInsecureCerts'] = True
        else:
            capabilities['acceptInsecureCerts'] = False

        capabilities['moz:firefoxOptions'] = {
            'binary': self.path,
            'args': ['-profile', task['profile']],
            'prefs': self.prepare_prefs(),
            "log": {"level": "error"},
            'env': {
                "MOZ_LOG_FILE": os.environ["MOZ_LOG_FILE"],
                "MOZ_LOG": os.environ["MOZ_LOG"]
            }
        }
        service_args = ["--marionette-port", "2828"]

        self.driver = webdriver.Firefox(desired_capabilities=capabilities, service_args=service_args)
        logging.debug(self.driver.capabilities)

        self.driver.set_page_load_timeout(task['time_limit'])
        if 'browserVersion' in self.driver.capabilities:
            self.browser_version = self.driver.capabilities['browserVersion']
        elif 'version' in self.driver.capabilities:
            self.browser_version = self.driver.capabilities['version']
        DesktopBrowser.wait_for_idle(self)
        self.driver.get(self.start_page)
        logging.debug('Resizing browser to %dx%d', task['width'], task['height'])
        self.driver.set_window_position(0, 0)
        self.driver.set_window_size(task['width'], task['height'])

        logging.debug('Installing extension')
        extension_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support', 'Firefox', 'extension')
        self.driver.install_addon(extension_path, temporary=True)

    def prepare_prefs(self):
        """Load the prefs file and configure them through webdriver"""
        prefs = {}
        prefs_file = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support', 'Firefox', 'profile', 'prefs.js')
        with open(prefs_file) as f_in:
            for line in f_in:
                matches = re.search(r'user_pref\("([^"]+)",[\s]*([^\)]*)[\s]*\);', line)
                if matches:
                    key = matches.group(1).strip()
                    value = self.get_pref_value(matches.group(2).strip())
                    if value is not None:
                        prefs[key] = value
        return prefs

    def driver_set_pref(self, key, value):
        """Set a Firefox pref at runtime"""
        if self.driver is not None:
            try:
                script = 'const { Services } = ChromeUtils.import("resource://gre/modules/Services.jsm");'
                script += 'Services.prefs.'
                if isinstance(value, bool):
                    script += 'setBoolPref'
                elif isinstance(value, (str, unicode)):
                    script += 'setStringPref'
                else:
                    script += 'setIntPref'
                script += '({0}, {1});'.format(json.dumps(key), json.dumps(value))
                logging.debug(script)
                self.driver.set_context(self.driver.CONTEXT_CHROME)
                self.driver.execute_script(script)
            except Exception:
                logging.exception("Error setting pref")
            finally:
                self.driver.set_context(self.driver.CONTEXT_CONTENT)

    def set_window_size(self, width, height):
        """Position the window"""
        self.driver.set_window_size(width, height)

    def disconnect_driver(self):
        """Disconnect WebDriver"""
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                logging.exception('Error quitting WebDriver')
            self.driver = None

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.driver is not None:
            try:
                self.driver.get(url)
            except Exception as err:
                logging.exception("Error navigating Firefox: %s", str(err))

    def execute_js(self, script):
        """Run JavaScript"""
        if self.must_exit:
            return
        ret = None
        if self.driver is not None:
            try:
                self.driver.set_script_timeout(30)
                ret = self.driver.execute_script(script)
            except Exception:
                logging.exception('Error executing script')
        return ret

    def run_js_file(self, file_name):
        """Execute one of our JS scripts"""
        if self.must_exit:
            return
        ret = None
        script = None
        script_file_path = os.path.join(self.script_dir, file_name)
        if os.path.isfile(script_file_path):
            with open(script_file_path, 'r') as script_file:
                script = script_file.read()
        if self.driver is not None and script is not None:
            try:
                self.driver.set_script_timeout(30)
                ret = self.driver.execute_script('return ' + script)
            except Exception:
                logging.exception('Error executing script file')
            if ret is not None:
                logging.debug(ret)
        return ret

    def grab_raw_screenshot(self):
        """Grab a screenshot using Marionette"""
        if self.must_exit:
            return
        return self.driver.get_screenshot_as_png()
