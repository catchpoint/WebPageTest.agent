#!/usr/bin/env python
# Copyright 2018 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Watchdog helper"""
import os
import time

def main():
    """Startup and initialization"""
    import argparse
    parser = argparse.ArgumentParser(description='wptagent watchdog helper.', prog='alive.py')
    parser.add_argument('--file', help="File to check for modifications within the last hour.")
    parser.add_argument('--ping', help="Address to ping as a last resort.")
    options, _ = parser.parse_known_args()

    # If the system has been up for less than an hour, it is OK (avoid boot loops).
    if os.path.isfile('/proc/uptime'):
        with open('/proc/uptime', 'r') as f_in:
            uptime_seconds = int(float(f_in.readline().split()[0]))
            if uptime_seconds < 3600:
                print 'OK: Freshly booted ({0:d} seconds)'.format(uptime_seconds)
                exit(0)

    # Check if the watchdog file has been updated in the last hour.
    if options.file and os.path.isfile(options.file):
        elapsed = int(time.time() - os.path.getmtime(options.file))
        if elapsed < 3600:
            print 'OK: File last modified {0:d} seconds ago'.format(elapsed)
            exit(0)

    # Ping the provided address if requested.
    if options.ping:
        response = os.system('ping -c 2 -i 0.2 -n -W 1 {0} > /dev/null 2>&1'.format(options.ping))
        if response == 0:
            print 'OK: ping succeeded'
            # Update the alive file to avoid pinging all the time
            if options.file:
                with open(options.file, 'a'):
                    os.utime(options.file, None)
            exit(0)

    print 'FAIL: No checks passed'
    exit(1)

if __name__ == '__main__':
    main()
