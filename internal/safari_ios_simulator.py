# Copyright 2020 Catchpoint Systems LLC
# Use of this source code is governed by the Polyform Shield License
# found in the LICENSE file.
"""Logic for controlling a desktop WebKit GTK browser (Linux)"""
import logging
import os
import subprocess
import sys
import time
from .desktop_browser import DesktopBrowser
from .devtools_browser import DevtoolsBrowser
if (sys.version_info >= (3, 0)):
    from time import monotonic
else:
    from monotonic import monotonic

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
        self.webinspector_proxy = None
        self.device_id = browser_info['device']['udid']
        self.rotate_simulator = False
        self.driver = None
        if 'rotate' in browser_info and browser_info['rotate']:
            self.rotate_simulator = True

    def shutdown(self):
        """Agent is dying NOW"""
        DevtoolsBrowser.shutdown(self)
        DesktopBrowser.shutdown(self)

    def prepare(self, job, task):
        """ Prepare the OS and simulator """
        subprocess.call(['sudo', 'xcode-select', '-s', '/Applications/Xcode.app'], timeout=60)
        if not task['cached'] and not self.options.ioswebdriver:
            logging.debug('Resetting simulator state')
            subprocess.call(['xcrun', 'simctl', 'erase', self.device_id], timeout=60)

    def launch(self, job, task):
        """ Launch the browser using Selenium (only first view tests are supported) """
        try:
            if self.options.ioswebdriver:
                if task['cached']:
                    raise Exception('Webdriver not supported for repeat view tests')
                logging.debug('Starting the simulator with webdriver')
                from selenium import webdriver
                capabilities = webdriver.DesiredCapabilities.SAFARI.copy()
                capabilities['platformName'] = 'iOS'
                capabilities['safari:useSimulator'] = True
                capabilities['safari:deviceUDID'] = self.device_id
                self.driver = webdriver.Safari(desired_capabilities=capabilities)
                self.driver.get(self.start_page)
            else:
                logging.debug('Booting the simulator')
                subprocess.call(['xcrun', 'simctl', 'boot', self.device_id], timeout=60)
                subprocess.call(['open', '-a', 'Simulator'], timeout=60)

                logging.debug('Opening Safari')
                subprocess.call(['xcrun', 'simctl', 'openurl', self.device_id, self.start_page], timeout=240)

            # find the webinspector socket
            webinspector_socket = None
            end_time = monotonic() + 30
            while webinspector_socket is None and monotonic() < end_time and not self.must_exit:
                try:
                    out = subprocess.check_output(['lsof', '-aUc', 'launchd_sim'], universal_newlines=True, timeout=10)
                    if out:
                        for line in out.splitlines(keepends=False):
                            if line.endswith('com.apple.webinspectord_sim.socket'):
                                offset = line.find('/private')
                                if offset >= 0:
                                    webinspector_socket = line[offset:]
                                    break
                except Exception:
                    pass
                if webinspector_socket is None:
                    time.sleep(2)

            # Try to move the simulator window
            logging.debug('Moving Simulator Window')
            script = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support', 'osx', 'MoveSimulator.app')
            args = ['open', '-W', '-a', script]
            logging.debug(' '.join(args))
            subprocess.call(args, timeout=60)
            time.sleep(2)
            if self.rotate_simulator:
                self.rotate_simulator_window()
                time.sleep(2)

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
                        self.check_simulator_orientation()
                        DevtoolsBrowser.prepare_browser(self, task)
                        DevtoolsBrowser.navigate(self, self.start_page)
                        DesktopBrowser.wait_for_idle(self, 2)
        except Exception:
            logging.exception('Error starting the simulator')
            self.job['reboot'] = True

    def rotate_simulator_window(self):
        """Run the apple script to rotate the window"""
        logging.debug('Rotating Simulator Window')
        script = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'support', 'osx', 'RotateSimulator.app')
        args = ['open', '-W', '-a', script]
        logging.debug(' '.join(args))
        subprocess.call(args, timeout=60)

    def check_simulator_orientation(self):
        """Make sure the simulator didn't remember an earlier rotation"""
        self.find_simulator_window()
        if 'capture_rect' in self.job:
            rotated = self.job['capture_rect']['width'] > self.job['capture_rect']['height']
            if rotated != self.rotate_simulator:
                logging.debug("Fixing simulator rotation")
                self.rotate_simulator_window()
                time.sleep(5)
                self.find_simulator_window()

    def find_simulator_window(self):
        """ Figure out where the simulator opened on screen for video capture """
        count = 0
        found = False
        attempts = 10
        if 'capture_rect' in self.job:
            del self.job['capture_rect']
        while count < attempts and not found and not self.must_exit:
            from Quartz import ( # pylint: disable=import-error
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
                    # Use the biggest window belonging to Simulator
                    if not found or (width * height) > (self.job['capture_rect']['width'] * self.job['capture_rect']['height']):
                        self.job['capture_rect'] = {
                            'x': x,
                            'y': y,
                            'width': width,
                            'height': height
                        }
                        logging.debug("Simulator window: %d,%d - %d x %d", x, y, width, height)
                        found = True
            if count < attempts and not found:
                time.sleep(0.5)

    def run_task(self, task):
        """Run an individual test (only first view is supported)"""
        if self.connected and not self.must_exit:
            DevtoolsBrowser.run_task(self, task)

    def execute_js(self, script):
        """Run javascipt"""
        return DevtoolsBrowser.execute_js(self, script)

    def stop(self, job, task):
        # Reset a rotated simulator
        if self.rotate_simulator:
            self.rotate_simulator_window()
            self.rotate_simulator_window()
            self.rotate_simulator_window()
        if self.connected:
            DevtoolsBrowser.disconnect(self)
        if self.webinspector_proxy:
            self.webinspector_proxy.terminate()
            self.webinspector_proxy.communicate()
            self.webinspector_proxy = None
        if self.driver is not None:
            self.driver.quit()
        # Stop the browser
        subprocess.call(['xcrun', 'simctl', 'terminate', self.device_id, 'com.apple.mobilesafari'], timeout=60)
        time.sleep(2)
        # Shutdown the simulator
        if self.device_id is not None:
            subprocess.call(['xcrun', 'simctl', 'shutdown', self.device_id], timeout=60)
        else:
            subprocess.call(['xcrun', 'simctl', 'shutdown', 'all'], timeout=60)
        self.device_id = None
        time.sleep(5)
        #Cleanup
        subprocess.call(['killall', 'Simulator'])
        time.sleep(5)
        DesktopBrowser.stop(self, job, task)

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
