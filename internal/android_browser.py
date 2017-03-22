# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for android browsers"""
import gzip
import hashlib
import logging
import os
import shutil
import subprocess
import time

class AndroidBrowser(object):
    """Android Browser base"""
    def __init__(self, adb, job, options, config):
        self.adb = adb
        self.job = job
        self.options = options
        self.config = config
        self.video_processing = None
        self.tcpdump_enabled = bool('tcpdump' in job and job['tcpdump'])

    def prepare(self, job, task):
        """Prepare the browser and OS"""
        self.adb.cleanup_device()
        # Download and install the APK if necessary
        if 'apk_info' in job and 'packages' in job['apk_info'] and \
                self.config['package'] in job['apk_info']['packages']:
            apk_info = job['apk_info']['packages'][self.config['package']]
            if 'apk_url' in apk_info and 'md5' in apk_info:
                local_file = os.path.join(job['persistent_dir'],
                                          self.config['package'] + '.' + apk_info['md5'] + '.apk')
                if not os.path.isfile(local_file):
                    valid = False
                    md5_hash = hashlib.md5()
                    try:
                        logging.debug('Downloading browser update: %s',
                                      apk_info['apk_url'])
                        import requests
                        if not os.path.isdir(job['persistent_dir']):
                            os.makedirs(job['persistent_dir'])
                        request = requests.get(apk_info['apk_url'], stream=True)
                        if request.status_code == 200:
                            with open(local_file, 'wb') as f_out:
                                for chunk in request.iter_content(chunk_size=4096):
                                    f_out.write(chunk)
                                    md5_hash.update(chunk)
                            md5 = md5_hash.hexdigest().lower()
                            if md5 == apk_info['md5'].lower():
                                valid = True
                    except Exception:
                        pass
                    if os.path.isfile(local_file):
                        if valid:
                            logging.debug('Installing browser APK')
                            self.adb.adb(['install', '-rg', local_file])
                        else:
                            logging.error('Error downloading browser APK')
                            os.remove(local_file)
        # kill any running instances
        self.adb.shell(['am', 'force-stop', self.config['package']])

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        if task['log_data']:
            task['page_data']['osVersion'] = self.adb.version
            out = self.adb.shell(['dumpsys', 'package', self.config['package'], '|', 'grep',
                                  'versionName', '|', 'head', '-n1'])
            if out is not None:
                separator = out.find('=')
                if separator > -1:
                    task['page_data']['browserVersion'] = out[separator + 1:].strip()
            if self.tcpdump_enabled:
                self.adb.start_tcpdump()
            if self.job['video']:
                self.adb.start_screenrecord()
            if self.tcpdump_enabled or self.job['video']:
                time.sleep(0.5)

    def on_stop_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        if self.tcpdump_enabled:
            logging.debug("Stopping tcpdump")
            tcpdump = os.path.join(task['dir'], task['prefix']) + '.cap'
            self.adb.stop_tcpdump(tcpdump)
            if os.path.isfile(tcpdump):
                pcap_out = tcpdump + '.gz'
                with open(tcpdump, 'rb') as f_in:
                    with gzip.open(pcap_out, 'wb', 7) as f_out:
                        shutil.copyfileobj(f_in, f_out)
                if os.path.isfile(pcap_out):
                    os.remove(tcpdump)

        if self.job['video']:
            logging.debug("Stopping video capture")
            task['video_file'] = os.path.join(task['dir'], task['prefix']) + '_video.mp4'
            self.adb.stop_screenrecord(task['video_file'])
            # kick off the video processing (async)
            if os.path.isfile(task['video_file']):
                video_path = os.path.join(task['dir'], task['video_subdirectory'])
                support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
                if task['current_step'] == 1:
                    filename = '{0:d}.{1:d}.histograms.json.gz'.format(task['run'],
                                                                       task['cached'])
                else:
                    filename = '{0:d}.{1:d}.{2:d}.histograms.json.gz'.format(task['run'],
                                                                             task['cached'],
                                                                             task['current_step'])
                histograms = os.path.join(task['dir'], filename)
                visualmetrics = os.path.join(support_path, "visualmetrics.py")
                self.video_processing = subprocess.Popen(['python', visualmetrics, '-vvvv',
                                                          '-i', task['video_file'],
                                                          '-d', video_path,
                                                          '--force', '--quality',
                                                          '{0:d}'.format(self.job['iq']),
                                                          '--viewport', '--orange',
                                                          '--maxframes', '50',
                                                          '--histogram', histograms])

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        if self.video_processing is not None:
            self.video_processing.communicate()
            self.video_processing = None
            if 'keepvideo' not in self.job or not self.job['keepvideo']:
                try:
                    os.remove(task['video_file'])
                except Exception:
                    pass
