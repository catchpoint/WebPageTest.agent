# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for webdriver browsers"""
import os
import shutil
from .desktop_browser import DesktopBrowser

class Firefox(DesktopBrowser):
    """Firefox"""

    def __init__(self, path, options, job):
        DesktopBrowser.__init__(self, path, options, job)
        self.job = job
        self.task = None
        self.options = options
        self.path = path

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        DesktopBrowser.prepare(self, job, task)
        profile_template = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                        'profiles', 'Firefox')
        if not task['cached'] and os.path.isdir(profile_template):
            try:
                if os.path.isdir(task['profile']):
                    shutil.rmtree(task['profile'])
                shutil.copytree(profile_template, task['profile'])
            except Exception:
                pass

    def launch(self, _job, task):
        """Launch the browser"""
        args = ['-profile', '"{0}"'.format(task['profile']),
                '-no-remote',
                '-width', str(task['width']),
                '-height', str(task['height']),
                'about:blank']
        if self.path.find(' ') > -1:
            command_line = '"{0}"'.format(self.path)
        else:
            command_line = self.path
        command_line += ' ' + ' '.join(args)
        DesktopBrowser.launch_browser(self, command_line)

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        DesktopBrowser.on_start_recording(self, task)

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        DesktopBrowser.on_stop_recording(self, task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        DesktopBrowser.on_start_processing(self, task)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DesktopBrowser.wait_for_processing(self, task)
