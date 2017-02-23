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
            else:
                self.shaper = Dummynet()
        elif plat == "Linux":
            self.shaper = NetEm()

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

#
# Dummynet
#
class Dummynet(object):
    """Dummynet support (windows only currently)"""
    def __init__(self):
        self.exe = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                "support", "dummynet")
        if platform.machine().endswith('64'):
            self.exe = os.path.join(self.exe, "x64", "ipfw.exe")
        else:
            self.exe = os.path.join(self.exe, "x86", "ipfw.exe")

    def ipfw(self, args):
        """Run a single ipfw command"""
        command = [self.exe]
        command.extend(args)
        logging.debug(' '.join(command))
        return subprocess.call(command) == 0

    def install(self):
        """Set up the pipes"""
        return self.ipfw(['-q', 'flush']) and\
               self.ipfw(['-q', 'pipe', 'flush']) and\
               self.ipfw(['pipe', '1', 'config', 'delay', '0ms', 'noerror']) and\
               self.ipfw(['pipe', '2', 'config', 'delay', '0ms', 'noerror']) and\
               self.ipfw(['queue', '1', 'config', 'pipe', '1', 'queue', '100', \
                          'noerror', 'mask', 'dst-port', '0xffff']) and\
               self.ipfw(['queue', '2', 'config', 'pipe', '2', 'queue', '100', \
                          'noerror', 'mask', 'dst-port', '0xffff']) and\
               self.ipfw(['add', 'queue', '1', 'ip', 'from', 'any', 'to', 'any', 'in']) and\
               self.ipfw(['add', 'queue', '2', 'ip', 'from', 'any', 'to', 'any', 'out']) and\
               self.ipfw(['add', '60000', 'allow', 'ip', 'from', 'any', 'to', 'any'])

    def remove(self):
        """clear the config"""
        return self.ipfw(['-q', 'flush']) and\
               self.ipfw(['-q', 'pipe', 'flush'])

    def reset(self):
        """Disable traffic-shaping"""
        return self.ipfw(['pipe', '1', 'config', 'delay', '0ms', 'noerror']) and\
               self.ipfw(['pipe', '2', 'config', 'delay', '0ms', 'noerror']) and\
               self.ipfw(['queue', '1', 'config', 'pipe', '1', 'queue', '100', \
                          'noerror', 'mask', 'dst-port', '0xffff']) and\
               self.ipfw(['queue', '2', 'config', 'pipe', '2', 'queue', '100', \
                          'noerror', 'mask', 'dst-port', '0xffff'])

    def configure(self, in_bps, out_bps, rtt, plr):
        """Enable traffic-shaping"""
        # inbound connection
        in_kbps = int(in_bps / 1000)
        in_latency = rtt / 2
        if rtt % 2:
            in_latency += 1
        in_command = ['pipe', '1', 'config']
        if in_kbps > 0:
            in_command.extend(['bw', '{0:d}Kbit/s'.format(in_kbps)])
        if in_latency >= 0:
            in_command.extend(['delay', '{0:d}ms'.format(in_latency)])

        # outbound connection
        out_kbps = int(out_bps / 1000)
        out_latency = rtt / 2
        out_command = ['pipe', '2', 'config']
        if out_kbps > 0:
            out_command.extend(['bw', '{0:d}Kbit/s'.format(out_kbps)])
        if out_latency >= 0:
            out_command.extend(['delay', '{0:d}ms'.format(out_latency)])

        # Packet loss get applied to the queues
        plr = plr / 100.0
        in_queue_command = ['queue', '1', 'config', 'pipe', '1', 'queue', '100']
        out_queue_command = ['queue', '2', 'config', 'pipe', '2', 'queue', '100']
        if plr > 0.0 and plr <= 1.0:
            in_queue_command.extend(['plr', '{0:.4f}'.format(plr)])
            out_queue_command.extend(['plr', '{0:.4f}'.format(plr)])
        in_queue_command.extend(['mask', 'dst-port', '0xffff'])
        out_queue_command.extend(['mask', 'dst-port', '0xffff'])

        return self.ipfw(in_command) and\
               self.ipfw(out_command) and\
               self.ipfw(in_queue_command) and\
               self.ipfw(out_queue_command)

#
# netem
#
class NetEm(object):
    """Linux traffic-shaper using netem/tc"""
    def __init__(self):
        pass

    def install(self):
        """Install and configure the traffic-shaper"""
        return True

    def remove(self):
        """Uninstall traffic-shaping"""
        return True

    def reset(self):
        """Disable traffic-shaping"""
        return True

    def configure(self, in_bps, out_bps, rtt, plr):
        """Enable traffic-shaping"""
        return True
