# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for desktop browsers"""
import gzip
import logging
import os
import platform
import Queue
import shutil
import subprocess
import threading
import time
import monotonic

class DesktopBrowser(object):
    """Desktop Browser base"""
    START_BROWSER_TIME_LIMIT = 30

    def __init__(self, path, job, options):
        self.path = path
        self.proc = None
        self.job = job
        self.recording = False
        self.usage_queue = None
        self.thread = None
        self.options = options
        self.interfaces = None
        self.tcpdump_enabled = bool('tcpdump' in job and job['tcpdump'])
        self.tcpdump = None
        self.pcap_file = None
        self.pcap_thread = None
        self.task = None
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")

    def prepare(self, _, task):
        """Prepare the profile/OS for the browser"""
        self.task = task
        self.find_default_interface()
        if self.tcpdump_enabled:
            os.environ["SSLKEYLOGFILE"] = os.path.join(task['dir'], task['prefix']) + '_keylog.log'
        else:
            os.environ["SSLKEYLOGFILE"] = ''
        try:
            from .os_util import kill_all
            from .os_util import flush_dns
            logging.debug("Preparing browser")
            kill_all(os.path.basename(self.path), True)
            if self.options.shaper is None or self.options.shaper != 'none':
                flush_dns()
            if 'profile' in task:
                if not task['cached'] and os.path.isdir(task['profile']):
                    logging.debug("Clearing profile %s", task['profile'])
                    shutil.rmtree(task['profile'])
                if not os.path.isdir(task['profile']):
                    os.makedirs(task['profile'])
        except Exception as err:
            logging.critical("Exception preparing Browser: %s", err.__str__())

    def find_default_interface(self):
        """Look through the list of interfaces for the non-loopback interface"""
        import psutil
        try:
            if self.interfaces is None:
                self.interfaces = {}
                # Look to see which interfaces are up
                stats = psutil.net_if_stats()
                for interface in stats:
                    if interface != 'lo' and interface[:3] != 'ifb' and stats[interface].isup:
                        self.interfaces[interface] = {'packets': 0}
                if len(self.interfaces) > 1:
                    # See which interfaces have received data
                    cnt = psutil.net_io_counters(True)
                    for interface in cnt:
                        if interface in self.interfaces:
                            self.interfaces[interface]['packets'] = \
                                cnt[interface].packets_sent + cnt[interface].packets_recv
                    remove = []
                    for interface in self.interfaces:
                        if self.interfaces[interface]['packets'] == 0:
                            remove.append(interface)
                    if len(remove):
                        for interface in remove:
                            del self.interfaces[interface]
                if len(self.interfaces) > 1:
                    # Eliminate any with the loopback address
                    remove = []
                    addresses = psutil.net_if_addrs()
                    for interface in addresses:
                        if interface in self.interfaces:
                            for address in addresses[interface]:
                                if address.address == '127.0.0.1':
                                    remove.append(interface)
                                    break
                    if len(remove):
                        for interface in remove:
                            del self.interfaces[interface]
        except Exception:
            pass

    def launch_browser(self, command_line):
        """Launch the browser and keep track of the process"""
        logging.debug(command_line)
        self.proc = subprocess.Popen(command_line, shell=True)

    def stop(self, job, task):
        """Terminate the browser (gently at first but forced if needed)"""
        from .os_util import kill_all
        logging.debug("Stopping browser")
        if self.proc:
            kill_all(os.path.basename(self.path), False)
            self.proc.terminate()
            self.proc.kill()
            self.proc = None

    def wait_for_idle(self):
        """Wait for no more than 20% of a single core used for 500ms"""
        import psutil
        logging.debug("Waiting for Idle...")
        cpu_count = psutil.cpu_count()
        if cpu_count > 0:
            target_pct = 20. / float(cpu_count)
            idle_start = None
            end_time = monotonic.monotonic() + self.START_BROWSER_TIME_LIMIT
            idle = False
            while not idle and monotonic.monotonic() < end_time:
                check_start = monotonic.monotonic()
                pct = psutil.cpu_percent(interval=0.1)
                if pct <= target_pct:
                    if idle_start is None:
                        idle_start = check_start
                    if monotonic.monotonic() - idle_start > 0.5:
                        idle = True
                else:
                    idle_start = None

    def clear_profile(self, task):
        """Delete the browser profile directory"""
        if os.path.isdir(task['profile']):
            end_time = monotonic.monotonic() + 30
            while monotonic.monotonic() < end_time:
                try:
                    shutil.rmtree(task['profile'])
                except Exception:
                    pass
                if os.path.isdir(task['profile']):
                    time.sleep(0.1)
                else:
                    break

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        if task['log_data']:
            self.recording = True
            ver = platform.uname()
            task['page_data']['osVersion'] = '{0} {1}'.format(ver[0], ver[2])
            # Spawn tcpdump
            if self.tcpdump_enabled:
                self.pcap_file = os.path.join(task['dir'], task['prefix']) + '.cap'
                if platform.system() == 'Windows':
                    tcpdump = os.path.join(self.support_path, 'tcpdump.exe')
                    args = [tcpdump, 'start', self.pcap_file]
                else:
                    interface = 'any' if self.job['interface'] is None else self.job['interface']
                    args = ['sudo', 'tcpdump', '-p', '-i', interface, '-s', '0',
                            '-w', self.pcap_file]
                logging.debug(' '.join(args))
                self.tcpdump = subprocess.Popen(args)
                # give it time to actually start capturing
                time.sleep(1)

            # start the background thread for monitoring CPU and bandwidth
            self.usage_queue = Queue.Queue()
            self.thread = threading.Thread(target=self.background_thread)
            self.thread.daemon = True
            self.thread.start()

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        self.recording = False
        if self.thread is not None:
            self.thread.join()
            self.thread = None
        # record the CPU/Bandwidth/memory info
        if self.usage_queue is not None and not self.usage_queue.empty() and task is not None:
            file_path = os.path.join(task['dir'], task['prefix']) + '_progress.csv.gz'
            gzfile = gzip.open(file_path, 'wb')
            if gzfile:
                gzfile.write("Offset Time (ms),Bandwidth In (bps),CPU Utilization (%),Memory\n")
                while not self.usage_queue.empty():
                    snapshot = self.usage_queue.get_nowait()
                    gzfile.write('{0:d},{1:d},{2:0.2f},-1\n'.format(
                        snapshot['time'], snapshot['bw'], snapshot['cpu']))
                gzfile.close()
        if self.tcpdump is not None:
            logging.debug('Stopping tcpdump')
            if platform.system() == 'Windows':
                tcpdump = os.path.join(self.support_path, 'tcpdump.exe')
                subprocess.call([tcpdump, 'stop'])
            else:
                subprocess.call(['sudo', 'killall', 'tcpdump'])
            self.tcpdump = None
            from .os_util import kill_all
            from .os_util import wait_for_all
            kill_all('tcpdump', False)
            wait_for_all('tcpdump')
            if self.pcap_file is not None:
                logging.debug('Compressing pcap')
                if os.path.isfile(self.pcap_file):
                    pcap_out = self.pcap_file + '.gz'
                    with open(self.pcap_file, 'rb') as f_in:
                        with gzip.open(pcap_out, 'wb', 7) as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    if os.path.isfile(pcap_out):
                        #self.pcap_thread = threading.Thread(target=self.process_pcap)
                        #self.pcap_thread.daemon = True
                        #self.pcap_thread.start()
                        try:
                            os.remove(self.pcap_file)
                        except Exception:
                            pass

    def wait_for_processing(self, _):
        """Wait for any background processing threads to finish"""
        if self.pcap_thread is not None:
            logging.debug('Waiting for pcap processing to finish')
            self.pcap_thread.join()
            self.pcap_thread = None
        self.pcap_file = None

    def process_pcap(self):
        """Process the pcap in a background thread"""
        pcap_file = self.pcap_file + '.gz'
        if os.path.isfile(pcap_file):
            path_base = os.path.join(self.task['dir'], self.task['prefix'])
            slices_file = path_base + '_pcap_slices.json.gz'
            pcap_parser = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                       'support', "pcap-parser.py")
            cmd = ['python', pcap_parser, '--json', '-i', pcap_file, '-d', slices_file]
            logging.debug(cmd)
            subprocess.call(cmd)

    def get_net_bytes(self):
        """Get the bytes received, ignoring the loopback interface"""
        import psutil
        bytes_in = 0
        net = psutil.net_io_counters(True)
        for interface in net:
            if self.interfaces is not None:
                if interface in self.interfaces:
                    bytes_in += net[interface].bytes_recv
            elif interface != 'lo' and interface[:3] != 'ifb':
                bytes_in += net[interface].bytes_recv
        return bytes_in

    def background_thread(self):
        """Background thread for monitoring CPU and bandwidth usage"""
        import psutil
        last_time = start_time = monotonic.monotonic()
        last_bytes = self.get_net_bytes()
        snapshot = {'time': 0, 'cpu': 0.0, 'bw': 0}
        self.usage_queue.put(snapshot)
        while self.recording:
            snapshot = {'bw': 0}
            snapshot['cpu'] = psutil.cpu_percent(interval=0.1)
            now = monotonic.monotonic()
            snapshot['time'] = int((now - start_time) * 1000)
            # calculate the bandwidth over the last interval in Kbps
            bytes_in = self.get_net_bytes()
            if now > last_time:
                snapshot['bw'] = int((bytes_in - last_bytes) * 8.0 / (now - last_time))
            last_time = now
            last_bytes = bytes_in
            self.usage_queue.put(snapshot)
