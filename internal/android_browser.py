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
import ujson as json

class AndroidBrowser(object):
    """Android Browser base"""
    def __init__(self, adb, options, job, config):
        self.adb = adb
        self.job = job
        self.options = options
        self.config = config
        self.video_processing = None
        self.tcpdump_processing = None
        self.task = None
        self.video_enabled = bool(job['video'])
        self.tcpdump_enabled = bool('tcpdump' in job and job['tcpdump'])
        self.tcpdump_file = None
        self.cpu_start = None
        if self.config['type'] == 'blackbox':
            self.tcpdump_enabled = True
            self.video_enabled = True

    def prepare(self, job, task):
        """Prepare the browser and OS"""
        self.task = task
        self.adb.cleanup_device()
        # Download and install the APK if necessary
        if 'apk_url' in self.config and 'md5' in self.config:
            if not os.path.isdir(job['persistent_dir']):
                os.makedirs(job['persistent_dir'])
            last_install_file = os.path.join(job['persistent_dir'],
                                             self.config['package'] + '.md5')
            last_md5 = None
            if os.path.isfile(last_install_file):
                with open(last_install_file, 'rb') as f_in:
                    last_md5 = f_in.read()
            if last_md5 is None or last_md5 != self.config['md5']:
                valid = False
                tmp_file = os.path.join(job['persistent_dir'],
                                        self.config['package'] + '.apk')
                if os.path.isfile(tmp_file):
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass
                md5_hash = hashlib.md5()
                try:
                    logging.debug('Downloading browser update: %s',
                                  self.config['apk_url'])
                    import requests
                    request = requests.get(self.config['apk_url'], stream=True)
                    if request.status_code == 200:
                        with open(tmp_file, 'wb') as f_out:
                            for chunk in request.iter_content(chunk_size=4096):
                                f_out.write(chunk)
                                md5_hash.update(chunk)
                        md5 = md5_hash.hexdigest().lower()
                        if md5 == self.config['md5']:
                            valid = True
                except Exception:
                    pass
                if os.path.isfile(tmp_file):
                    if valid:
                        logging.debug('Installing browser APK')
                        self.adb.adb(['install', '-rg', tmp_file])
                        with open(last_install_file, 'wb') as f_out:
                            f_out.write(md5)
                    else:
                        logging.error('Error downloading browser APK')
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass
        # kill any running instances
        self.adb.shell(['am', 'force-stop', self.config['package']])

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        if task['log_data']:
            self.cpu_start = self.adb.cpu_times()
            task['page_data']['osVersion'] = self.adb.version
            out = self.adb.shell(['dumpsys', 'package', self.config['package'], '|', 'grep',
                                  'versionName', '|', 'head', '-n1'])
            if out is not None:
                separator = out.find('=')
                if separator > -1:
                    task['page_data']['browserVersion'] = out[separator + 1:].strip()
            if self.tcpdump_enabled:
                self.adb.start_tcpdump()
            if self.video_enabled:
                self.adb.start_screenrecord()
            if self.tcpdump_enabled or self.video_enabled:
                time.sleep(0.5)

    def on_stop_recording(self, task):
        """Notification that we are done with an operation that needs to be recorded"""
        if self.cpu_start is not None:
            cpu_end = self.adb.cpu_times()
            # only the busy times look accurate from Android, idle has a habit of
            # rolling backwards so percent can't be calculated
            cpu_busy = (cpu_end.user - self.cpu_start.user) + \
                    (cpu_end.system - self.cpu_start.system)
            task['page_data']['fullyLoadedCPUms'] = int(cpu_busy * 1000.0)
            self.cpu_start = None
        if self.tcpdump_enabled:
            tcpdump = os.path.join(task['dir'], task['prefix']) + '.cap'
            self.adb.stop_tcpdump(tcpdump)
        if self.video_enabled:
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
                args = ['python', visualmetrics, '-vvvv', '-i', task['video_file'],
                        '-d', video_path, '--force', '--quality', '{0:d}'.format(self.job['iq']),
                        '--viewport', '--maxframes', '50', '--histogram', histograms]
                if 'renderVideo' in self.job and self.job['renderVideo']:
                    video_out = os.path.join(task['dir'], task['prefix']) + '_rendered_video.mp4'
                    args.extend(['--render', video_out])
                if 'videoFlags' in self.config:
                    args.extend(self.config['videoFlags'])
                else:
                    args.append('--orange')
                logging.debug(' '.join(args))
                self.video_processing = subprocess.Popen(args)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        if self.tcpdump_enabled:
            tcpdump = os.path.join(task['dir'], task['prefix']) + '.cap'
            if os.path.isfile(tcpdump):
                pcap_out = tcpdump + '.gz'
                with open(tcpdump, 'rb') as f_in:
                    with gzip.open(pcap_out, 'wb', 7) as f_out:
                        shutil.copyfileobj(f_in, f_out)
                if os.path.isfile(pcap_out):
                    os.remove(tcpdump)
                    self.tcpdump_file = pcap_out
                    path_base = os.path.join(task['dir'], task['prefix'])
                    slices_file = path_base + '_pcap_slices.json.gz'
                    pcap_parser = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                               'support', "pcap-parser.py")
                    cmd = ['python', pcap_parser, '--json', '-i', pcap_out, '-d', slices_file]
                    logging.debug(' '.join(cmd))
                    self.tcpdump_processing = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                                               stderr=subprocess.PIPE)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        if self.video_processing is not None:
            self.video_processing.communicate()
            self.video_processing = None
            if not self.job['keepvideo']:
                try:
                    os.remove(task['video_file'])
                except Exception:
                    pass
        if self.tcpdump_processing is not None:
            try:
                stdout, _ = self.tcpdump_processing.communicate()
                if stdout is not None:
                    result = json.loads(stdout)
                    if result:
                        if 'in' in result:
                            task['page_data']['pcapBytesIn'] = result['in']
                        if 'out' in result:
                            task['page_data']['pcapBytesOut'] = result['out']
                        if 'in_dup' in result:
                            task['page_data']['pcapBytesInDup'] = result['in_dup']
                if 'tcpdump' not in self.job or not self.job['tcpdump']:
                    if self.tcpdump_file is not None:
                        os.remove(self.tcpdump_file)
            except Exception:
                pass
