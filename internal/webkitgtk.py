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
        dirs = ['~/.config/epiphany', '.local/share/epiphany', '.local/share/webkitgtk']
        for directory in dirs:
            directory = os.path.expanduser(directory)
            if os.path.exists(directory):
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
        os.environ["WEBKIT_INSPECTOR_SERVER"] = "127.0.0.1:{}".format(task['port'])
        if self.path.find(' ') > -1:
            command_line = '"{0}"'.format(self.path)
        else:
            command_line = self.path
        if 'addCmdLine' in job:
            command_line += ' ' + job['addCmdLine']
        command_line += ' ' + 'about:blank'
        # re-try launching and connecting a few times if necessary
        connected = False
        DesktopBrowser.launch_browser(self, command_line)
        if DevtoolsBrowser.connect(self, task):
            connected = True
        if connected:
            self.connected = True
            self.move_window(task)
            DesktopBrowser.wait_for_idle(self)
            DevtoolsBrowser.prepare_browser(self, task)
            DevtoolsBrowser.navigate(self, self.start_page)
            DesktopBrowser.wait_for_idle(self, 2)

    def move_window(self, task):
        """Move the browser window (Linux only for now)"""
        if platform.system() == "Linux":
            try:
                import Xlib
                from Xlib import display
                disp = display.Display()
                root = disp.screen().root
                width = task['width']
                height = task['height']
                window_ids = root.get_full_property(disp.intern_atom('_NET_CLIENT_LIST'), Xlib.X.AnyPropertyType).value
                for window_id in window_ids:
                    window = disp.create_resource_object('window', window_id)
                    logging.debug("%s :: %s", window.get_wm_class(), window.get_wm_name())
                    _, class_name = window.get_wm_class()
                    if class_name == 'Epiphany':
                        # First move it WAY negative to account for any border. It will be clipped to the top-left corner
                        window.configure(x=-1000, y=-1000, border_width=0, stack_mode=Xlib.X.Above)
                        disp.sync()
                        # Now resize it adjusting for the margins
                        geometry = window.get_geometry()
                        logging.debug("Current window position: %d, %d - %d x %d", geometry.x, geometry.y, geometry.width, geometry.height)
                        width += abs(geometry.x) * 2
                        height += abs(geometry.y) * 2
                        window.configure(width=width, height=height, border_width=0, stack_mode=Xlib.X.Above)
                        disp.sync()
                        geometry = window.get_geometry()
                        logging.debug("Current window position: %d, %d - %d x %d", geometry.x, geometry.y, geometry.width, geometry.height)
                        geometry = window.get_geometry()
            except Exception:
                logging.exception("Error moving the window")

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
