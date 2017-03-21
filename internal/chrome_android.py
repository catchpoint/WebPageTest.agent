# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Chrome browser on Android"""
import logging
import os
import time
from .devtools_browser import DevtoolsBrowser

CHROME_COMMAND_LINE_OPTIONS = [
    '--disable-fre',
    '--disable-background-networking',
    '--no-first-run',
    '--disable-infobars',
    '--disable-translate',
    '--allow-running-insecure-content',
    '--disable-save-password-bubble',
    '--disable-background-downloads',
    '--disable-add-to-shelf',
    '--disable-client-side-phishing-detection',
    '--disable-datasaver-prompt',
    '--disable-default-apps',
    '--disable-domain-reliability',
    '--disable-background-timer-throttling',
    '--disable-external-intent-requests'
]

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

class ChromeAndroid(DevtoolsBrowser):
    """Chrome browser on Android"""
    def __init__(self, adb, config, options, job):
        self.adb = adb
        self.config = config
        self.options = options
        DevtoolsBrowser.__init__(self, job, use_devtools_video=False)
        self.connected = False

    def prepare(self, _, task):
        """Prepare the profile/OS for the browser"""
        self.task = task
        try:
            self.adb.adb(['forward', '--remove', 'tcp:{0}'.format(task['port'])])
            # kill any running instances
            self.adb.shell(['am', 'force-stop', self.config['package']])
            # clear the profile if necessary
            if task['cached']:
                self.adb.su('rm -r /data/data/' + self.config['package'] + '/app_tabs')
            else:
                self.clear_profile(task)
            # prepare the device
        except Exception as err:
            logging.critical("Exception preparing Browser: %s", err.__str__())

    def launch(self, job, task):
        """Launch the browser"""
        args = CHROME_COMMAND_LINE_OPTIONS
        if 'host_rules' in task:
            args.append('--host-rules=' + ','.join(task['host_rules']))
        if 'ignoreSSL' in job and job['ignoreSSL']:
            args.append('--ignore-certificate-errors')
        command_line = 'chrome ' + ' '.join(args)
        if 'addCmdLine' in job:
            command_line += ' ' + job['addCmdLine']
        local_command_line = os.path.join(task['dir'], 'chrome-command-line')
        remote_command_line = '/data/local/tmp/chrome-command-line'
        root_command_line = '/data/local/chrome-command-line'
        with open(local_command_line, 'wb') as f_out:
            f_out.write(command_line)
        if self.adb.adb(['push', local_command_line, remote_command_line]):
            # try copying it to /data/local for rooted devices that need it there
            if self.adb.su('cp {0} {1}'.format(remote_command_line, root_command_line)) is not None:
                self.adb.su('chmod 755 {0}'.format(root_command_line))
                self.adb.shell(['rm', remote_command_line])
            # launch the browser
            activity = '{0}/{1}'.format(self.config['package'], self.config['activity'])
            self.adb.shell(['am', 'start', '-n', activity, '-a',
                            'android.intent.action.VIEW', '-d', START_PAGE])
            # port-forward the devtools interface
            if self.adb.adb(['forward', 'tcp:{0}'.format(task['port']),
                             'localabstract:chrome_devtools_remote']):
                if DevtoolsBrowser.connect(self, task):
                    self.connected = True
                    DevtoolsBrowser.prepare_browser(self, task)
                    DevtoolsBrowser.navigate(self, START_PAGE)
                    time.sleep(0.5)

    def run_task(self, task):
        """Run an individual test"""
        if self.connected:
            DevtoolsBrowser.run_task(self, task)

    def stop(self):
        """Stop testing"""
        if self.connected:
            DevtoolsBrowser.disconnect(self)
        # kill the browser
        self.adb.shell(['am', 'force-stop', self.config['package']])

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        DevtoolsBrowser.on_start_recording(self, task)

    def on_stop_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        DevtoolsBrowser.on_stop_recording(self, task)

    def wait_for_processing(self):
        """Wait for any background processing threads to finish"""

    def clear_profile(self, task):
        """Clear the browser profile"""
        # Fail gracefully if root access isn't available
        out = self.adb.su('ls -1 /data/data/' + self.config['package'])
        if out is not None:
            remove = ''
            for entry in out.splitlines():
                entry = entry.strip()
                if len(entry) and entry != '.' and entry != '..' and \
                        entry != 'lib' and entry != 'shared_prefs':
                    remove += ' /data/data/' + self.config['package'] + '/' + entry
            if len(remove):
                self.adb.su('rm -r' + remove)
