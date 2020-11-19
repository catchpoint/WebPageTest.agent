#!/usr/bin/env python
# Copyright 2019 WebPageTest LLC.
# Copyright 2018 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Watchdog helper"""
import os
import platform
import subprocess
import time
import psutil


def main():
    """Startup and initialization"""
    import argparse
    parser = argparse.ArgumentParser(description='wptagent watchdog helper.', prog='alive.py')
    parser.add_argument('--file', help="File to check for modifications within the last hour.")
    parser.add_argument('--ping', help="Address to ping as a last resort.")
    parser.add_argument('--reboot', action='store_true', default=False,
                        help="Reboot if the watchdog fails.")
    options, _ = parser.parse_known_args()

    # If the system has been up for less than an hour, it is OK (avoid boot loops).
    if os.path.isfile('/proc/uptime'):
        with open('/proc/uptime', 'r') as f_in:
            uptime_seconds = int(float(f_in.readline().split()[0]))
            if uptime_seconds < 3600:
                print('OK: Freshly booted ({0:d} seconds)'.format(uptime_seconds))
                exit(0)
    elif platform.system() == "Windows":
        uptime_seconds = int(time.time()) - int(psutil.boot_time())
        if uptime_seconds < 3600:
            print('OK: Freshly booted ({0:d} seconds)'.format(uptime_seconds))
            exit(0)

    # Check if the watchdog file has been updated in the last hour.
    if options.file and os.path.isfile(options.file):
        elapsed = int(time.time() - os.path.getmtime(options.file))
        if elapsed < 3600:
            print('OK: File last modified {0:d} seconds ago'.format(elapsed))
            exit(0)

    # Ping the provided address if requested.
    if options.ping and platform.system() != "Windows":
        response = os.system('ping -c 2 -i 0.2 -n -W 1 {0} > /dev/null 2>&1'.format(options.ping))
        if response == 0:
            print('OK: ping succeeded')
            # Update the alive file to avoid pinging all the time
            if options.file:
                with open(options.file, 'a'):
                    os.utime(options.file, None)
            exit(0)

    print('FAIL: No checks passed')
    if options.reboot:
        if platform.system() == 'Windows':
            subprocess.call(['shutdown', '/r', '/f'])
        else:
            subprocess.call(['sudo', 'reboot'])
    exit(1)


if __name__ == '__main__':
    main()
