# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Cross-platform support for traffic-shaping"""
import logging
import os
import platform
import subprocess

class TrafficShaper(object):
    """Main traffic-shaper interface"""
    def __init__(self):
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
        self.shaper = None
        plat = platform.system()
        if plat == "Windows":
            winver = float(".".join(platform.version().split('.')[:2]))
            if winver >= 8.1:
                self.shaper = WinShaper()

    def install(self):
        """Install and configure the traffic-shaper"""
        ret = False
        if self.shaper is not None:
            ret = self.shaper.install()
        return ret

    def remove(self):
        """Uninstall traffic-shaping"""
        ret = False
        if self.shaper is not None:
            ret = self.shaper.remove()
        return ret

    def reset(self):
        """Disable traffic-shaping"""
        ret = False
        if self.shaper is not None:
            ret = self.shaper.reset()
        return ret

    def configure(self, job):
        """Enable traffic-shaping"""
        ret = False
        in_bps = 0
        if 'bwIn' in job:
            in_bps = int(job['bwIn']) * 1000
        out_bps = 0
        if 'bwOut' in job:
            out_bps = int(job['bwOut']) * 1000
        rtt = 0
        if 'latency' in job:
            rtt = int(job['latency'])
        plr = .0
        if 'plr' in job:
            plr = float(job['plr'])
        if self.shaper is not None:
            ret = self.shaper.configure(in_bps, out_bps, rtt, plr)
        return ret


#
# winshaper
#
class WinShaper(object):
    """Windows 8.1+ traffic-shaper using winshaper"""
    def __init__(self):
        self.exe = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                "support", "winshaper", "shaper.exe")

    def install(self):
        """Install and configure the traffic-shaper"""
        command = [self.exe, 'install']
        logging.debug(' '.join(command))
        return subprocess.call(command) == 0

    def remove(self):
        """Uninstall traffic-shaping"""
        command = [self.exe, 'remove']
        logging.debug(' '.join(command))
        return subprocess.call(command) == 0

    def reset(self):
        """Disable traffic-shaping"""
        command = [self.exe, 'reset']
        logging.debug(' '.join(command))
        return subprocess.call(command) == 0

    def configure(self, in_bps, out_bps, rtt, plr):
        """Enable traffic-shaping"""
        command = [self.exe, 'set',
                   'inbps={0:d}'.format(in_bps),
                   'outbps={0:d}'.format(out_bps),
                   'rtt={0:d}'.format(rtt),
                   'plr={0:.2f}'.format(plr)]
        logging.debug(' '.join(command))
        return subprocess.call(command) == 0
