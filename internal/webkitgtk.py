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

    def launch(self, job, task):
        """Launch the browser"""
        dirs = ['~/.config/epiphany', '~/.local/share/epiphany', '~/.local/share/webkitgtk']
        for directory in dirs:
            directory = os.path.expanduser(directory)
            if os.path.exists(directory):
                logging.debug("Removing %s", directory)
                try:
                    shutil.rmtree(directory)
                except Exception:
                    pass
        if not task['cached']:
            cache_dir = os.path.expanduser('~/.cache/epiphany')
            if os.path.exists(cache_dir):
                try:
                    shutil.rmtree(cache_dir)
                except Exception:
                    pass
        # Create a session state file with the window dimensions we want
        config_dir = os.path.expanduser('~/.config')
        if not os.path.isdir(config_dir):
            os.mkdir(config_dir)
        config_dir += '/epiphany'
        if not os.path.isdir(config_dir):
            os.mkdir(config_dir)
        state_file = config_dir + "/session_state.xml"
        with open(state_file, 'wt') as f_out:
            f_out.write('<?xml version="1.0"?>\n')
            f_out.write('<session>\n')
            f_out.write('	<window x="0" y="0" width="{}" height="{}" active-tab="0" role="epiphany-window-25336e5d">\n'.format(task['width'], task['height']))
            f_out.write('	 	 <embed url="about:blank" title="Blank page"/>\n')
            f_out.write('	 </window>\n')
            f_out.write('</session>\n')

        os.environ["WEBKIT_INSPECTOR_SERVER"] = "127.0.0.1:{}".format(task['port'])
        if self.path.find(' ') > -1:
            command_line = '"{0}"'.format(self.path)
        else:
            command_line = self.path
        # re-try launching and connecting a few times if necessary
        connected = False
        DesktopBrowser.launch_browser(self, command_line)
        if DevtoolsBrowser.connect(self, task):
            connected = True
        if connected:
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
