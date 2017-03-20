# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""ADB command-line interface"""
import logging
import re
import subprocess
from threading import Timer

class Adb(object):
    """ADB command-line interface"""
    def __init__(self, options):
        self.device = options.device
        self.ping_address = None

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
            if not silent:
                logging.debug(stdout[:100])
        except Exception:
            logging.debug('Error running command')
        finally:
            if timer is not None:
                timer.cancel()
        return stdout

    def shell(self, args, timeout_sec=60, silent=False):
        """Run an adb shell command"""
        cmd = ['adb', 'shell']
        cmd.extend(args)
        return self.run(cmd, timeout_sec, silent)

    def su(self, command, timeout_sec=60, silent=False):
        """Ren a command as su"""
        cmd = ['su', '-c', command]
        return self.shell(cmd, timeout_sec, silent)

    def adb(self, args, silent=False):
        """Run an arbitrary adb command"""
        ret = False
        cmd = ['adb']
        cmd.extend(args)
        if not silent:
            logging.debug(' '.join(cmd))
        result = subprocess.call(cmd)
        if result == 0:
            ret = True
        return ret

    def start(self):
        """ Do some startup check to make sure adb is installed"""
        import psutil
        ret = False
        out = self.run(['adb', 'devices'])
        if out is not None:
            ret = True
            # Set the CPU affinity for adb which helps avoid hangs
            for proc in psutil.process_iter():
                if proc.name() == "adb.exe" or proc.name() == "adb":
                    proc.cpu_affinity([0])
        return ret

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

    def is_device_ready(self):
        """Check to see if the device is ready to run tests"""
        is_ready = True
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
