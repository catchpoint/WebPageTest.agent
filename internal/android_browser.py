# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Base class support for android browsers"""
import gzip
import hashlib
import logging
import multiprocessing
import os
import shutil
import subprocess
import sys
import threading
import time
if (sys.version_info >= (3, 0)):
    from time import monotonic
    GZIP_TEXT = 'wt'
else:
    from monotonic import monotonic
    GZIP_TEXT = 'w'
try:
    import ujson as json
except BaseException:
    import json

from .base_browser import BaseBrowser


SET_ORANGE = "(function() {" \
             "var wptDiv = document.getElementById('wptorange');" \
             "if (!wptDiv) {" \
             "wptDiv = document.createElement('div');" \
             "wptDiv.id = 'wptorange';" \
             "wptDiv.style.position = 'absolute';" \
             "wptDiv.style.top = '0';" \
             "wptDiv.style.left = '0';" \
             "wptDiv.style.width = Math.max(document.documentElement.clientWidth, document.body.clientWidth || 0, window.clientWidth || 0) + 'px';" \
             "wptDiv.style.height = Math.max(document.documentElement.clientHeight, document.body.clientHeight || 0, window.innerHeight || 0) + 'px';" \
             "wptDiv.style.zIndex = '2147483647';" \
             "wptDiv.style.backgroundColor = '#DE640D';" \
             "document.body.appendChild(wptDiv);" \
             "}})();"


class AndroidBrowser(BaseBrowser):
    """Android Browser base"""
    def __init__(self, adb, options, job, config):
        BaseBrowser.__init__(self)
        self.adb = adb
        self.job = job
        self.options = options
        self.config = config
        self.recording = False
        self.usage_queue = None
        self.thread = None
        self.video_processing = None
        self.tcpdump_processing = None
        self.task = None
        self.video_enabled = bool(job['video'])
        self.tcpdump_enabled = bool('tcpdump' in job and job['tcpdump'])
        self.tcpdump_file = None
        if self.config['type'] == 'blackbox':
            self.tcpdump_enabled = True
            self.video_enabled = True

    def prepare(self, job, task):
        """Prepare the browser and OS"""
        self.task = task
        self.adb.cleanup_device()
        self.stop_all_browsers()
        proxies = {"http": None, "https": None}
        # Download and install the APK if necessary
        if 'apk_url' in self.config and 'md5' in self.config:
            if not os.path.isdir(job['persistent_dir']):
                os.makedirs(job['persistent_dir'])
            last_install_file = os.path.join(job['persistent_dir'],
                                             self.config['package'] + '.md5')
            last_md5 = None
            if os.path.isfile(last_install_file):
                with open(last_install_file, 'r') as f_in:
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
                    logging.debug('Downloading browser update: %s to %s',
                                  self.config['apk_url'], tmp_file)
                    import requests
                    request = requests.get(self.config['apk_url'], stream=True, proxies=proxies)
                    if request.status_code == 200:
                        with open(tmp_file, 'wb') as f_out:
                            for chunk in request.iter_content(chunk_size=4096):
                                f_out.write(chunk)
                                md5_hash.update(chunk)
                        md5 = md5_hash.hexdigest().lower()
                        if md5 == self.config['md5']:
                            valid = True
                except Exception:
                    logging.exception('Error downloading browser update')
                if os.path.isfile(tmp_file):
                    if valid:
                        # Uninstall the previous install of the same package if we are installing a custom browser.
                        if 'uninstall' in self.config and self.config['uninstall']:
                            logging.debug('Unistalling previous browser APK')
                            self.adb.adb(['uninstall', self.config['package']])
                        logging.debug('Installing browser APK')
                        self.adb.adb(['install', '-rg', tmp_file])
                        with open(last_install_file, 'w') as f_out:
                            f_out.write(md5)
                    else:
                        logging.error('Error downloading browser APK')
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass
        # kill any running instances
        self.adb.shell(['am', 'force-stop', self.config['package']])
        # Modify the hosts file for non-Chrome browsers
        if 'dns_override' in task:
            self.modify_hosts(task, task['dns_override'])
        self.profile_end('desktop.prepare')

    def modify_hosts(self, task, hosts):
        """Add entries to the system's hosts file"""
        hosts_tmp = os.path.join(task['dir'], "hosts.wpt")
        hosts_remote_tmp = '/data/local/tmp/hosts.wpt'
        hosts_file = '/etc/hosts'
        if len(hosts):
            logging.debug('Modifying hosts file:')
            # Make sure the system files are writable
            self.adb.su('mount -o rw,remount /system')
            try:
                hosts_text = "127.0.0.1       localhost\n::1             ip6-localhost"
                if hosts_text is not None:
                    hosts_text += "\n"
                    for pair in hosts:
                        hosts_text += "{0}    {1}\n".format(pair[1], pair[0])
                    with open(hosts_tmp, 'w') as f_out:
                        f_out.write(hosts_text)
                    self.adb.adb(['push', hosts_tmp, hosts_remote_tmp])
                    self.adb.su('cp {} {}'.format(hosts_remote_tmp, hosts_file))
                    self.adb.su('rm {}'.format(hosts_remote_tmp))
                    self.adb.su('chown root:root {}'.format(hosts_file))
                    self.adb.su('chmod 644 {}'.format(hosts_file))
                    os.unlink(hosts_tmp)
                    logging.debug(hosts_text)
            except Exception as err:
                logging.exception("Exception modifying hosts file: %s", err.__str__())

    def stop(self, job, task):
        """ Post-test cleanup """
        pass

    def stop_all_browsers(self):
        """Kill all instances of known browsers"""
        out = self.adb.shell(['ps'], silent=True)
        found_browsers = []
        all_browsers = self.config['all']
        for line in out.splitlines():
            for name in all_browsers:
                browser_info = all_browsers[name]
                if name not in found_browsers and 'package' in browser_info and \
                        line.find(browser_info['package']) >= 0:
                    found_browsers.append(name)
        if len(found_browsers):
            for name in found_browsers:
                package = all_browsers[name]['package']
                self.adb.shell(['am', 'force-stop', package])

    def execute_js(self, _script):
        """Run javascipt (stub for overriding"""
        return None

    def prepare_script_for_record(self, script, mark_start = False):
        """Convert a script command into one that first removes the orange frame"""
        mark = "fetch('http://127.0.0.1:8888/wpt-start-recording');" if mark_start else ''
        return "(function() {" \
               "var wptDiv = document.getElementById('wptorange');" \
               "if(wptDiv) {wptDiv.parentNode.removeChild(wptDiv);}" \
               "window.requestAnimationFrame(function(){" \
               "window.requestAnimationFrame(function(){" + mark + script + "});"\
               "});" \
               "})();"

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        if self.must_exit:
            return
        if task['log_data']:
            self.recording = True
            task['page_data']['osVersion'] = self.adb.version
            task['page_data']['os_version'] = self.adb.version
            version = self.adb.get_package_version(self.config['package'])
            if version is not None:
                task['page_data']['browserVersion'] = version
                task['page_data']['browser_version'] = version
            if not self.job['dtShaper']:
                if not self.job['shaper'].configure(self.job, task):
                    task['error'] = "Error configuring traffic-shaping"
                    task['page_data']['result'] = 12999
            if self.tcpdump_enabled:
                self.adb.start_tcpdump()
            if self.video_enabled and not self.job['disable_video']:
                if task['navigated']:
                    self.execute_js(SET_ORANGE)
                    time.sleep(0.5)
                logging.debug('Starting video capture...')
                self.adb.start_screenrecord()
            if self.tcpdump_enabled or self.video_enabled:
                time.sleep(2)

            # start the background thread for monitoring bandwidth if reverse-tethered
            if self.options.simplert or self.options.vpntether or self.options.vpntether2:
                self.usage_queue = multiprocessing.JoinableQueue()
                self.thread = threading.Thread(target=self.background_thread)
                self.thread.daemon = True
                self.thread.start()

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        pass

    def on_stop_recording(self, task):
        """Notification that we are done with an operation that needs to be recorded"""
        if self.thread is not None:
            self.thread.join(10)
            self.thread = None
        # record the CPU/Bandwidth/memory info
        self.recording = False
        if self.usage_queue is not None and not self.usage_queue.empty() and task is not None:
            file_path = os.path.join(task['dir'], task['prefix']) + '_progress.csv.gz'
            gzfile = gzip.open(file_path, GZIP_TEXT, 7)
            if gzfile:
                logline = "Offset Time (ms),Bandwidth In (bps),CPU Utilization (%),Memory\n"
                logging.debug(logline)
                gzfile.write(logline)
                try:
                    while True:
                        snapshot = self.usage_queue.get(5)
                        if snapshot is None:
                            break
                        logline = '{0:d},{1:d},-1,-1\n'.format(snapshot['time'], snapshot['bw'])
                        logging.debug(logline)
                        gzfile.write(logline)
                except:
                    logging.exception("Error processing usage queue")
                gzfile.close()
        if self.tcpdump_enabled:
            tcpdump = os.path.join(task['dir'], task['prefix']) + '.cap'
            self.adb.stop_tcpdump(tcpdump)
        if self.video_enabled and not self.job['disable_video']:
            task['video_file'] = os.path.join(task['dir'], task['prefix']) + '_video.mp4'
            self.adb.stop_screenrecord(task['video_file'])
        self.job['shaper'].reset()

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        if self.must_exit:
            return
        # kick off the video processing (async)
        if 'video_file' in task and task['video_file'] is not None and \
                os.path.isfile(task['video_file']):
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
            progress_file = os.path.join(task['dir'], task['prefix']) + \
                '_visual_progress.json.gz'
            visualmetrics = os.path.join(support_path, "visualmetrics.py")
            args = [sys.executable, visualmetrics, '-i', task['video_file'],
                    '-d', video_path, '--force', '--quality',
                    '{0:d}'.format(self.job['imageQuality']),
                    '--viewport', '--maxframes', '50', '--histogram', histograms,
                    '--progress', progress_file]
            if 'debug' in self.job and self.job['debug']:
                args.append('-vvvv')
            if 'renderVideo' in self.job and self.job['renderVideo']:
                video_out = os.path.join(task['dir'], task['prefix']) + '_rendered_video.mp4'
                args.extend(['--render', video_out])
            if 'fullSizeVideo' in self.job and self.job['fullSizeVideo']:
                args.append('--full')
            if 'thumbsize' in self.job:
                try:
                    thumbsize = int(self.job['thumbsize'])
                    if thumbsize > 0 and thumbsize <= 2000:
                        args.extend(['--thumbsize', str(thumbsize)])
                except Exception:
                    pass
            if 'videoFlags' in self.config:
                args.extend(self.config['videoFlags'])
            else:
                args.append('--orange')
            try:
                logging.debug('Video file size: %d', os.path.getsize(video_path))
            except Exception:
                pass
            logging.debug(' '.join(args))
            self.video_processing = subprocess.Popen(args, close_fds=True)
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
                    cmd = [sys.executable, pcap_parser, '--json', '-i', pcap_out, '-d', slices_file]
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
                logging.exception('Error processing tcpdump')

    def step_complete(self, task):
        """All of the processing for the current test step is complete"""
        # Write out the accumulated page_data
        if task['log_data'] and task['page_data']:
            if 'browser' in self.job:
                task['page_data']['browser_name'] = self.job['browser']
            if 'step_name' in task:
                task['page_data']['eventName'] = task['step_name']
            if 'run_start_time' in task:
                task['page_data']['test_run_time_ms'] = \
                    int(round((monotonic() - task['run_start_time']) * 1000.0))
            path = os.path.join(task['dir'], task['prefix'] + '_page_data.json.gz')
            json_page_data = json.dumps(task['page_data'])
            logging.debug('Page Data: %s', json_page_data)
            with gzip.open(path, GZIP_TEXT, 7) as outfile:
                outfile.write(json_page_data)

    def screenshot(self, task):
        """Grab a screenshot using adb"""
        if self.must_exit:
            return
        png_file = os.path.join(task['dir'], task['prefix'] + '_screen.png')
        self.adb.screenshot(png_file, self.job['image_magick']['mogrify'])
        task['page_data']['result'] = 0
        if self.config['type'] == 'blackbox':
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

    def wait_for_network_idle(self, timeout=60, threshold=1000):
        """Wait for 5 one-second intervals that receive less than 1KB"""
        logging.debug('Waiting for network idle')
        end_time = monotonic() + timeout
        self.adb.get_bytes_rx()
        idle_count = 0
        while idle_count < 5 and monotonic() < end_time and not self.must_exit:
            time.sleep(1)
            bytes_rx = self.adb.get_bytes_rx()
            logging.debug("Bytes received: %d", bytes_rx)
            if bytes_rx > threshold:
                idle_count = 0
            else:
                idle_count += 1

    def get_net_bytes(self):
        """Get the bytes "received" on the tun0 interface when reberse tethering"""
        import psutil
        bytes_in = 0
        net = psutil.net_io_counters(True)
        for interface in net:
            if interface[:3] == 'tun':
                bytes_in += net[interface].bytes_sent
        return bytes_in

    def background_thread(self):
        """Background thread for monitoring CPU and bandwidth usage"""
        import psutil
        last_time = start_time = monotonic()
        last_bytes = self.get_net_bytes()
        snapshot = {'time': 0, 'bw': 0}
        self.usage_queue.put(snapshot)
        while self.recording and not self.must_exit:
            snapshot = {'bw': 0}
            now = monotonic()
            snapshot['time'] = int((now - start_time) * 1000)
            # calculate the bandwidth over the last interval in Kbps
            bytes_in = self.get_net_bytes()
            if now > last_time:
                snapshot['bw'] = int((bytes_in - last_bytes) * 8.0 / (now - last_time))
            last_time = now
            last_bytes = bytes_in
            self.usage_queue.put(snapshot)
        self.usage_queue.put(None)
