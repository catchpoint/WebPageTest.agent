# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Chrome browser on Android"""
import gzip
import logging
import os
import re
import shutil
import sys
import time
if (sys.version_info >= (3, 0)):
    from time import monotonic
else:
    from monotonic import monotonic
from .devtools_browser import DevtoolsBrowser
from .android_browser import AndroidBrowser

CHROME_COMMAND_LINE_OPTIONS = [
    '--allow-running-insecure-content',
    '--disable-background-networking',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-breakpad',
    '--disable-client-side-phishing-detection',
    '--disable-component-update',
    '--disable-default-apps',
    '--disable-domain-reliability',
    '--disable-external-intent-requests',
    '--disable-fetching-hints-at-navigation-start',
    '--disable-fre',
    '--disable-hang-monitor',
    '--disable-ipc-flooding-protection',
    '--disable-prompt-on-repost',
    '--disable-renderer-backgrounding',
    '--disable-site-isolation-trials',
    '--disable-sync',
    '--enable-remote-debugging',
    '--metrics-recording-only',
    '--metrics-recording-only',
    '--mute-audio',
    '--net-log-capture-mode=IncludeSensitive',
    '--no-default-browser-check',
    '--no-first-run',
    '--password-store=basic',
    '--use-mock-keychain',
]

HOST_RULES = [
    '"MAP cache.pack.google.com 127.0.0.1"',
    '"MAP clients1.google.com 127.0.0.1"',
    '"MAP offlinepages-pa.googleapis.com 127.0.0.1"',
    '"MAP optimizationguide-pa.googleapis.com 127.0.0.1"',
    '"MAP redirector.gvt1.com 127.0.0.1"',
    '"MAP update.googleapis.com 127.0.0.1"',
]

DISABLE_CHROME_FEATURES = [
    'AutofillServerCommunication',
    'CalculateNativeWinOcclusion',
    'HeavyAdPrivacyMitigations',
    'InterestFeedContentSuggestions',
    'MediaRouter',
    'OfflinePagesPrefetching',
    'OptimizationHints',
    'Translate',
]

ENABLE_CHROME_FEATURES = [
]

ENABLE_BLINK_FEATURES = [
]

""" Orange page
<html>
<head>
<style>
body {background-color: white; margin: 0;}
#wptorange {width:100%; height: 100%; background-color: #DE640D;}
</style>
</head>
<body><div id='wptorange'></div></body>
</html>
"""
START_PAGE = 'data:text/html,%3Chtml%3E%0D%0A%3Chead%3E%0D%0A%3Cstyle%3E%0D%0Abody%20%7B'\
             'background-color%3A%20white%3B%20margin%3A%200%3B%7D%0D%0A%23wptorange%20%7B'\
             'width%3A100%25%3B%20height%3A%20100%25%3B%20background-color'\
             '%3A%20%23DE640D%3B%7D%0D%0A%3C%2Fstyle%3E%0D%0A%3C%2Fhead%3E%0D%0A%3Cbody%3E%3C'\
             'div%20id%3D%27wptorange%27%3E%3C%2Fdiv%3E%3C%2Fbody%3E%0D%0A%3C%2Fhtml%3E'

class ChromeAndroid(AndroidBrowser, DevtoolsBrowser):
    """Chrome browser on Android"""
    def __init__(self, adb, config, options, job):
        self.adb = adb
        self.options = options
        self.config = dict(config)
        # default (overridable) configs
        self.config['command_line_file'] = 'chrome-command-line'
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
            self.config['uninstall'] = True
        if 'customBrowserMD5' in job:
            self.config['md5'] = job['customBrowserMD5'].lower()
        if 'customBrowser_flagsFile' in job:
            self.config['command_line_file'] = os.path.basename(job['customBrowser_flagsFile'])
        AndroidBrowser.__init__(self, adb, options, job, self.config)
        DevtoolsBrowser.__init__(self, options, job, use_devtools_video=False)
        self.devtools_screenshot = False
        self.connected = False

    def shutdown(self):
        """Shutdown the agent cleanly but mid-test"""
        DevtoolsBrowser.shutdown(self)
        AndroidBrowser.shutdown(self)

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
        features = list(ENABLE_CHROME_FEATURES)
        disable_features = list(DISABLE_CHROME_FEATURES)
        host_rules = list(HOST_RULES)
        if 'host_rules' in task:
            host_rules.extend(task['host_rules'])
        args.append('--host-resolver-rules=' + ','.join(host_rules))
        if 'ignoreSSL' in job and job['ignoreSSL']:
            args.append('--ignore-certificate-errors')
        if 'netlog' in job and job['netlog']:
            self.adb.shell(['rm', '/data/local/tmp/netlog.txt'])
            args.append('--log-net-log=/data/local/tmp/netlog.txt')
        if len(features):
            args.append('--enable-features=' + ','.join(features))
        if len(ENABLE_BLINK_FEATURES):
            args.append('--enable-blink-features=' + ','.join(ENABLE_BLINK_FEATURES))
        args.append('--disable-features=' + ','.join(disable_features))
        self.sanitize_shell_args(args)
        command_line = 'chrome ' + ' '.join(args)
        if 'addCmdLine' in job:
            command_line += ' ' + self.sanitize_shell_string(job['addCmdLine'])
        command_line += ' about:blank'
        local_command_line = os.path.join(task['dir'], self.config['command_line_file'])
        remote_command_line = '/data/local/tmp/' + self.config['command_line_file']
        root_command_line = '/data/local/' + self.config['command_line_file']
        logging.debug(command_line)
        with open(local_command_line, 'w') as f_out:
            f_out.write(command_line)
        if self.adb.adb(['push', local_command_line, remote_command_line]):
            os.remove(local_command_line)
            # Disable SELinux enforcement
            self.adb.su('setenforce 0')
            # try copying it to /data/local for rooted devices that need it there
            if self.adb.su('cp {0} {1}'.format(remote_command_line, root_command_line)) is not None:
                self.adb.su('chmod 666 {0}'.format(root_command_line))
            # configure any browser-specific prefs
            self.setup_prefs()
            self.configure_prefs()
            # launch the browser
            activity = '{0}/{1}'.format(self.config['package'], self.config['activity'])
            self.adb.shell(['am', 'start', '-n', activity, '-a',
                            'android.intent.action.VIEW', '-d', START_PAGE])
            # port-forward the devtools interface
            socket_name = self.get_devtools_socket()
            if socket_name is not None:
                if self.adb.adb(['forward', 'tcp:{0}'.format(task['port']),
                                 'localabstract:{}'.format(socket_name)]):
                    if DevtoolsBrowser.connect(self, task):
                        self.connected = True
                        DevtoolsBrowser.prepare_browser(self, task)
                        DevtoolsBrowser.navigate(self, START_PAGE)
                        time.sleep(0.5)
                        self.wait_for_network_idle(120, 4000)

    def setup_prefs(self):
        """Install our base set of preferences"""
        # Crashes chrome on the Moto G4's so disabled for now
        """
        src = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                           'support', 'chrome', 'prefs.json')
        remote_prefs = '/data/local/tmp/Preferences'
        self.adb.shell(['rm', remote_prefs])
        app_dir = '/data/data/{0}'.format(self.config['package'])
        self.adb.su('mkdir {0}/app_chrome'.format(app_dir))
        self.adb.su('chmod 777 {0}/app_chrome'.format(app_dir))
        self.adb.su('mkdir {0}/app_chrome/Default'.format(app_dir))
        self.adb.su('chmod 777 {0}/app_chrome/Default'.format(app_dir))
        dest = '{0}/app_chrome/Default/Preferences'.format(app_dir)
        self.adb.adb(['push', src, remote_prefs])
        self.adb.su('cp {0} {1}'.format(remote_prefs, dest))
        self.adb.shell(['rm', remote_prefs])
        self.adb.su('chmod 777 {0}'.format(dest))
        """
        return

    def configure_prefs(self):
        """Configure browser-specific shared_prefs"""
        if self.config['package'] == 'com.sec.android.app.sbrowser':
            prefs = {
                'enable_quick_menu': '<boolean name="enable_quick_menu" value="false" />'
            }
            self.write_prefs(prefs, 'com.sec.android.app.sbrowser_preferences.xml')
        elif self.config['package'] == 'com.sec.android.app.sbrowser.beta':
            prefs = {
                'enable_quick_menu': '<boolean name="enable_quick_menu" value="false" />'
            }
            self.write_prefs(prefs, 'com.sec.android.app.sbrowser.beta_preferences.xml')

    def write_prefs(self, prefs, file_base):
        """update the prefs xml file"""
        prefs_file = '/data/data/{0}/shared_prefs/{1}'.format(self.config['package'], file_base)
        current = None
        current = self.adb.su('cat "{0}"'.format(prefs_file))
        modified = False
        if current is not None:
            out = ''
            for line in current.splitlines():
                line = line.rstrip()
                # See if it is a pref we need to modify
                for name in prefs:
                    if line.find('name="{0}"'.format(name)) >= 0:
                        value = prefs[name]
                        if value is not None:
                            if line.find(value) < 0:
                                logging.debug('Setting pref : %s', value)
                                line = '    {0}'.format(value)
                                prefs.pop(name, None)
                                modified = True
                                break
                if line.startswith('</map>'):
                    # Add any missing prefs
                    for name in prefs:
                        value = prefs[name]
                        if value is not None:
                            logging.debug('Adding pref : %s', value)
                            out += '    {0}\n'.format(value)
                            modified = True
                out += line + '\n'
        if modified:
            local = os.path.join(self.task['dir'], 'pref.xml')
            remote = '/data/local/tmp/pref.xml'
            with open(local, 'w') as f_out:
                f_out.write(out)
            if os.path.isfile(local):
                self.adb.shell(['rm', remote])
                if self.adb.adb(['push', local, remote]):
                    if self.adb.su('cp {0} {1}'.format(remote, prefs_file)) is not None:
                        self.adb.su('chmod 666 {0}'.format(prefs_file))
                    self.adb.shell(['rm', remote])
                os.remove(local)

    def get_devtools_socket(self):
        """Get the socket name of the remote devtools socket. @..._devtools_remote"""
        socket_name = None
        end_time = monotonic() + 120
        time.sleep(1)
        while socket_name is None and monotonic() < end_time:
            out = self.adb.shell(['cat', '/proc/net/unix'])
            if out is not None:
                for line in out.splitlines():
                    match = re.search(r'00010000 0001.* @([^\s]+_devtools_remote)', line)
                    if match:
                        socket_name = match.group(1)
                        logging.debug('Remote devtools socket: {0}'.format(socket_name))
            if socket_name is None:
                time.sleep(1)
        if socket_name is None:
            logging.debug('Failed to find remote devtools socket')
        return socket_name

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
        AndroidBrowser.stop(self, job, task)
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

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        AndroidBrowser.on_stop_capture(self, task)
        DevtoolsBrowser.on_stop_capture(self, task)

    def on_stop_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        AndroidBrowser.on_stop_recording(self, task)
        AndroidBrowser.screenshot(self, task)
        DevtoolsBrowser.on_stop_recording(self, task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        AndroidBrowser.on_start_processing(self, task)
        DevtoolsBrowser.on_start_processing(self, task)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DevtoolsBrowser.wait_for_processing(self, task)
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
