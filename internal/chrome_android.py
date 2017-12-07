# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Chrome browser on Android"""
import gzip
import logging
import os
import shutil
import time
from .devtools_browser import DevtoolsBrowser
from .android_browser import AndroidBrowser

CHROME_COMMAND_LINE_OPTIONS = [
    '--disable-fre',
    '--enable-benchmarking',
    '--metrics-recording-only',
    '--disable-geolocation',
    '--disable-background-networking',
    '--no-default-browser-check',
    '--no-first-run',
    '--process-per-tab',
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
    '--safebrowsing-disable-auto-update',
    '--disable-external-intent-requests',
    '--enable-remote-debugging',
    '--disable-browser-side-navigation'
]

HOST_RULES = [
    '"MAP cache.pack.google.com 127.0.0.1"',
    '"MAP clients1.google.com 127.0.0.1"'
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

class ChromeAndroid(AndroidBrowser, DevtoolsBrowser):
    """Chrome browser on Android"""
    def __init__(self, adb, config, options, job):
        self.adb = adb
        self.options = options
        self.config = dict(config)
        # default (overridable) configs
        self.config['command_line_file'] = 'chrome-command-line'
        self.config['adb_socket'] = 'localabstract:chrome_devtools_remote'
        # pull in the APK info for the browser
        if 'apk_info' in job and 'packages' in job['apk_info'] and \
                self.config['package'] in job['apk_info']['packages']:
            apk_info = job['apk_info']['packages'][self.config['package']]
            self.config['apk_url'] = apk_info['apk_url']
            self.config['md5'] = apk_info['md5'].lower()
        # pull in the settings for a custom browser into the config
        if 'customBrowser_package' in job:
            self.config['package'] = job['customBrowser_package']
        if 'customBrowser_activity' in job:
            self.config['activity'] = job['customBrowser_activity']
        if 'customBrowserUrl' in job:
            self.config['apk_url'] = job['customBrowserUrl']
        if 'customBrowserMD5' in job:
            self.config['md5'] = job['customBrowserMD5'].lower()
        if 'customBrowser_socket' in job:
            self.config['adb_socket'] = job['customBrowser_socket']
        if 'customBrowser_flagsFile' in job:
            self.config['command_line_file'] = os.path.basename(job['customBrowser_flagsFile'])
        AndroidBrowser.__init__(self, adb, options, job, self.config)
        DevtoolsBrowser.__init__(self, options, job, use_devtools_video=False)
        self.connected = False

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        self.task = task
        AndroidBrowser.prepare(self, job, task)
        try:
            self.adb.adb(['forward', '--remove', 'tcp:{0}'.format(task['port'])])
            # clear the profile if necessary
            if task['cached']:
                self.adb.su('rm -r /data/data/' + self.config['package'] + '/app_tabs')
            else:
                self.clear_profile(task)
        except Exception as err:
            logging.exception("Exception preparing Browser: %s", err.__str__())

    def launch(self, job, task):
        """Launch the browser"""
        args = list(CHROME_COMMAND_LINE_OPTIONS)
        host_rules = list(HOST_RULES)
        if 'host_rules' in task:
            host_rules.extend(task['host_rules'])
        args.append('--host-resolver-rules=' + ','.join(host_rules))
        if 'ignoreSSL' in job and job['ignoreSSL']:
            args.append('--ignore-certificate-errors')
        if 'netlog' in job and job['netlog']:
            self.adb.shell(['rm', '/data/local/tmp/netlog.txt'])
            args.append('--log-net-log=/data/local/tmp/netlog.txt')
        command_line = 'chrome ' + ' '.join(args)
        if 'addCmdLine' in job:
            command_line += ' ' + job['addCmdLine']
        command_line += ' about:blank'
        local_command_line = os.path.join(task['dir'], self.config['command_line_file'])
        remote_command_line = '/data/local/tmp/' + self.config['command_line_file']
        root_command_line = '/data/local/' + self.config['command_line_file']
        logging.debug(command_line)
        with open(local_command_line, 'wb') as f_out:
            f_out.write(command_line)
        if self.adb.adb(['push', local_command_line, remote_command_line]):
            os.remove(local_command_line)
            # try copying it to /data/local for rooted devices that need it there
            if self.adb.su('cp {0} {1}'.format(remote_command_line, root_command_line)) is not None:
                self.adb.su('chmod 666 {0}'.format(root_command_line))
            # launch the browser
            activity = '{0}/{1}'.format(self.config['package'], self.config['activity'])
            self.adb.shell(['am', 'start', '-n', activity, '-a',
                            'android.intent.action.VIEW', '-d', START_PAGE])
            # port-forward the devtools interface
            if self.adb.adb(['forward', 'tcp:{0}'.format(task['port']),
                             self.config['adb_socket']]):
                if DevtoolsBrowser.connect(self, task):
                    self.connected = True
                    DevtoolsBrowser.prepare_browser(self, task)
                    DevtoolsBrowser.navigate(self, START_PAGE)
                    time.sleep(0.5)

    def run_task(self, task):
        """Run an individual test"""
        if self.connected:
            DevtoolsBrowser.run_task(self, task)

    def stop(self, job, task):
        """Stop testing"""
        if self.connected:
            DevtoolsBrowser.disconnect(self)
        self.adb.adb(['forward', '--remove', 'tcp:{0}'.format(task['port'])])
        # kill the browser
        self.adb.shell(['am', 'force-stop', self.config['package']])
        self.adb.shell(['rm', '/data/local/tmp/' + self.config['command_line_file']])
        self.adb.su('rm /data/local/' + self.config['command_line_file'])
        # grab the netlog if there was one
        if 'netlog' in job and job['netlog']:
            netlog_file = os.path.join(task['dir'], task['prefix']) + '_netlog.txt'
            self.adb.adb(['pull', '/data/local/tmp/netlog.txt', netlog_file])
            self.adb.shell(['rm', '/data/local/tmp/netlog.txt'])
            if os.path.isfile(netlog_file):
                netlog_gzip = netlog_file + '.gz'
                with open(netlog_file, 'rb') as f_in:
                    with gzip.open(netlog_gzip, 'wb', 7) as f_out:
                        shutil.copyfileobj(f_in, f_out)
                if os.path.isfile(netlog_gzip):
                    os.remove(netlog_file)

    def execute_js(self, script):
        """Run javascipt"""
        return DevtoolsBrowser.execute_js(self, script)

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        AndroidBrowser.on_start_recording(self, task)
        DevtoolsBrowser.on_start_recording(self, task)

    def on_stop_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        AndroidBrowser.on_stop_recording(self, task)
        DevtoolsBrowser.on_stop_recording(self, task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        AndroidBrowser.on_start_processing(self, task)
        DevtoolsBrowser.on_start_processing(self, task)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        AndroidBrowser.wait_for_processing(self, task)

    def clear_profile(self, task):
        """Clear the browser profile"""
        local_command_line = os.path.join(task['dir'], self.config['command_line_file'])
        remote_command_line = '/data/local/tmp/' + self.config['command_line_file']
        root_command_line = '/data/local/' + self.config['command_line_file']
        if os.path.isfile(local_command_line):
            os.remove(local_command_line)
        self.adb.shell(['rm', remote_command_line])
        self.adb.su('rm "{0}"'.format(root_command_line))
        # Fail gracefully if root access isn't available
        if self.adb.short_version >= 7.0:
            out = self.adb.su('ls -1 /data/data/' + self.config['package'])
        else:
            out = self.adb.su('ls /data/data/' + self.config['package'])
        if out is not None:
            remove = ''
            for entry in out.splitlines():
                entry = entry.strip()
                if len(entry) and entry != '.' and entry != '..' and \
                        entry != 'lib' and entry != 'shared_prefs':
                    remove += ' /data/data/' + self.config['package'] + '/' + entry
            if len(remove):
                self.adb.su('rm -r' + remove)
