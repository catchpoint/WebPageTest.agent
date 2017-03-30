# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Cross-platform support for traffic-shaping"""
import logging
import os
import platform
import re
import subprocess
import time

class TrafficShaper(object):
    """Main traffic-shaper interface"""
    def __init__(self, options):
        shaper_name = options.shaper
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
        self.shaper = None
        if shaper_name is not None:
            if shaper_name == 'none':
                self.shaper = NoShaper()
            elif shaper_name[:5] == 'netem':
                parts = shaper_name.split(',')
                if_out = parts[1].strip() if len(parts) > 1 else None
                if_in = 'usb0' if options.rndis else None
                self.shaper = NetEm(out_interface=if_out, in_interface=if_in)
            elif shaper_name[:6] == 'remote':
                parts = shaper_name.split(',')
                if len(parts) == 4:
                    self.shaper = RemoteDummynet(parts[1].strip(), parts[2].strip(),
                                                 parts[3].strip())
        elif options.rndis:
            self.shaper = NoShaper()
        else:
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
            logging.debug('Resetting traffic shaping')
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
            logging.debug('Configuring traffic shaping: %d/%d - %d ms, %0.2f%% plr',
                          in_bps, out_bps, rtt, plr)
            ret = self.shaper.configure(in_bps, out_bps, rtt, plr)
            job['interface'] = self.shaper.interface
        return ret


#
# NoShaper
#
class NoShaper(object):
    """Allow resets but fail any explicit shaping"""
    def __init__(self):
        self.interface = None

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
        if in_bps > 0 or out_bps > 0 or rtt > 0 or plr > 0:
            return False
        return True

#
# winshaper
#
class WinShaper(object):
    """Windows 8.1+ traffic-shaper using winshaper"""
    def __init__(self):
        self.interface = None
        self.exe = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                "support", "winshaper", "shaper.exe")

    def shaper(self, args):
        """Run a shaper command with elevated permissions"""
        from .os_util import run_elevated
        return run_elevated(self.exe, ' '.join(args)) == 0

    def install(self):
        """Install and configure the traffic-shaper"""
        return self.shaper(['install'])

    def remove(self):
        """Uninstall traffic-shaping"""
        return self.shaper(['remove'])

    def reset(self):
        """Disable traffic-shaping"""
        return self.shaper(['reset'])

    def configure(self, in_bps, out_bps, rtt, plr):
        """Enable traffic-shaping"""
        return self.shaper(['set',
                            'inbps={0:d}'.format(in_bps),
                            'outbps={0:d}'.format(out_bps),
                            'rtt={0:d}'.format(rtt),
                            'plr={0:.2f}'.format(plr)])

#
# Dummynet
#
class Dummynet(object):
    """Dummynet support (windows only currently)"""
    def __init__(self):
        self.interface = None
        self.in_pipe = '1'
        self.out_pipe = '2'
        self.exe = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                "support", "dummynet")
        if platform.machine().endswith('64'):
            self.exe = os.path.join(self.exe, "x64", "ipfw.exe")
        else:
            self.exe = os.path.join(self.exe, "x86", "ipfw.exe")

    def ipfw(self, args):
        """Run a single ipfw command"""
        from .os_util import run_elevated
        cmd = ' '.join(args)
        logging.debug('ipfw ' + cmd)
        return run_elevated(self.exe, cmd) == 0

    def install(self):
        """Set up the pipes"""
        return self.ipfw(['-q', 'flush']) and\
               self.ipfw(['-q', 'pipe', 'flush']) and\
               self.ipfw(['pipe', self.in_pipe, 'config', 'delay', '0ms', 'noerror']) and\
               self.ipfw(['pipe', self.out_pipe, 'config', 'delay', '0ms', 'noerror']) and\
               self.ipfw(['queue', self.in_pipe, 'config', 'pipe', self.in_pipe, 'queue', '100', \
                          'noerror', 'mask', 'dst-port', '0xffff']) and\
               self.ipfw(['queue', self.out_pipe, 'config', 'pipe', self.out_pipe, 'queue', '100', \
                          'noerror', 'mask', 'dst-port', '0xffff']) and\
               self.ipfw(['add', 'queue', self.in_pipe, 'ip', 'from', 'any', 'to', 'any',
                          'in']) and\
               self.ipfw(['add', 'queue', self.out_pipe, 'ip', 'from', 'any', 'to', 'any',
                          'out']) and\
               self.ipfw(['add', '60000', 'allow', 'ip', 'from', 'any', 'to', 'any'])

    def remove(self):
        """clear the config"""
        return self.ipfw(['-q', 'flush']) and\
               self.ipfw(['-q', 'pipe', 'flush'])

    def reset(self):
        """Disable traffic-shaping"""
        return self.ipfw(['pipe', self.in_pipe, 'config', 'delay', '0ms', 'noerror']) and\
               self.ipfw(['pipe', self.out_pipe, 'config', 'delay', '0ms', 'noerror']) and\
               self.ipfw(['queue', self.in_pipe, 'config', 'pipe', self.in_pipe, 'queue', '100', \
                          'noerror', 'mask', 'dst-port', '0xffff']) and\
               self.ipfw(['queue', self.out_pipe, 'config', 'pipe', self.out_pipe, 'queue', '100', \
                          'noerror', 'mask', 'dst-port', '0xffff'])

    def configure(self, in_bps, out_bps, rtt, plr):
        """Enable traffic-shaping"""
        # inbound connection
        in_kbps = int(in_bps / 1000)
        in_latency = rtt / 2
        if rtt % 2:
            in_latency += 1
        in_command = ['pipe', self.in_pipe, 'config']
        if in_kbps > 0:
            in_command.extend(['bw', '{0:d}Kbit/s'.format(in_kbps)])
        if in_latency >= 0:
            in_command.extend(['delay', '{0:d}ms'.format(in_latency)])

        # outbound connection
        out_kbps = int(out_bps / 1000)
        out_latency = rtt / 2
        out_command = ['pipe', self.out_pipe, 'config']
        if out_kbps > 0:
            out_command.extend(['bw', '{0:d}Kbit/s'.format(out_kbps)])
        if out_latency >= 0:
            out_command.extend(['delay', '{0:d}ms'.format(out_latency)])

        # Packet loss get applied to the queues
        plr = plr / 100.0
        in_queue_command = ['queue', self.in_pipe, 'config', 'pipe', self.in_pipe, 'queue', '100']
        out_queue_command = ['queue', self.out_pipe, 'config', 'pipe', self.out_pipe,
                             'queue', '100']
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
# RemoteDummynet - Remote PC running dummynet with pre-configured pipes
#
class RemoteDummynet(Dummynet):
    """Allow resets but fail any explicit shaping"""
    def __init__(self, server, in_pipe, out_pipe):
        Dummynet.__init__(self)
        self.server = server
        self.in_pipe = in_pipe
        self.out_pipe = out_pipe
        self.use_shell = bool(platform.system() == "Windows")

    def ipfw(self, args):
        """Run a single command on the remote server"""
        success = False
        cmd = ['ssh', '-o', 'StrictHostKeyChecking=no',
               'root@{0}'.format(self.server), 'ipfw ' + ' '.join(args)]
        logging.debug(' '.join(cmd))
        count = 0
        while not success and count < 30:
            count += 1
            try:
                subprocess.check_call(cmd, shell=self.use_shell)
                success = True
            except Exception:
                time.sleep(0.2)
        return success

    def install(self):
        """Install and configure the traffic-shaper"""
        return self.ipfw(['pipe', 'show', self.in_pipe])

    def remove(self):
        """Uninstall traffic-shaping"""
        return True

#
# netem
#
class NetEm(object):
    """Linux traffic-shaper using netem/tc"""
    def __init__(self, out_interface=None, in_interface=None):
        self.interface = out_interface
        self.in_interface = in_interface

    def install(self):
        """Install and configure the traffic-shaper"""
        ret = False

        # Figure out the default interface
        try:
            if self.interface is None:
                out = subprocess.check_output(['route'])
                routes = out.splitlines()
                match = re.compile(r'^([^\s]+)\s+[^\s]+\s+[^\s]+\s+[^\s]+\s+'\
                                r'[^\s]+\s+[^\s]+\s+[^\s]+\s+([^\s]+)')
                for route in routes:
                    fields = re.search(match, route)
                    if fields:
                        destination = fields.group(1)
                        if destination == 'default':
                            self.interface = fields.group(2)
                            logging.debug("Default interface: %s", self.interface)
                            break

            if self.interface:
                if self.in_interface is None:
                    self.in_interface = 'ifb0'
                # Set up the ifb interface so inbound traffic can be shaped
                subprocess.call(['sudo', 'modprobe', 'ifb'])
                subprocess.call(['sudo', 'ip', 'link', 'set', 'dev', 'ifb0', 'up'])
                subprocess.call(['sudo', 'tc', 'qdisc', 'add', 'dev', self.interface,
                                 'ingress'])
                subprocess.call(['sudo', 'tc', 'filter', 'add', 'dev', self.interface, 'parent',
                                 'ffff:', 'protocol', 'ip', 'u32', 'match', 'u32', '0', '0',
                                 'flowid', '1:1', 'action', 'mirred', 'egress', 'redirect',
                                 'dev', 'ifb0'])
                self.reset()
                ret = True
            else:
                logging.critical("Unable to identify default interface using 'route'")
        except Exception as err:
            logging.debug("Error configuring netem: %s", err.__str__())
        return ret

    def remove(self):
        """Uninstall traffic-shaping"""
        return True

    def reset(self):
        """Disable traffic-shaping"""
        ret = False
        if self.interface is not None and self.in_interface is not None:
            ret = subprocess.call(['sudo', 'tc', 'qdisc', 'del', 'dev', self.in_interface,
                                   'root']) == 0 and\
                  subprocess.call(['sudo', 'tc', 'qdisc', 'del', 'dev', self.interface,
                                   'root']) == 0
        return ret

    def configure(self, in_bps, out_bps, rtt, plr):
        """Enable traffic-shaping"""
        ret = False
        if self.interface is not None and self.in_interface is not None:
            in_latency = rtt / 2
            if rtt % 2:
                in_latency += 1
            if self.configure_interface(self.in_interface, in_bps, in_latency, plr):
                ret = self.configure_interface(self.interface, out_bps, rtt / 2, plr)
        return ret

    def configure_interface(self, interface, bps, latency, plr):
        """Configure traffic-shaping for a single interface"""
        ret = False
        args = ['sudo', 'tc', 'qdisc', 'add', 'dev', interface, 'root', 'handle',
                '1:0', 'netem', 'delay', '{0:d}ms'.format(latency)]
        if plr > 0:
            args.extend(['loss', '{0:.2f}%'.format(plr)])
        logging.debug(' '.join(args))
        ret = subprocess.call(args) == 0
        if ret and bps > 0:
            kbps = int(bps / 1000)
            args = ['sudo', 'tc', 'qdisc', 'add', 'dev', interface, 'parent', '1:1',
                    'handle', '10:', 'tbf', 'rate', '{0:d}kbit'.format(kbps),
                    'buffer', '150000', 'limit', '150000']
            logging.debug(' '.join(args))
            ret = subprocess.call(args) == 0
        return ret
