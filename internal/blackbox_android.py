# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Chrome browser on Android"""
import logging
import os
import re
import sys
import time
if (sys.version_info >= (3, 0)):
    from time import monotonic
else:
    from monotonic import monotonic
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
    '--disable-sync',
    '--disable-external-intent-requests'
]

HOST_RULES = [
    '"MAP cache.pack.google.com 127.0.0.1"',
    '"MAP clients1.google.com 127.0.0.1"'
]

START_PAGE = 'http://www.webpagetest.org/blank.html'


class BlackBoxAndroid(AndroidBrowser):
    """Chrome browser on Android"""
    def __init__(self, adb, config, options, job):
        self.adb = adb
        self.task = None
        self.options = options
        self.config = dict(config)
        # pull in the APK info for the browser
        if 'apk_info' in job and 'packages' in job['apk_info'] and \
                self.config['package'] in job['apk_info']['packages']:
            apk_info = job['apk_info']['packages'][self.config['package']]
            self.config['apk_url'] = apk_info['apk_url']
            self.config['md5'] = apk_info['md5'].lower()
        AndroidBrowser.__init__(self, adb, options, job, self.config)

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        self.task = task
        AndroidBrowser.prepare(self, job, task)
        if not task['cached']:
            self.clear_profile(task)
        if 'settings' in self.config and self.config['settings'] == "Opera Mini":
            self.prepare_opera_mini_settings()

    def launch(self, job, task):
        """Launch the browser"""
        # copy the Chrome command-line just in case it is needed
        if self.must_exit:
            return
        args = list(CHROME_COMMAND_LINE_OPTIONS)
        host_rules = list(HOST_RULES)
        if 'host_rules' in task:
            host_rules.extend(task['host_rules'])
        args.append('--host-resolver-rules=' + ','.join(host_rules))
        if 'ignoreSSL' in job and job['ignoreSSL']:
            args.append('--ignore-certificate-errors')
        self.sanitize_shell_args(args)
        command_line = 'chrome ' + ' '.join(args)
        if 'addCmdLine' in job:
            command_line += ' ' + self.sanitize_shell_string(job['addCmdLine'])
        local_command_line = os.path.join(task['dir'], 'chrome-command-line')
        remote_command_line = '/data/local/tmp/chrome-command-line'
        root_command_line = '/data/local/chrome-command-line'
        logging.debug(command_line)
        with open(local_command_line, 'w') as f_out:
            f_out.write(command_line)
        if self.adb.adb(['push', local_command_line, remote_command_line]):
            os.remove(local_command_line)
            # try copying it to /data/local for rooted devices that need it there
            if self.adb.su('cp {0} {1}'.format(remote_command_line, root_command_line)) is not None:
                self.adb.su('chmod 666 {0}'.format(root_command_line))
            # launch the browser
            activity = '{0}/{1}'.format(self.config['package'], self.config['activity'])
            start_page = START_PAGE
            if 'startPage' in self.config:
                start_page = self.config['startPage']
            self.adb.shell(['am', 'start', '-n', activity, '-a',
                            'android.intent.action.VIEW', '-d', start_page])
            if 'startupDelay' in self.config:
                time.sleep(self.config['startupDelay'])
            self.wait_for_network_idle()

    def run_task(self, task):
        """Skip anything that isn't a navigate command"""
        if self.must_exit:
            return
        logging.debug("Running test")
        end_time = monotonic() + task['test_time_limit']
        task['log_data'] = True
        task['current_step'] = 1
        task['prefix'] = task['task_prefix']
        task['video_subdirectory'] = task['task_video_prefix']
        if self.job['video']:
            task['video_directories'].append(task['video_subdirectory'])
        task['step_name'] = 'Navigate'
        task['run_start_time'] = monotonic()
        self.on_start_recording(task)
        while len(task['script']) and monotonic() < end_time and not self.must_exit:
            command = task['script'].pop(0)
            if command['command'] == 'navigate':
                task['page_data']['URL'] = command['target']
                activity = '{0}/{1}'.format(self.config['package'], self.config['activity'])
                cmd = 'am start -n {0} -a android.intent.action.VIEW \
                       -d "{1}"'.format(activity,
                                        command['target'].replace('"', '%22'))
                local_intent = os.path.join(task['dir'], 'wpt_intent.sh')
                remote_intent = '/data/local/tmp/wpt_intent.sh'
                self.adb.shell(['rm', remote_intent])
                with open(local_intent, 'w') as f_out:
                    f_out.write(cmd)
                if self.adb.adb(['push', local_intent, remote_intent]):
                    os.remove(local_intent)
                    self.adb.shell(['chmod', '777', remote_intent])
                    self.adb.shell([remote_intent])
                    self.adb.shell(['rm', remote_intent])
            self.wait_for_page_load()
        self.on_stop_capture(task)
        self.on_stop_recording(task)
        self.on_start_processing(task)
        self.wait_for_processing(task)
        self.step_complete(task)

    def run_lighthouse_test(self, task):
        """Stub for lighthouse test"""
        pass

    def stop(self, job, task):
        """Stop testing"""
        # kill the browser
        self.adb.shell(['am', 'force-stop', self.config['package']])
        self.adb.shell(['rm', '/data/local/tmp/chrome-command-line'])
        self.adb.su('rm /data/local/chrome-command-line')
        AndroidBrowser.stop(self, job, task)

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        AndroidBrowser.on_stop_capture(self, task)

    def on_stop_recording(self, task):
        """Collect post-test data"""
        AndroidBrowser.on_stop_recording(self, task)
        AndroidBrowser.screenshot(self, task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        AndroidBrowser.on_start_processing(self, task)

    def clear_profile(self, _):
        """Clear the browser profile"""
        if 'clearProfile' in self.config and self.config['clearProfile']:
            self.adb.shell(['pm', 'clear', self.config['package']])
        elif 'directories' in self.config:
            remove = ' '
            for directory in self.config['directories']:
                remove += ' "/data/data/{0}/{1}"'.format(self.config['package'], directory)
            if len(remove):
                self.adb.su('rm -r' + remove)

    def ensure_xml_setting(self, settings, key, value):
        """Make sure the provided setting exists in the setting string"""
        if settings.find('name="{0}" value="{1}"'.format(key, value)) == -1:
            self.modified = True
            settings = re.sub(r'name=\"{0}\" value=\"[^\"]\"'.format(key),
                              'name="{0}" value="{1}"'.format(key, value), settings)
            if settings.find('name="{0}" value="{1}"'.format(key, value)) == -1:
                settings = settings.replace('\n</map>',
                                            '\n    <int name="{0}" value="{1}" />\n</map>'.format(key, value))
        return settings

    def prepare_opera_mini_settings(self):
        """Configure the data saver settings"""
        compression = "1"
        if "mode" in self.config:
            if self.config["mode"].find("high") >= 0:
                compression = "0"
        settings_file = "/data/data/{0}/shared_prefs/user_settings.xml".format(self.config['package'])
        settings = self.adb.su('cat ' + settings_file).replace("\r", "")
        original_settings = str(settings)
        # make sure ad blocking and compression are at least enabled
        settings = self.ensure_xml_setting(settings, "obml_ad_blocking", "1")
        settings = self.ensure_xml_setting(settings, "compression_enabled", "1")
        settings = self.ensure_xml_setting(settings, "compression", compression)
        if settings != original_settings:
            local_settings = os.path.join(self.task['dir'], 'user_settings.xml')
            remote_temp = '/data/local/tmp/user_settings.xml'
            with open(local_settings, 'w') as f_out:
                f_out.write(settings)
            if self.adb.adb(['push', local_settings, remote_temp]):
                self.adb.su('chmod 666 /data/local/tmp/user_settings.xml')
                self.adb.su('cp /data/local/tmp/user_settings.xml ' + settings_file)
            os.remove(local_settings)

    def wait_for_page_load(self):
        """Once the video starts growing, wait for it to stop"""
        logging.debug('Waiting for the page to load')
        # Wait for the video to start (up to 30 seconds)
        end_startup = monotonic() + 30
        end_time = monotonic() + self.task['time_limit']
        last_size = self.adb.get_video_size()
        video_started = False
        bytes_rx = self.adb.get_bytes_rx()
        while not video_started and monotonic() < end_startup and not self.must_exit:
            time.sleep(5)
            video_size = self.adb.get_video_size()
            bytes_rx = self.adb.get_bytes_rx()
            delta = video_size - last_size
            logging.debug('Video Size: %d bytes (+ %d)', video_size, delta)
            last_size = video_size
            if delta > 50000:
                video_started = True
        # Wait for the activity to stop
        video_idle_count = 0
        while video_idle_count <= 3 and monotonic() < end_time and not self.must_exit:
            time.sleep(5)
            video_size = self.adb.get_video_size()
            bytes_rx = self.adb.get_bytes_rx()
            delta = video_size - last_size
            logging.debug('Video Size: %d bytes (+ %d) - %d bytes received',
                          video_size, delta, bytes_rx)
            last_size = video_size
            if delta > 10000 or bytes_rx > 5000:
                video_idle_count = 0
            else:
                video_idle_count += 1
