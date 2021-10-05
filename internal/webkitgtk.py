# Copyright 2020 Catchpoint Systems LLC
# Use of this source code is governed by the Polyform Shield License
# found in the LICENSE file.
"""Logic for controlling a desktop WebKit GTK browser (Linux)"""
import logging
import os
import platform
import shutil
import subprocess
from .desktop_browser import DesktopBrowser
from .devtools_browser import DevtoolsBrowser

class WebKitGTK(DesktopBrowser, DevtoolsBrowser):
    """Desktop WebKitGTK"""
    def __init__(self, path, options, job):
        """WebKitGTK"""
        self.options = options
        DesktopBrowser.__init__(self, path, options, job)
        DevtoolsBrowser.__init__(self, options, job, use_devtools_video=False, is_webkit=True)
        self.start_page = 'http://127.0.0.1:8888/orange.html'
        self.connected = False

    def shutdown(self):
        """Agent is dying NOW"""
        DevtoolsBrowser.shutdown(self)
        DesktopBrowser.shutdown(self)

    def launch(self, job, task):
        """Launch the browser (only first view tests are supported)"""
        if not self.task['cached']:
            os.environ["WEBKIT_INSPECTOR_SERVER"] = "127.0.0.1:{}".format(task['port'])
            if self.path.find(' ') > -1:
                command_line = '"{0}"'.format(self.path)
            else:
                command_line = self.path
            command_line += ' --automation-mode'
            # re-try launching and connecting a few times if necessary
            DesktopBrowser.launch_browser(self, command_line)
            if DevtoolsBrowser.connect(self, task):
                self.connected = True
                DesktopBrowser.wait_for_idle(self)
                DevtoolsBrowser.prepare_browser(self, task)
                DevtoolsBrowser.navigate(self, self.start_page)
                DesktopBrowser.wait_for_idle(self, 2)

    def run_task(self, task):
        """Run an individual test"""
        if self.connected:
            DevtoolsBrowser.run_task(self, task)

    def execute_js(self, script):
        """Run javascipt"""
        return DevtoolsBrowser.execute_js(self, script)

    def stop(self, job, task):
        if self.connected:
            DevtoolsBrowser.disconnect(self)
        DesktopBrowser.stop(self, job, task)
        # Make SURE the processes are gone
        if platform.system() == "Linux":
            subprocess.call(['killall', '-9', 'epiphany'])

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
