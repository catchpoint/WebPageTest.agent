# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for desktop browsers"""
import gzip
import logging
import math
import multiprocessing
import os
import platform
import shutil
import signal
import subprocess
import threading
import time
import monotonic
import ujson as json
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


class DesktopBrowser(BaseBrowser):
    """Desktop Browser base"""
    START_BROWSER_TIME_LIMIT = 30

    def __init__(self, path, options, job):
        BaseBrowser.__init__(self)
        self.path = path
        self.proc = None
        self.job = job
        self.recording = False
        self.usage_queue = None
        self.thread = None
        self.cleanup_thread = None
        self.options = options
        self.interfaces = None
        self.tcpdump_enabled = bool('tcpdump' in job and job['tcpdump'])
        self.tcpdump = None
        self.ffmpeg = None
        self.video_capture_running = False
        self.video_processing = None
        self.pcap_file = None
        self.pcap_thread = None
        self.task = None
        self.cpu_start = None
        self.throttling_cpu = False
        self.screen_width = 1920
        self.screen_height = 1200
        self.device_pixel_ratio = None
        self.stopping = False
        self.is_chrome = False
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        self.stopping = False
        self.task = task
        self.find_default_interface()
        if platform.system() == 'Windows':
            self.prepare_windows()
            self.cleanup_thread = threading.Thread(target=self.background_cleanup)
            self.cleanup_thread.daemon = True
            self.cleanup_thread.start()
        if self.tcpdump_enabled:
            os.environ["SSLKEYLOGFILE"] = os.path.join(task['dir'], task['prefix']) + '_keylog.log'
        else:
            os.environ["SSLKEYLOGFILE"] = ''
        try:
            from .os_util import kill_all
            from .os_util import flush_dns
            logging.debug("Preparing browser")
            kill_all(os.path.basename(self.path), True)
            if 'browser_info' in job and 'other_exes' in job['browser_info']:
                for exe in job['browser_info']['other_exes']:
                    kill_all(exe, True)
            if self.options.shaper is None or self.options.shaper != 'none':
                flush_dns()
            if 'profile' in task:
                if not task['cached'] and os.path.isdir(task['profile']):
                    logging.debug("Clearing profile %s", task['profile'])
                    shutil.rmtree(task['profile'])
                if not os.path.isdir(task['profile']):
                    os.makedirs(task['profile'])
        except Exception as err:
            logging.exception("Exception preparing Browser: %s", err.__str__())
        # Modify the hosts file for non-Chrome browsers
        self.restore_hosts()
        if not self.is_chrome and 'dns_override' in task:
            self.modify_hosts(task, task['dns_override'])

    def modify_hosts(self, task, hosts):
        """Add entries to the system's hosts file (non-Windows currently)"""
        hosts_backup = os.path.join(os.path.abspath(os.path.dirname(__file__)), "hosts.backup")
        hosts_tmp = os.path.join(task['dir'], "hosts.wpt")
        hosts_file = '/etc/hosts'
        if len(hosts) and platform.system() != 'Windows':
            logging.debug('Modifying hosts file:')
            try:
                hosts_text = None
                with open(hosts_file, 'r') as f_in:
                    hosts_text = f_in.read()
                if hosts_text is not None:
                    hosts_text += "\n"
                    for pair in hosts:
                        hosts_text += "{0}    {1}\n".format(pair[1], pair[0])
                    with open(hosts_tmp, 'w') as f_out:
                        f_out.write(hosts_text)
                    subprocess.call(['sudo', 'cp', hosts_file, hosts_backup])
                    subprocess.call(['sudo', 'cp', hosts_tmp, hosts_file])
                    os.unlink(hosts_tmp)
                    logging.debug(hosts_text)
            except Exception as err:
                logging.exception("Exception modifying hosts file: %s", err.__str__())

    def restore_hosts(self):
        """See if we have a backup hosts file to restore"""
        hosts_backup = os.path.join(os.path.abspath(os.path.dirname(__file__)), "hosts.backup")
        hosts_file = '/etc/hosts'
        if os.path.isfile(hosts_backup) and platform.system() != 'Windows':
            logging.debug('Restoring backup of hosts file')
            subprocess.call(['sudo', 'cp', hosts_backup, hosts_file])
            subprocess.call(['sudo', 'rm', hosts_backup])

    # pylint: disable=E0611,E0401,E1101
    def close_top_window(self, hwnd, _):
        """Close all top-level windows"""
        keep_titles = ['Start']
        keep_classes = ['ConsoleWindowClass', 'Windows.UI.Core.CoreWindow']
        keep_exes = ['explorer.exe', 'cmd.exe', 'teamviewer.exe']
        try:
            import win32api
            import win32con
            import win32event
            import win32gui
            import win32process
            import psutil
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                window_class = win32gui.GetClassName(hwnd)
                _, proccess_id = win32process.GetWindowThreadProcessId(hwnd)
                exe = os.path.basename(psutil.Process(proccess_id).exe()).lower()
                if len(window_title) and \
                        window_title not in keep_titles and \
                        window_class not in keep_classes and \
                        exe not in keep_exes:
                    placement = win32gui.GetWindowPlacement(hwnd)
                    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                    width = abs(right - left)
                    height = abs(bottom - top)
                    if width > 0 and height > 0 and \
                            top >= 0 and left >= 0 and \
                            placement[1] != win32con.SW_SHOWMINIMIZED and \
                            placement[1] != win32con.SW_MINIMIZE and \
                            placement[1] != win32con.SW_FORCEMINIMIZE:
                        logging.debug("Closing Window: %s (%s) : %d,%d %dx%d : %d - %s",
                                      window_title, window_class, left, top, width, height,
                                      placement[1], exe)
                        handle = win32api.OpenProcess(
                            win32con.PROCESS_TERMINATE | win32con.SYNCHRONIZE |
                            win32con.PROCESS_QUERY_INFORMATION,
                            0, proccess_id)
                        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                        if handle:
                            result = win32event.WaitForSingleObject(handle, 10000)
                            if result == win32event.WAIT_TIMEOUT:
                                logging.debug("Terminating process for: %s (%s)",
                                              window_title, window_class)
                                win32api.TerminateProcess(handle, 0)
                            win32api.CloseHandle(handle)
        except Exception as err:
            pass

    def close_top_dialog(self, hwnd, _):
        """Close all top-level dialogs"""
        close_classes = ["#32770", "Notepad", "Internet Explorer_Server"]
        keep_titles = ['Delete Browsing History', 'Shut Down Windows', 'TeamViewer']
        try:
            import win32gui
            import win32con
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                window_class = win32gui.GetClassName(hwnd)
                if window_class in close_classes and window_title not in keep_titles:
                    logging.debug("Closing Window/Dialog: %s (%s)", window_title, window_class)
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as err:
            logging.exception("Exception closing window: %s", err.__str__())

    def close_dialogs(self):
        """Send a close message to any top-level dialogs"""
        try:
            import win32gui
            win32gui.EnumWindows(self.close_top_dialog, None)
        except Exception:
            pass

    def background_cleanup(self):
        """Background thread to do cleanup while the test is running"""
        while not self.stopping:
            self.close_dialogs()
            time.sleep(0.5)

    def prepare_windows(self):
        """Do Windows-specific cleanup and prep"""
        try:
            from .os_util import kill_all
            import win32gui
            kill_all("WerFault.exe", True)
            win32gui.EnumWindows(self.close_top_window, None)
        except Exception:
            pass
    # pylint: enable=E0611,E0401,E1101

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
        command_line = self.enable_cpu_throttling(command_line)
        logging.debug(command_line)
        if platform.system() == 'Windows':
            self.proc = subprocess.Popen(command_line, shell=True)
        else:
            self.proc = subprocess.Popen(command_line, preexec_fn=os.setsid, shell=True)

    def close_browser(self, job, _task):
        """Terminate the browser but don't do all of the cleanup that stop does"""
        if self.proc:
            logging.debug("Closing browser")
            from .os_util import kill_all
            kill_all(os.path.basename(self.path), False)
            if 'browser_info' in job and 'other_exes' in job['browser_info']:
                for exe in job['browser_info']['other_exes']:
                    kill_all(exe, False)
            try:
                if platform.system() != 'Windows':
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.terminate()
                self.proc.kill()
            except Exception:
                pass
            self.proc = None

    def stop(self, job, task):
        """Terminate the browser (gently at first but forced if needed)"""
        self.stopping = True
        self.recording = False
        logging.debug("Stopping browser")
        self.close_browser(job, task)
        self.disable_cpu_throttling()
        self.restore_hosts()
        # Clean up the downloads folder in case anything was downloaded
        if platform.system() == 'Linux':
            downloads = os.path.expanduser('~/Downloads')
            if os.path.isdir(downloads):
                try:
                    shutil.rmtree(downloads)
                    os.makedirs(downloads)
                except Exception:
                    pass
        if self.cleanup_thread is not None:
            self.cleanup_thread.join(10)
            self.cleanup_thread = None
        if self.thread is not None:
            self.thread.join(10)
            self.thread = None

    def wait_for_idle(self):
        """Wait for no more than 50% of a single core used for 500ms"""
        import psutil
        logging.debug("Waiting for Idle...")
        cpu_count = psutil.cpu_count()
        if cpu_count > 0:
            target_pct = 50. / float(cpu_count)
            idle_start = None
            end_time = monotonic.monotonic() + self.START_BROWSER_TIME_LIMIT
            idle = False
            while not idle and monotonic.monotonic() < end_time:
                check_start = monotonic.monotonic()
                pct = psutil.cpu_percent(interval=0.1)
                if pct <= target_pct:
                    if idle_start is None:
                        idle_start = check_start
                    if monotonic.monotonic() - idle_start >= 0.4:
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

    def execute_js(self, _):
        """Run javascipt (stub for overriding"""
        return None

    def prepare_script_for_record(self, script):
        """Convert a script command into one that first removes the orange frame"""
        return "(function() {" \
               "var wptDiv = document.getElementById('wptorange');" \
               "if(wptDiv) {wptDiv.parentNode.removeChild(wptDiv);}" \
               "window.requestAnimationFrame(function(){" \
               "window.requestAnimationFrame(function(){" + script + "});"\
               "});" \
               "})();"

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        import psutil
        if self.device_pixel_ratio is None:
            self.device_pixel_ratio = 1.0
            try:
                ratio = self.execute_js('window.devicePixelRatio')
                if ratio is not None:
                    self.device_pixel_ratio = max(1.0, float(ratio))
            except Exception:
                pass
        if task['log_data']:
            if not self.job['shaper'].configure(self.job, task):
                self.task['error'] = "Error configuring traffic-shaping"
            self.cpu_start = psutil.cpu_times()
            self.recording = True
            ver = platform.uname()
            task['page_data']['osVersion'] = '{0} {1}'.format(ver[0], ver[2])
            task['page_data']['os_version'] = '{0} {1}'.format(ver[0], ver[2])
            # Spawn tcpdump
            if self.tcpdump_enabled:
                self.pcap_file = os.path.join(task['dir'], task['prefix']) + '.cap'
                interface = 'any' if self.job['interface'] is None else self.job['interface']
                if self.options.tcpdump:
                    interface = self.options.tcpdump
                if platform.system() == 'Windows':
                    tcpdump = os.path.join(self.support_path, 'windows', 'WinDump.exe')
                    if interface == 'any':
                        args = [tcpdump, '-p', '-s', '0', '-w', self.pcap_file]
                    else:
                        args = [tcpdump, '-p', '-i', interface, '-s', '0',
                                '-w', self.pcap_file]
                    logging.debug(' '.join(args))
                    self.tcpdump = subprocess.Popen(args,
                                                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                else:
                    args = ['sudo', 'tcpdump', '-p', '-i', interface, '-s', '0',
                            '-w', self.pcap_file]
                    logging.debug(' '.join(args))
                    self.tcpdump = subprocess.Popen(args)
                # Wait for the capture file to start growing
                end_time = monotonic.monotonic() + 5
                started = False
                while not started and monotonic.monotonic() < end_time:
                    if os.path.isfile(self.pcap_file):
                        started = True
                    time.sleep(0.1)

            # Start video capture
            if self.job['capture_display'] is not None and not self.job['disable_video']:
                if task['navigated']:
                    self.execute_js(SET_ORANGE)
                    time.sleep(1)
                task['video_file'] = os.path.join(task['dir'], task['prefix']) + '_video.mp4'
                if platform.system() == 'Windows':
                    from win32api import GetSystemMetrics
                    self.screen_width = GetSystemMetrics(0)
                    self.screen_height = GetSystemMetrics(1)
                elif platform.system() == 'Darwin':
                    try:
                        from AppKit import NSScreen
                        self.screen_width = int(NSScreen.screens()[0].frame().size.width)
                        self.screen_height = int(NSScreen.screens()[0].frame().size.height)
                    except Exception:
                        pass
                task['width'] = min(task['width'], self.screen_width)
                task['height'] = min(task['height'], self.screen_height)
                if platform.system() == 'Darwin':
                    width = int(math.ceil(task['width'] * self.device_pixel_ratio))
                    height = int(math.ceil(task['height'] * self.device_pixel_ratio))
                    args = ['ffmpeg', '-f', 'avfoundation',
                            '-i', str(self.job['capture_display']),
                            '-r', str(self.job['fps']),
                            '-filter:v',
                            'crop={0:d}:{1:d}:0:0'.format(width, height),
                            '-codec:v', 'libx264rgb', '-crf', '0', '-preset', 'ultrafast',
                            task['video_file']]
                else:
                    grab = 'gdigrab' if platform.system() == 'Windows' else 'x11grab'
                    args = ['ffmpeg', '-f', grab, '-video_size',
                            '{0:d}x{1:d}'.format(task['width'], task['height']),
                            '-framerate', str(self.job['fps']),
                            '-draw_mouse', '0', '-i', str(self.job['capture_display']),
                            '-codec:v', 'libx264rgb', '-crf', '0', '-preset', 'ultrafast',
                            task['video_file']]
                if platform.system() in ['Linux', 'Darwin']:
                    args.insert(0, 'nice')
                    args.insert(1, '-n')
                    args.insert(2, '10')
                logging.debug(' '.join(args))
                try:
                    if platform.system() == 'Windows':
                        self.ffmpeg = subprocess.Popen(args,
                                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                    else:
                        self.ffmpeg = subprocess.Popen(args)
                    # Wait up to 5 seconds for something to be captured
                    end_time = monotonic.monotonic() + 5
                    started = False
                    initial_size = None
                    while not started and monotonic.monotonic() < end_time:
                        if os.path.isfile(task['video_file']):
                            video_size = os.path.getsize(task['video_file'])
                            if initial_size == None:
                                initial_size = video_size
                            logging.debug("Video file size: %d", video_size)
                            if video_size > initial_size or video_size > 10000:
                                started = True
                        if not started:
                            time.sleep(0.1)
                    self.video_capture_running = True
                except Exception:
                    pass

            # start the background thread for monitoring CPU and bandwidth
            self.usage_queue = multiprocessing.JoinableQueue()
            self.thread = threading.Thread(target=self.background_thread)
            self.thread.daemon = True
            self.thread.start()
        self.start_cpu_throttling()

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        if self.tcpdump is not None:
            logging.debug('Stopping tcpdump')
            from .os_util import kill_all
            if platform.system() == 'Windows':
                os.kill(self.tcpdump.pid, signal.CTRL_BREAK_EVENT)
                kill_all('WinDump', False)
            else:
                subprocess.call(['sudo', 'killall', 'tcpdump'])
                kill_all('tcpdump', False)
        if self.ffmpeg is not None:
            logging.debug('Stopping video capture')
            self.video_capture_running = False
            if platform.system() == 'Windows':
                os.kill(self.ffmpeg.pid, signal.CTRL_BREAK_EVENT)
            else:
                self.ffmpeg.terminate()

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        self.stop_cpu_throttling()
        import psutil
        if self.cpu_start is not None:
            cpu_end = psutil.cpu_times()
            cpu_busy = (cpu_end.user - self.cpu_start.user) + \
                (cpu_end.system - self.cpu_start.system)
            cpu_total = cpu_busy + (cpu_end.idle - self.cpu_start.idle)
            cpu_pct = cpu_busy * 100.0 / cpu_total
            task['page_data']['fullyLoadedCPUms'] = int(cpu_busy * 1000.0)
            task['page_data']['fullyLoadedCPUpct'] = cpu_pct
            self.cpu_start = None
        self.recording = False
        if self.thread is not None:
            self.thread.join(10)
            self.thread = None
        # record the CPU/Bandwidth/memory info
        if self.usage_queue is not None and not self.usage_queue.empty() and task is not None:
            file_path = os.path.join(task['dir'], task['prefix']) + '_progress.csv.gz'
            gzfile = gzip.open(file_path, 'wb', 7)
            if gzfile:
                gzfile.write("Offset Time (ms),Bandwidth In (bps),CPU Utilization (%),Memory\n")
                while not self.usage_queue.empty():
                    snapshot = self.usage_queue.get_nowait()
                    gzfile.write('{0:d},{1:d},{2:0.2f},-1\n'.format(
                        snapshot['time'], snapshot['bw'], snapshot['cpu']))
                gzfile.close()
        if self.tcpdump is not None:
            logging.debug('Waiting for tcpdump to stop')
            from .os_util import wait_for_all
            if platform.system() == 'Windows':
                wait_for_all('WinDump')
            else:
                wait_for_all('tcpdump')
            self.tcpdump = None
        if self.ffmpeg is not None:
            logging.debug('Waiting for video capture to finish')
            self.ffmpeg.communicate()
            self.ffmpeg = None
        if platform.system() == 'Windows':
            from .os_util import kill_all
            kill_all('ffmpeg.exe', True)
        else:
            subprocess.call(['killall', '-9', 'ffmpeg'])
        self.job['shaper'].reset()

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        # kick off the video processing (async)
        if 'video_file' in task and os.path.isfile(task['video_file']):
            video_path = os.path.join(task['dir'], task['video_subdirectory'])
            support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
            if task['current_step'] == 1:
                filename = '{0:d}.{1:d}.histograms.json.gz'.format(task['run'], task['cached'])
            else:
                filename = '{0:d}.{1:d}.{2:d}.histograms.json.gz'.format(task['run'],
                                                                         task['cached'],
                                                                         task['current_step'])
            histograms = os.path.join(task['dir'], filename)
            progress_file = os.path.join(task['dir'], task['prefix']) + '_visual_progress.json.gz'
            visualmetrics = os.path.join(support_path, "visualmetrics.py")
            args = ['python', visualmetrics, '-i', task['video_file'],
                    '-d', video_path, '--force', '--quality',
                    '{0:d}'.format(self.job['imageQuality']),
                    '--viewport', '--orange', '--maxframes', '50', '--histogram', histograms,
                    '--progress', progress_file]
            if 'debug' in self.job and self.job['debug']:
                args.append('-vvvv')
            if not task['navigated']:
                args.append('--forceblank')
            if 'heroElementTimes' in self.job and self.job['heroElementTimes']:
                hero_elements_file = os.path.join(task['dir'], task['prefix']) + '_hero_elements.json.gz'
                args.extend(['--herodata', hero_elements_file])
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
            logging.debug(' '.join(args))
            self.video_processing = subprocess.Popen(args, close_fds=True)
        # Process the tcpdump
        if self.pcap_file is not None:
            logging.debug('Compressing pcap')
            if os.path.isfile(self.pcap_file):
                pcap_out = self.pcap_file + '.gz'
                with open(self.pcap_file, 'rb') as f_in:
                    with gzip.open(pcap_out, 'wb', 7) as f_out:
                        shutil.copyfileobj(f_in, f_out)
                if os.path.isfile(pcap_out):
                    self.pcap_thread = threading.Thread(target=self.process_pcap)
                    self.pcap_thread.daemon = True
                    self.pcap_thread.start()
                    try:
                        os.remove(self.pcap_file)
                    except Exception:
                        pass

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        if self.video_processing is not None:
            logging.debug('Waiting for video processing to finish')
            self.video_processing.communicate()
            self.video_processing = None
            logging.debug('Video processing complete')
            if not self.job['keepvideo']:
                try:
                    os.remove(task['video_file'])
                except Exception:
                    pass
        if self.pcap_thread is not None:
            logging.debug('Waiting for pcap processing to finish')
            self.pcap_thread.join()
            self.pcap_thread = None
        self.pcap_file = None

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
                    int(round((monotonic.monotonic() - task['run_start_time']) * 1000.0))
            path = os.path.join(task['dir'], task['prefix'] + '_page_data.json.gz')
            json_page_data = json.dumps(task['page_data'])
            logging.debug('Page Data: %s', json_page_data)
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json_page_data)

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
            try:
                stdout = subprocess.check_output(cmd)
                if stdout is not None:
                    result = json.loads(stdout)
                    if result:
                        if 'in' in result:
                            self.task['page_data']['pcapBytesIn'] = result['in']
                        if 'out' in result:
                            self.task['page_data']['pcapBytesOut'] = result['out']
                        if 'in_dup' in result:
                            self.task['page_data']['pcapBytesInDup'] = result['in_dup']
            except Exception:
                pass

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
            # if we are capturing video, make sure it doesn't get too big
            if self.ffmpeg is not None and \
                    self.video_capture_running and \
                    'video_file' in self.task and \
                    os.path.isfile(self.task['video_file']):
                video_size = os.path.getsize(self.task['video_file'])
                if video_size > 50000000:
                    logging.debug('Stopping video capture - File is too big: %d', video_size)
                    self.video_capture_running = False
                    if platform.system() == 'Windows':
                        os.kill(self.ffmpeg.pid, signal.CTRL_BREAK_EVENT)
                    else:
                        self.ffmpeg.terminate()

    def enable_cpu_throttling(self, command_line):
        """Prepare the CPU throttling if necessary"""
        if self.options.throttle and 'throttle_cpu' in self.job:
            logging.debug('CPU Throttle target: %0.3fx', self.job['throttle_cpu'])
        if self.options.throttle and 'throttle_cpu' in self.job and \
                self.job['throttle_cpu'] > 1:
            try:
                import getpass
                uid = '{0}:{0}'.format(getpass.getuser())
                cmd = ['sudo', 'cgcreate', '-a', uid, '-t', uid, '-g', 'cpu,cpuset:wptagent']
                logging.debug(' '.join(cmd))
                subprocess.check_call(cmd)
                cmd = ['sudo', 'cgset', '-r', 'cpuset.cpus="0"', 'wptagent']
                logging.debug(' '.join(cmd))
                subprocess.check_call(cmd)
                cmd = ['sudo', 'cgset', '-r', 'cpu.cfs_period_us=1000', 'wptagent']
                logging.debug(' '.join(cmd))
                subprocess.check_call(cmd)
                cmd = ['sudo', 'cgset', '-r', 'cpu.cfs_quota_us=1000', 'wptagent']
                logging.debug(' '.join(cmd))
                subprocess.check_call(cmd)
                command_line = 'cgexec -g cpu:wptagent ' + command_line
            except Exception as err:
                logging.critical("Exception enabling throttling: %s", err.__str__())
            self.throttling_cpu = True
        return command_line

    def disable_cpu_throttling(self):
        """Remove the CPU throttling if necessary"""
        if self.throttling_cpu:
            try:
                cmd = ['sudo', 'cgdelete', '-r', 'cpu,cpuset:wptagent']
                logging.debug(' '.join(cmd))
                subprocess.check_call(cmd)
            except Exception:
                pass

    def start_cpu_throttling(self):
        """Start the CPU throttling if necessary"""
        if self.options.throttle and 'throttle_cpu' in self.job:
            self.task['page_data']['throttle_cpu_requested'] = self.job['throttle_cpu_requested']
        if self.throttling_cpu:
            self.task['page_data']['throttle_cpu'] = self.job['throttle_cpu']
            try:
                # Leave the quota at 1000 and vary the period to get to the correct multiplier
                period = int(round(1000.0 * self.job['throttle_cpu']))
                cmd = ['sudo', 'cgset', '-r', 'cpu.cfs_period_us={0:d}'.format(period), 'wptagent']
                logging.debug(' '.join(cmd))
                subprocess.check_call(cmd)
            except Exception:
                pass

    def stop_cpu_throttling(self):
        """Start the CPU throttling if necessary"""
        if self.throttling_cpu:
            try:
                cmd = ['sudo', 'cgset', '-r', 'cpu.cfs_period_us=1000', 'wptagent']
                logging.debug(' '.join(cmd))
                subprocess.check_call(cmd)
            except Exception:
                pass
