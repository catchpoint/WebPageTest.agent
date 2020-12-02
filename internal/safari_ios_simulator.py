# Copyright 2020 Catchpoint Systems LLC
# Use of this source code is governed by the Polyform Shield License
# found in the LICENSE file.
"""Logic for controlling a desktop WebKit GTK browser (Linux)"""
import logging
import os
import subprocess
import time
from .desktop_browser import DesktopBrowser
from .devtools_browser import DevtoolsBrowser

class SafariSimulator(DesktopBrowser, DevtoolsBrowser):
    """iOS Simulator"""
    def __init__(self, browser_info, options, job):
        """SafariSimulator"""
        self.browser_info = browser_info
        self.options = options
        DesktopBrowser.__init__(self, None, options, job)
        DevtoolsBrowser.__init__(self, options, job, use_devtools_video=False, is_webkit=True, is_ios=True)
        self.start_page = 'http://127.0.0.1:8888/orange.html'
        self.connected = False
        self.driver = None
        self.webinspector_proxy = None
        self.device_id = browser_info['device']['udid']
        self.rotate_simulator = False
        if 'rotate' in browser_info and browser_info['rotate']:
            self.rotate_simulator = True

    def launch(self, job, task):
        """ Launch the browser using Selenium (only first view tests are supported) """
        if not self.task['cached']:
            try:
                # Reset the simulator state (disabled for now until we know if we need it for sure)
                # subprocess.call(['xcrun', 'simctl', 'erase', self.device_id])

                # Start the simulator and browser
                from selenium import webdriver
                capabilities = webdriver.DesiredCapabilities.SAFARI.copy()
                capabilities['platformName'] = 'iOS'
                capabilities['safari:useSimulator'] = True
                capabilities['safari:deviceUDID'] = self.device_id
                self.driver = webdriver.Safari(desired_capabilities=capabilities)
                self.driver.get(self.start_page)

                # Try to move the simulator window
                if self.rotate_simulator:
                    script = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support', 'osx', 'RotateSimulator.app')
                else:
                    script = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support', 'osx', 'MoveSimulator.app')
                args = ['open', '-W', '-a', script]
                logging.debug(' '.join(args))
                subprocess.call(args)
                self.find_simulator_window()

                # find the webinspector socket
                webinspector_socket = None
                out = subprocess.check_output(['lsof', '-aUc', 'launchd_sim'], universal_newlines=True)
                if out:
                    for line in out.splitlines(keepends=False):
                        if line.endswith('com.apple.webinspectord_sim.socket'):
                            offset = line.find('/private')
                            if offset >= 0:
                                webinspector_socket = line[offset:]
                                break
                # Start the webinspector proxy
                if webinspector_socket is not None:
                    args = ['ios_webkit_debug_proxy', '-F', '-s', 'unix:' + webinspector_socket]
                    logging.debug(' '.join(args))
                    self.webinspector_proxy = subprocess.Popen(args)
                    if self.webinspector_proxy:
                        # Connect to WebInspector
                        task['port'] = 9222
                        if DevtoolsBrowser.connect(self, task):
                            self.connected = True
                            # Finish the startup init
                            DesktopBrowser.wait_for_idle(self)
                            DevtoolsBrowser.prepare_browser(self, task)
                            DevtoolsBrowser.navigate(self, self.start_page)
                            DesktopBrowser.wait_for_idle(self, 2)
            except Exception:
                logging.exception('Error starting the simulator')

    def find_simulator_window(self):
        """ Figure out where the simulator opened on screen for video capture """
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID
        )
        windowList = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
        for window in windowList:
            ownerName = window['kCGWindowOwnerName']
            if ownerName == "Simulator":
                x = int(window['kCGWindowBounds']['X'])
                y = int(window['kCGWindowBounds']['Y'])
                width = int(window['kCGWindowBounds']['Width'])
                height = int(window['kCGWindowBounds']['Height'])
                self.job['capture_rect'] = {
                    'x': x,
                    'y': y,
                    'width': width,
                    'height': height
                }
                logging.debug("Simulator window: %d,%d - %d x %d", x, y, width, height)
                found = True
                break

    def run_task(self, task):
        """Run an individual test (only first view is supported)"""
        if self.connected:
            DevtoolsBrowser.run_task(self, task)

    def execute_js(self, script):
        """Run javascipt"""
        return DevtoolsBrowser.execute_js(self, script)

    def stop(self, job, task):
        if self.connected:
            DevtoolsBrowser.disconnect(self)
        if self.webinspector_proxy:
            self.webinspector_proxy.terminate()
            self.webinspector_proxy.communicate()
            self.webinspector_proxy = None
        if self.driver is not None:
            self.driver.quit()
            self.driver = None
        DesktopBrowser.stop(self, job, task)
        # Make SURE the processes are gone
        if self.device_id is not None:
            subprocess.call(['xcrun', 'simctl', 'shutdown', self.device_id])
        else:
            subprocess.call(['xcrun', 'simctl', 'shutdown', 'all'])
        self.device_id = None
        from AppKit import NSWorkspace
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.localizedName() == 'Simulator':
                app.terminate()

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        DesktopBrowser.on_start_recording(self, task)
        DevtoolsBrowser.on_start_recording(self, task)

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        DesktopBrowser.on_stop_capture(self, task)
        DevtoolsBrowser.on_stop_capture(self, task)

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
        DevtoolsBrowser.wait_for_processing(self, task)
        DesktopBrowser.wait_for_processing(self, task)

    def grab_raw_screenshot(self):
        """Grab a screenshot using webdriver"""
        logging.debug('Capturing screen shot')
        return self.driver.get_screenshot_as_base64()
