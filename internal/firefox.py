# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for webdriver browsers"""
import logging
import os
import re
import shutil
import time
import monotonic
from .desktop_browser import DesktopBrowser

""" Orange page that changes itself to white on navigation
<html>
<head>
<style>
body {background-color: white; margin: 0;}
#o {width:100%; height: 100%; background-color: #DE640D;}
</style>
<script>
window.addEventListener('beforeunload', function() {
  var o = document.getElementById('o')
  o.parentNode.removeChild(o);
});
</script>
</head>
<body><div id='o'></div></body>
</html>
"""
START_PAGE = 'data:text/html,%3Chtml%3E%0D%0A%3Chead%3E%0D%0A%3Cstyle%3E%0D%0Abody%20%7B'\
             'background-color%3A%20white%3B%20margin%3A%200%3B%7D%0D%0A%23o%20%7Bwidth'\
             '%3A100%25%3B%20height%3A%20100%25%3B%20background-color%3A%20%23DE640D%3B'\
             '%7D%0D%0A%3C%2Fstyle%3E%0D%0A%3Cscript%3E%0D%0Awindow.addEventListener%28%27'\
             'beforeunload%27%2C%20function%28%29%20%7B%0D%0A%20%20var%20o%20%3D%20'\
             'document.getElementById%28%27o%27%29%0D%0A%20%20o.parentNode.removeChild'\
             '%28o%29%3B%0D%0A%7D%29%3B%0D%0A%3C%2Fscript%3E%0D%0A%3C%2Fhead%3E%0D%0A%3Cbody%3E%3C'\
             'div%20id%3D%27o%27%3E%3C%2Fdiv%3E%3C%2Fbody%3E%0D%0A%3C%2Fhtml%3E'

class Firefox(DesktopBrowser):
    """Firefox"""

    def __init__(self, path, options, job):
        DesktopBrowser.__init__(self, path, options, job)
        self.job = job
        self.task = None
        self.options = options
        self.path = path
        self.event_name = None
        self.moz_log = None
        self.marionette = None

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        self.moz_log = os.path.join(task['dir'], task['prefix']) + '_moz.log'
        os.environ["MOZ_LOG_FILE"] = self.moz_log
        os.environ["MOZ_LOG"] = 'timestamp,sync,nsHttp:5,nsSocketTransport:5,nsStreamPump:5,'\
                                'nsHostResolver:5,pipnss:5'
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
        from marionette_driver.marionette import Marionette
        args = ['-profile', '"{0}"'.format(task['profile']),
                '-no-remote',
                '-width', str(task['width']),
                '-height', str(task['height']),
                '-marionette',
                'about:blank']
        if self.path.find(' ') > -1:
            command_line = '"{0}"'.format(self.path)
        else:
            command_line = self.path
        command_line += ' ' + ' '.join(args)
        DesktopBrowser.launch_browser(self, command_line)
        self.marionette = Marionette('localhost', port=2828)
        self.marionette.start_session(timeout=60)
        self.marionette.navigate(START_PAGE)

    def stop(self, job, task):
        if self.marionette is not None:
            self.marionette.close()
        DesktopBrowser.stop(self, job, task)
        os.environ["MOZ_LOG_FILE"] = ''
        os.environ["MOZ_LOG"] = ''

    def run_task(self, task):
        """Run an individual test"""
        if self.marionette is not None:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic.monotonic() + task['time_limit']
            task['current_step'] = 1
            recording = False
            while len(task['script']) and task['error'] is None and \
                    monotonic.monotonic() < end_time:
                self.prepare_task(task)
                command = task['script'].pop(0)
                if not recording and command['record']:
                    recording = True
                    self.on_start_recording(task)
                self.process_command(command)
                if command['record']:
                    self.wait_for_page_load()
                    if not task['combine_steps'] or not len(task['script']):
                        self.on_stop_recording(task)
                        recording = False
                        self.on_start_processing(task)
                        self.wait_for_processing(task)
                        if task['log_data']:
                            # Move on to the next step
                            task['current_step'] += 1
                            self.event_name = None
            # Always navigate to about:blank after finishing in case the tab is
            # remembered across sessions
            self.marionette.navigate('about:blank')
            self.task = None

    def wait_for_page_load(self):
        """Wait for the onload event from the extension"""
        pass

    def prepare_task(self, task):
        """Format the file prefixes for multi-step testing"""
        task['page_data'] = {}
        if task['current_step'] == 1:
            task['prefix'] = task['task_prefix']
            task['video_subdirectory'] = task['task_video_prefix']
        else:
            task['prefix'] = '{0}_{1:d}'.format(task['task_prefix'], task['current_step'])
            task['video_subdirectory'] = '{0}_{1:d}'.format(task['task_video_prefix'],
                                                            task['current_step'])
        if task['video_subdirectory'] not in task['video_directories']:
            task['video_directories'].append(task['video_subdirectory'])
        if self.event_name is not None:
            task['step_name'] = self.event_name
        else:
            task['step_name'] = 'Step_{0:d}'.format(task['current_step'])

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

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.marionette.navigate(command['target'])
        elif command['command'] == 'logdata':
            self.task['combine_steps'] = False
            if int(re.search(r'\d+', str(command['target'])).group()):
                logging.debug("Data logging enabled")
                self.task['log_data'] = True
            else:
                logging.debug("Data logging disabled")
                self.task['log_data'] = False
        elif command['command'] == 'combinesteps':
            self.task['log_data'] = True
            self.task['combine_steps'] = True
        elif command['command'] == 'seteventname':
            self.event_name = command['target']
        elif command['command'] == 'exec':
            self.marionette.execute_js_script(command['target'])
        elif command['command'] == 'sleep':
            delay = min(60, max(0, int(re.search(r'\d+', str(command['target'])).group())))
            if delay > 0:
                time.sleep(delay)
        elif command['command'] == 'setabm':
            self.task['stop_at_onload'] = \
                bool('target' in command and int(re.search(r'\d+',
                                                           str(command['target'])).group()) == 0)
        elif command['command'] == 'setactivitytimeout':
            if 'target' in command:
                self.task['activity_time'] = \
                    max(0, min(30, int(re.search(r'\d+', str(command['target'])).group())))
        elif command['command'] == 'setuseragent':
            self.task['user_agent_string'] = command['target']
        elif command['command'] == 'setcookie':
            if 'target' in command and 'value' in command:
                url = command['target'].strip()
                cookie = command['value']
                pos = cookie.find(';')
                if pos > 0:
                    cookie = cookie[:pos]
                pos = cookie.find('=')
                if pos > 0:
                    name = cookie[:pos].strip()
                    value = cookie[pos+1:].strip()
                    if len(name) and len(value) and len(url):
                        self.marionette.add_cookie({'url': url, 'name': name, 'value': value})

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.marionette is not None:
            self.marionette.navigate(url)
