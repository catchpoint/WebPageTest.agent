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

START_PAGE = 'data:text/html,'

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
        # launch the browser
        activity = '{0}/{1}'.format(self.config['package'], self.config['activity'])
        self.adb.shell(['am', 'start', '-n', activity, '-a',
                        'android.intent.action.VIEW', '-d', START_PAGE])
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
        self.on_start_recording(task)
        while len(task['script']) and monotonic.monotonic() < end_time:
            command = task['script'].pop(0)
            if command['command'] == 'navigate':
                activity = '{0}/{1}'.format(self.config['package'], self.config['activity'])
                self.adb.shell(['am', 'start', '-n', activity, '-a',
                                'android.intent.action.VIEW', '-d', command['target']])
            self.wait_for_page_load()
        self.on_stop_recording(task)
        self.wait_for_processing(task)

    def stop(self, job, task):
        """Stop testing"""
        # kill the browser
        self.adb.shell(['am', 'force-stop', self.config['package']])

    def on_stop_recording(self, task):
        """Collect post-test data"""
        AndroidBrowser.on_stop_recording(self, task)
        png_file = os.path.join(task['dir'], task['prefix'] + '_screen.png')
        self.adb.screenshot(png_file)
        task['page_data']['result'] = 0
        task['page_data']['visualTest'] = 1
        if os.path.isfile(png_file):
            if not self.job['pngss']:
                jpeg_file = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
                command = 'convert -quality {0:d} "{1}" "{2}"'.format(
                    self.job['iq'], png_file, jpeg_file)
                logging.debug(command)
                subprocess.call(command, shell=True)
                if os.path.isfile(jpeg_file):
                    try:
                        os.remove(png_file)
                    except Exception:
                        pass

    def clear_profile(self, _):
        """Clear the browser profile"""
        if 'clearProfile' in self.config and self.config['clearProfile']:
            self.adb.shell(['pm', 'clear', self.config['package']])
        elif 'directories' in self.config:
            remove = ' /data/data/{0}/'.format(self.config['package']).join(
                self.config['directories'])
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
        while not video_started and monotonic.monotonic() < end_startup:
            time.sleep(5)
            video_size = self.adb.get_video_size()
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
            delta = video_size - last_size
            logging.debug('Video Size: %d bytes (+ %d)', video_size, delta)
            last_size = video_size
            if delta > 10000:
                video_idle_count = 0
            else:
                video_idle_count += 1
