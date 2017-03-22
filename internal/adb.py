# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""ADB command-line interface"""
import logging
import os
import re
import subprocess
from threading import Timer

class Adb(object):
    """ADB command-line interface"""
    def __init__(self, options):
        self.device = options.device
        self.ping_address = None
        self.screenrecord = None
        self.tcpdump = None
        self.version = None
        self.short_version = None

    def run(self, cmd, timeout_sec=60, silent=False):
        """Run a shell command with a time limit and get the output"""
        stdout = None
        timer = None
        try:
            if not silent:
                logging.debug(' '.join(cmd))
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            timer = Timer(timeout_sec, proc.kill)
            timer.start()
            stdout, _ = proc.communicate()
            if not silent and stdout is not None and len(stdout):
                logging.debug(stdout[:100])
        except Exception:
            logging.debug('Error running command')
        finally:
            if timer is not None:
                timer.cancel()
        return stdout

    def build_adb_command(self, args):
        """Build an adb command with the (optional) device ID"""
        cmd = ['adb']
        if self.device is not None:
            cmd.extend(['-s', self.device])
        cmd.extend(args)
        return cmd

    def shell(self, args, timeout_sec=60, silent=False):
        """Run an adb shell command"""
        cmd = self.build_adb_command(['shell'])
        cmd.extend(args)
        return self.run(cmd, timeout_sec, silent)

    def su(self, command, timeout_sec=60, silent=False):
        """Ren a command as su"""
        cmd = ['su', '-c', command]
        return self.shell(cmd, timeout_sec, silent)

    def adb(self, args, silent=False):
        """Run an arbitrary adb command"""
        ret = False
        cmd = self.build_adb_command(args)
        if not silent:
            logging.debug(' '.join(cmd))
        try:
            stdout = subprocess.check_output(cmd)
            if not silent and stdout is not None and len(stdout):
                logging.debug(stdout[:100])
            ret = True
        except Exception:
            ret = False
        return ret

    def start(self):
        """ Do some startup check to make sure adb is installed"""
        import psutil
        ret = False
        out = self.run(self.build_adb_command(['devices']))
        if out is not None:
            ret = True
            # Set the CPU affinity for adb which helps avoid hangs
            for proc in psutil.process_iter():
                if proc.name() == "adb.exe" or proc.name() == "adb":
                    proc.cpu_affinity([0])
        return ret

    def start_screenrecord(self):
        """Start a screenrecord session on the device"""
        self.shell(['rm', '/data/local/tmp/wpt_video.mp4'])
        try:
            cmd = self.build_adb_command(['shell', 'screenrecord', '--verbose',
                                          '--bit-rate', '8000000',
                                          '/data/local/tmp/wpt_video.mp4'])
            self.screenrecord = subprocess.Popen(cmd)
        except Exception:
            pass

    def stop_screenrecord(self, local_file):
        """Stop a screen record and download the video to local_file"""
        if self.screenrecord is not None:
            self.shell(['killall', '-SIGINT', 'screenrecord'])
            self.screenrecord.communicate()
            self.screenrecord = None
            self.adb(['pull', '/data/local/tmp/wpt_video.mp4', local_file])
            self.shell(['rm', '/data/local/tmp/wpt_video.mp4'])

    def start_tcpdump(self):
        """Start a tcpdump capture"""
        tcpdump_binary = '/data/local/tmp/tcpdump474'
        capture_file = '/data/local/tmp/tcpdump.cap'
        local_binary = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                    'support', 'android', 'tcpdump')
        out = self.su('ls {0}'.format(tcpdump_binary))
        if out.find('No such') > -1:
            self.adb(['push', local_binary, tcpdump_binary])
            self.su('chown root {0}'.format(tcpdump_binary))
            self.su('chmod 755 {0}'.format(tcpdump_binary))
        cmd = self.build_adb_command(['shell', 'su', '-c',
                                      '{0} -i any -p -s 0 -w {1}'.format(tcpdump_binary,
                                                                         capture_file)])
        try:
            self.tcpdump = subprocess.Popen(cmd)
        except Exception:
            pass

    def stop_tcpdump(self, local_file):
        """Stop a tcpdump capture and download to local_file"""
        if self.tcpdump is not None:
            capture_file = '/data/local/tmp/tcpdump.cap'
            self.su('killall -SIGINT tcpdump474')
            self.tcpdump.communicate()
            self.tcpdump = None
            self.su('chmod 666 {0}'.format(capture_file))
            self.adb(['pull', capture_file, local_file])
            self.su('rm {0}'.format(capture_file))

    def get_battery_stats(self):
        """Get the temperature andlevel of the battery"""
        ret = {}
        out = self.shell(['dumpsys', 'battery'], silent=True)
        if out is not None:
            for line in out.splitlines():
                match = re.search(r'^\s*level:\s*(\d+)', line)
                if match:
                    ret['level'] = int(match.group(1))
                match = re.search(r'^\s*temperature:\s*(\d+)', line)
                if match:
                    ret['temp'] = float(match.group(1)) / 10.0
        return ret

    def ping(self, address):
        """Ping the provided network address"""
        ret = None
        if address is not None:
            out = self.shell(['ping', '-n', '-c3', '-i0.2', '-w5', address], silent=True)
            if out is not None:
                for line in out.splitlines():
                    match = re.search(r'^\s*rtt\s[^=]*=\s*([\d\.]*)', line)
                    if match:
                        ret = float(match.group(1))
            if ret is None:
                logging.debug('%s is unreachable', address)
            else:
                logging.debug('%s rtt %0.3f ms', address, ret)
        return ret

    def cleanup_device(self):
        """Do some device-level cleanup"""
        # Clear notifications
        self.su('service call notification 1')
        # Close some known apps that pop-over
        self.shell(['am', 'force-stop', 'com.motorola.ccc.ota'])
        self.shell(['am', 'force-stop', 'com.google.android.apps.docs'])
        self.shell(['am', 'force-stop', 'com.samsung.android.MtpApplication'])
        # Cleanup the downloads folders
        self.shell(['rm', '-rf', '/sdcard/Download/*', '/sdcard/Backucup', '/sdcard/UCDownloads'])
        self.su('rm -rf /data/media/0/Download/* /data/media/0/Backucup /data/media/0/UCDownloads')
        # See if there are any system dialogs that need dismissing
        out = self.shell(['dumpsys', 'window', 'windows'], silent=True)
        if re.search(r'Window #[^\n]*Application Error\:', out) is not None or \
                re.search(r'Window #[^\n]*systemui\.usb\.UsbDebuggingActivity', out) is not None:
            logging.warning('Dismissing system dialog')
            self.shell(['input', 'keyevent', 'KEYCODE_DPAD_RIGHT'], silent=True)
            self.shell(['input', 'keyevent', 'KEYCODE_DPAD_RIGHT'], silent=True)
            self.shell(['input', 'keyevent', 'KEYCODE_ENTER'], silent=True)

    def is_device_ready(self):
        """Check to see if the device is ready to run tests"""
        is_ready = True
        if self.version is None:
            self.cleanup_device()
            out = self.shell(['getprop', 'ro.build.version.release'], silent=True)
            if out is not None:
                self.version = 'Android ' + out.strip()
                match = re.search(r'^(\d+\.\d+)', out)
                if match:
                    self.short_version = float(match.group(1))
        battery = self.get_battery_stats()
        logging.debug(battery)
        if 'level' in battery and battery['level'] < 50:
            logging.info("Device not ready, low battery: %d %%", battery['level'])
            is_ready = False
        if 'temp' in battery and battery['temp'] > 35.0:
            logging.info("Device not ready, high temperature: %0.1f degrees", battery['temp'])
            is_ready = False
        # Try pinging the network (prefer the gateway but fall back to DNS or 8.8.8.8)
        net_ok = False
        if self.ping(self.ping_address) is not None:
            net_ok = True
        else:
            addresses = []
            props = self.shell(['getprop'])
            gateway = None
            if props is not None:
                for line in props.splitlines():
                    match = re.search(r'^\[net\.dns\d\]:\s+\[([^\]]*)\]', line)
                    if match:
                        dns = match.group(1)
                        if dns not in addresses:
                            addresses.append(dns)
                    match = re.search(r'^\[dhcp\.[^\.]+\.dns\d\]:\s+\[([^\]]*)\]', line)
                    if match:
                        dns = match.group(1)
                        if dns not in addresses:
                            addresses.append(dns)
                    match = re.search(r'^\[dhcp\.[^\.]+\.gateway\]:\s+\[([^\]]*)\]', line)
                    if match:
                        gateway = match.group(1)
            if gateway is not None:
                addresses.insert(0, gateway)
            addresses.append('8.8.8.8')
            for address in addresses:
                if self.ping(address) is not None:
                    self.ping_address = address
                    net_ok = True
                    break
        if not net_ok:
            logging.info("Device not ready, network not responding")
            is_ready = False
        return is_ready
