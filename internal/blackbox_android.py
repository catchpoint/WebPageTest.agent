# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Chrome browser on Android"""
import logging
import os
import subprocess
import time
import monotonic
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

    def launch(self, job, task):
        """Launch the browser"""
        # copy the Chrome command-line just in case it is needed
        args = list(CHROME_COMMAND_LINE_OPTIONS)
        host_rules = list(HOST_RULES)
        if 'host_rules' in task:
            host_rules.extend(task['host_rules'])
        args.append('--host-resolver-rules=' + ','.join(host_rules))
        if 'ignoreSSL' in job and job['ignoreSSL']:
            args.append('--ignore-certificate-errors')
        command_line = 'chrome ' + ' '.join(args)
        if 'addCmdLine' in job:
            command_line += ' ' + job['addCmdLine']
        local_command_line = os.path.join(task['dir'], 'chrome-command-line')
        remote_command_line = '/data/local/tmp/chrome-command-line'
        root_command_line = '/data/local/chrome-command-line'
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
        logging.debug("Running test")
        end_time = monotonic.monotonic() + task['time_limit']
        task['log_data'] = True
        task['current_step'] = 1
        task['prefix'] = task['task_prefix']
        task['video_subdirectory'] = task['task_video_prefix']
        if self.job['video']:
            task['video_directories'].append(task['video_subdirectory'])
        task['step_name'] = 'Navigate'
        task['run_start_time'] = monotonic.monotonic()
        self.on_start_recording(task)
        while len(task['script']) and monotonic.monotonic() < end_time:
            command = task['script'].pop(0)
            if command['command'] == 'navigate':
                activity = '{0}/{1}'.format(self.config['package'], self.config['activity'])
                cmd = 'am start -n {0} -a android.intent.action.VIEW -d "{1}"'.format(activity, \
                        command['target'].replace('"', '%22'))
                local_intent = os.path.join(task['dir'], 'wpt_intent.sh')
                remote_intent = '/data/local/tmp/wpt_intent.sh'
                self.adb.shell(['rm', remote_intent])
                with open(local_intent, 'wb') as f_out:
                    f_out.write(cmd)
                if self.adb.adb(['push', local_intent, remote_intent]):
                    os.remove(local_intent)
                    self.adb.shell(['chmod', '777', remote_intent])
                    self.adb.shell([remote_intent])
                    self.adb.shell(['rm', remote_intent])
            self.wait_for_page_load()
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

    def on_stop_recording(self, task):
        """Collect post-test data"""
        AndroidBrowser.on_stop_recording(self, task)
        png_file = os.path.join(task['dir'], task['prefix'] + '_screen.png')
        self.adb.screenshot(png_file)
        task['page_data']['result'] = 0
        task['page_data']['visualTest'] = 1
        if os.path.isfile(png_file):
            if not self.job['pngScreenShot']:
                jpeg_file = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
                command = '{0} "{1}" -resize {2:d}x{2:d} -quality {3:d} "{4}"'.format(
                    self.job['image_magick']['convert'],
                    png_file, 600, self.job['imageQuality'], jpeg_file)
                logging.debug(command)
                subprocess.call(command, shell=True)
                if os.path.isfile(jpeg_file):
                    try:
                        os.remove(png_file)
                    except Exception:
                        pass

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

    def wait_for_network_idle(self):
        """Wait for 5 one-second intervals that receive less than 1KB"""
        logging.debug('Waiting for network idle')
        end_time = monotonic.monotonic() + 60
        self.adb.get_bytes_rx()
        idle_count = 0
        while idle_count < 5 and monotonic.monotonic() < end_time:
            time.sleep(1)
            bytes_rx = self.adb.get_bytes_rx()
            logging.debug("Bytes received: %d", bytes_rx)
            if bytes_rx > 1000:
                idle_count = 0
            else:
                idle_count += 1

    def wait_for_page_load(self):
        """Once the video starts growing, wait for it to stop"""
        logging.debug('Waiting for the page to load')
        # Wait for the video to start (up to 30 seconds)
        end_startup = monotonic.monotonic() + 30
        end_time = monotonic.monotonic() + self.task['time_limit']
        last_size = self.adb.get_video_size()
        video_started = False
        bytes_rx = self.adb.get_bytes_rx()
        while not video_started and monotonic.monotonic() < end_startup:
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
        while video_idle_count <= 3 and monotonic.monotonic() < end_time:
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
