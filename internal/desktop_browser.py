# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for desktop browsers"""
import gzip
import logging
import os
import Queue
import shutil
import subprocess
import threading
import time
import constants
import monotonic

class DesktopBrowser(object):
    """Desktop Browser base"""
    def __init__(self, path, job):
        self.path = path
        self.proc = None
        self.job = job
        self.recording = False
        self.usage_queue = None
        self.thread = None

    def prepare(self, _, task):
        """Prepare the profile/OS for the browser"""
        try:
            from .os_util import kill_all
            from .os_util import flush_dns
            logging.debug("Preparing browser")
            kill_all(os.path.basename(self.path), True)
            flush_dns()
            if 'profile' in task:
                if not task['cached'] and os.path.isdir(task['profile']):
                    logging.debug("Clearing profile %s", task['profile'])
                    shutil.rmtree(task['profile'])
                if not os.path.isdir(task['profile']):
                    os.makedirs(task['profile'])
        except Exception as err:
            logging.critical("Exception preparing Browser: %s", err.__str__())

    def launch_browser(self, command_line):
        """Launch the browser and keep track of the process"""
        logging.debug(command_line)
        self.proc = subprocess.Popen(command_line, shell=True)

    def stop(self):
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
            end_time = monotonic.monotonic() + constants.START_BROWSER_TIME_LIMIT
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
                shutil.rmtree(task['profile'])
                if os.path.isdir(task['profile']):
                    time.sleep(0.1)
                else:
                    break

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        if task['log_data']:
            self.recording = True
            self.usage_queue = Queue.Queue()
            self.thread = threading.Thread(target=self.background_thread)
            self.thread.daemon = True
            self.thread.start()

    def on_stop_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        self.recording = False
        if self.thread is not None:
            self.thread.join()
            self.thread = None
        # record the CPU/Bandwidth/memory info
        if self.usage_queue is not None and not self.usage_queue.empty() and task is not None:
            file_path = os.path.join(task['dir'], task['prefix']) + 'progress.csv.gz'
            gzfile = gzip.open(file_path, 'wb')
            if gzfile:
                gzfile.write("Offset Time (ms),Bandwidth In (bps),CPU Utilization (%),Memory\n")
                while not self.usage_queue.empty():
                    snapshot = self.usage_queue.get_nowait()
                    gzfile.write('{0:d},{1:d},{2:0.2f},-1\n'.format(
                        snapshot['time'], snapshot['bw'], snapshot['cpu']))
                gzfile.close()

    def get_net_bytes(self):
        """Get the bytes received, ignoring the loopback interface"""
        import psutil
        bytes_in = 0
        net = psutil.net_io_counters(True)
        for interface in net:
            if interface != 'lo':
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
