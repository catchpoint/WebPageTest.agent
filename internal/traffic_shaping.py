# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Cross-platform support for traffic-shaping"""
import logging
import os
import platform
import re
import subprocess
import sys
import time

class TrafficShaper(object):
    """Main traffic-shaper interface"""
    def __init__(self, options, root_path):
        shaper_name = options.shaper
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
        self.shaper = None
        plat = platform.system()
        if shaper_name is None and plat == "Linux":
            shaper_name = 'netem'
        if shaper_name is not None:
            if shaper_name == 'none':
                self.shaper = NoShaper()
            elif shaper_name == 'chrome':
                self.shaper = ChromeShaper()
            elif shaper_name[:5] == 'netem':
                parts = shaper_name.split(',')
                if_out = parts[1].strip() if len(parts) > 1 else None
                if_in = None
                if options.rndis:
                    if_in = 'usb0'
                elif options.simplert:
                    if_in = 'tun0'
                elif options.vpntether or options.vpntether2:
                    if_in = 'tun0'
                self.shaper = NetEm(options=options, out_interface=if_out, in_interface=if_in)
            elif shaper_name[:6] == 'remote':
                parts = shaper_name.split(',')
                if len(parts) == 4:
                    self.shaper = RemoteDummynet(parts[1].strip(), parts[2].strip(),
                                                 parts[3].strip())
        elif options.rndis:
            self.shaper = NoShaper()
        else:
            if plat == "Windows":
                winver = float(".".join(platform.version().split('.')[:2]))
                if winver >= 6.3:
                    self.shaper = WinShaper()
            elif plat == "Linux":
                self.shaper = NetEm(options=options)
            elif plat == "Darwin":
                self.shaper = MacDummynet(root_path)

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

    def _to_int(self, s):
        return int(re.search(r'\d+', str(s)).group())

    def configure(self, job, task):
        """Enable traffic-shaping"""
        ret = False
        in_bps = 0
        if 'bwIn' in job:
            in_bps = self._to_int(job['bwIn']) * 1000
        out_bps = 0
        if 'bwOut' in job:
            out_bps = self._to_int(job['bwOut']) * 1000
        rtt = 0
        if 'latency' in job:
            rtt = self._to_int(job['latency'])
        plr = .0
        if 'plr' in job:
            plr = float(job['plr'])
        shaperLimit = 0
        if 'shaperLimit' in job:
            shaperLimit = self._to_int(job['shaperLimit'])
        if self.shaper is not None:
            # If a lighthouse test is running, force the Lighthouse 3G profile:
            # https://github.com/GoogleChrome/lighthouse/blob/master/docs/throttling.md
            # 1.6Mbps down, 750Kbps up, 150ms RTT
            if task['running_lighthouse'] and not job['lighthouse_throttle']:
                rtt = 150
                in_bps = 1600000
                out_bps = 750000
                plr = .0
                shaperLimit = 0
            logging.debug('Configuring traffic shaping: %d/%d - %d ms, %0.2f%% plr, %d tc-qdisc limit',
                          in_bps, out_bps, rtt, plr, shaperLimit)
            ret = self.shaper.configure(in_bps, out_bps, rtt, plr, shaperLimit)
            job['interface'] = self.shaper.interface
        return ret

    def set_devtools(self, devtools):
        """Configure the devtools interface for the shaper (Chrome-only)"""
        try:
            self.shaper.set_devtools(devtools)
        except Exception:
            logging.exception('Error setting shaper devtools interface')
        return

    def apply(self, target_id=None):
        """Apply the traffic-shaping for Chrome"""
        try:
            self.shaper.apply(target_id)
        except Exception:
            logging.exception('Error applying traffic shaping')

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

    def configure(self, in_bps, out_bps, rtt, plr, shaperLimit):
        """Enable traffic-shaping"""
        if in_bps > 0 or out_bps > 0 or rtt > 0 or plr > 0 or shaperLimit > 0:
            return False
        return True
    
    def set_devtools(self, devtools):
        """Stub for configuring the devtools interface"""
        return

    def apply(self, target_id):
        """Stub for applying Chrome traffic-shaping"""
        return
#
# ChromeShaper
#
class ChromeShaper(object):
    """Allow resets but fail any explicit shaping"""
    def __init__(self):
        self.interface = None
        self.devtools = None
        self.rtt = 0
        self.in_Bps = -1
        self.out_Bps = -1

    def install(self):
        """Install and configure the traffic-shaper"""
        return True

    def remove(self):
        """Uninstall traffic-shaping"""
        return True

    def reset(self):
        """Disable traffic-shaping"""
        self.rtt = 0
        self.in_Bps = -1
        self.out_Bps = -1
        self.apply()
        return True

    def configure(self, in_bps, out_bps, rtt, plr, shaperLimit):
        """Enable traffic-shaping"""
        self.rtt = rtt
        self.in_Bps = in_bps / 8
        self.out_Bps = out_bps / 8
        self.apply()
        return True

    def set_devtools(self, devtools):
        """Stub for configuring the devtools interface"""
        self.devtools = devtools

    def apply(self, target_id=None):
        """Stub for applying Chrome traffic-shaping"""
        if self.devtools is not None:
            self.devtools.send_command('Network.emulateNetworkConditions', {
                'offline': False,
                'latency': self.rtt,
                'downloadThroughput': self.in_Bps,
                'uploadThroughput': self.out_Bps
                }, wait=True, target_id=target_id)
        return

#
# winshaper
#
class WinShaper(object):
    """Windows 8.1+ traffic-shaper using winshaper"""
    def __init__(self):
        self.interface = None
        self.in_buff = 20000000
        self.out_buff = 20000000
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

    def configure(self, in_bps, out_bps, rtt, plr, shaperLimit):
        if shaperLimit > 0:
            return False # not supported

        """Enable traffic-shaping"""
        return self.shaper(['set',
                            'inbps={0:d}'.format(int(in_bps)),
                            'outbps={0:d}'.format(int(out_bps)),
                            'rtt={0:d}'.format(int(rtt)),
                            'plr={0:.2f}'.format(float(plr)),
                            'inbuff={0:d}'.format(int(self.in_buff)),
                            'outbuff={0:d}'.format(int(self.out_buff))])

    def set_devtools(self, devtools):
        """Stub for configuring the devtools interface"""
        return

    def apply(self, target_id):
        """Stub for applying Chrome traffic-shaping"""
        return

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
                          'noerror', 'mask', 'src-port', '0xffff']) and\
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

    def configure(self, in_bps, out_bps, rtt, plr, shaperLimit):
        """Enable traffic-shaping"""
        if shaperLimit > 0:
            return False # not supported
        # inbound connection
        in_kbps = int(in_bps / 1000)
        in_latency = rtt / 2
        if rtt % 2:
            in_latency += 1
        in_command = ['pipe', self.in_pipe, 'config']
        if in_kbps > 0:
            in_command.extend(['bw', '{0:d}Kbit/s'.format(int(in_kbps))])
        if in_latency >= 0:
            in_command.extend(['delay', '{0:d}ms'.format(int(in_latency))])

        # outbound connection	
        out_kbps = int(out_bps / 1000)
        out_latency = rtt / 2
        out_command = ['pipe', self.out_pipe, 'config']
        if out_kbps > 0:
            out_command.extend(['bw', '{0:d}Kbit/s'.format(int(out_kbps))])
        if out_latency >= 0:
            out_command.extend(['delay', '{0:d}ms'.format(int(out_latency))])

        # Packet loss get applied to the queues
        plr = plr / 100.0
        in_queue_command = ['queue', self.in_pipe, 'config', 'pipe', self.in_pipe, 'queue', '100']
        out_queue_command = ['queue', self.out_pipe, 'config', 'pipe', self.out_pipe,
                             'queue', '100']
        if plr > 0.0 and plr <= 1.0:
            in_queue_command.extend(['plr', '{0:.4f}'.format(float(plr))])	
            out_queue_command.extend(['plr', '{0:.4f}'.format(float(plr))])
        in_queue_command.extend(['mask', 'dst-port', '0xffff'])
        out_queue_command.extend(['mask', 'dst-port', '0xffff'])

        return self.ipfw(in_command) and\
               self.ipfw(out_command) and\
               self.ipfw(in_queue_command) and\
               self.ipfw(out_queue_command)

    def set_devtools(self, devtools):
        """Stub for configuring the devtools interface"""
        return

    def apply(self, target_id):
        """Stub for applying Chrome traffic-shaping"""
        return

#
# MacDummynet - Dummynet through pfctl
#
class MacDummynet(Dummynet):
    """Configure dummynet through pfctl and dnctl"""
    def __init__(self, root_path):
        self.interface = None
        self.in_pipe = '1'
        self.out_pipe = '2'
        self.token = None
        self.tmp_path = os.path.join(root_path, 'work', 'shaper')
        try:
            if not os.path.isdir(self.tmp_path):
                os.makedirs(self.tmp_path)
        except Exception:
            pass

    def pfctl(self, args):
        """Run a single pfctl command"""
        cmd = ['sudo', 'pfctl']
        cmd.extend(args)
        logging.debug(' '.join(cmd))
        return subprocess.call(cmd) == 0

    def dnctl(self, args):
        """Run a single dummynet command"""
        cmd = ['sudo', 'dnctl']
        cmd.extend(args)
        logging.debug(' '.join(cmd))
        return subprocess.call(cmd) == 0

    def install(self):
        """Set up the pipes"""
        # build a rules file that will only shape traffic on the default interface
        interface = 'any'
        out = subprocess.check_output(['route', '-n', 'get', 'default'], universal_newlines=True)
        if out:
            for line in out.splitlines(False):
                match = re.search(r'interface:\s+([^\s]+)', line)
                if match:
                    interface = match.group(1)
                    logging.debug('Default interface for traffic shaping: %s', interface)
                    break

        rules_file = os.path.join(self.tmp_path, 'pfctl.rules')
        with open(rules_file, 'wt') as f_out:
            f_out.write('pass quick on lo0 no state\n')
            f_out.write('dummynet in on {} all pipe 1\n'.format(interface))
            f_out.write('dummynet out on {} all pipe 2\n'.format(interface))

        return self.pfctl(['-E']) and\
               self.dnctl(['-q', 'flush']) and\
               self.dnctl(['-q', 'pipe', 'flush']) and\
               self.dnctl(['pipe', self.in_pipe, 'config', 'delay', '0ms', 'noerror']) and\
               self.dnctl(['pipe', self.out_pipe, 'config', 'delay', '0ms', 'noerror']) and\
               self.pfctl(['-f', rules_file])

    def remove(self):
        """clear the config"""
        return self.dnctl(['-q', 'flush']) and\
               self.dnctl(['-q', 'pipe', 'flush']) and\
               self.pfctl(['-f', '/etc/pf.conf']) and\
               self.pfctl(['-d'])

    def reset(self):
        """Disable traffic-shaping"""
        return self.dnctl(['pipe', self.in_pipe, 'config', 'delay', '0ms', 'noerror']) and\
               self.dnctl(['pipe', self.out_pipe, 'config', 'delay', '0ms', 'noerror'])

    def configure(self, in_bps, out_bps, rtt, plr, shaperLimit):
        """Enable traffic-shaping"""
        if shaperLimit > 0:
            return False # not supported
        # inbound connection
        in_kbps = int(in_bps / 1000)
        in_latency = rtt / 2
        if rtt % 2:
            in_latency += 1
        in_command = ['pipe', self.in_pipe, 'config']
        if in_kbps > 0:
            in_command.extend(['bw', '{0:d}Kbit/s'.format(int(in_kbps))])
        if in_latency >= 0:
            in_command.extend(['delay', '{0:d}ms'.format(int(in_latency))])

        # outbound connection
        out_kbps = int(out_bps / 1000)
        out_latency = rtt / 2
        out_command = ['pipe', self.out_pipe, 'config']
        if out_kbps > 0:
            out_command.extend(['bw', '{0:d}Kbit/s'.format(int(out_kbps))])
        if out_latency >= 0:
            out_command.extend(['delay', '{0:d}ms'.format(int(out_latency))])

        # Packet loss get applied to the queues
        plr = plr / 100.0
        if plr > 0.0 and plr <= 1.0:
            in_command.extend(['plr', '{0:.4f}'.format(float(plr))])
            out_command.extend(['plr', '{0:.4f}'.format(float(plr))])

        return self.dnctl(in_command) and\
               self.dnctl(out_command)

    def set_devtools(self, devtools):
        """Stub for configuring the devtools interface"""
        return

    def apply(self, target_id):
        """Stub for applying Chrome traffic-shaping"""
        return

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

    def set_devtools(self, devtools):
        """Stub for configuring the devtools interface"""
        return

    def apply(self, target_id):
        """Stub for applying Chrome traffic-shaping"""
        return

#
# netem
#
class NetEm(object):
    """Linux traffic-shaper using netem/tc"""
    def __init__(self, options, out_interface=None, in_interface=None):
        self.interface = out_interface
        self.in_interface = in_interface
        self.options = options

    def install(self):
        """Install and configure the traffic-shaper"""
        ret = False

        # Figure out the default interface
        try:
            if self.interface is None:
                if (sys.version_info >= (3, 0)):
                    out = subprocess.check_output(['route'], encoding='UTF-8')
                else:
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
                if self.in_interface.startswith('ifb'):
                    if self.options.dockerized:
                        subprocess.call(['sudo', 'ip', 'link', 'add', 'ifb0', 'type', 'ifb'])
                    else:
                        subprocess.call(['sudo', 'modprobe', 'ifb'])
                    subprocess.call(['sudo', 'ip', 'link', 'set', 'dev', 'ifb0', 'up'])
                    subprocess.call(['sudo', 'tc', 'qdisc', 'add', 'dev', self.interface,
                                     'ingress'])
                    subprocess.call(['sudo', 'tc', 'filter', 'add', 'dev', self.interface, 'parent',
                                     'ffff:', 'protocol', 'ip', 'u32', 'match', 'u32', '0', '0',
                                     'flowid', '1:1', 'action', 'mirred', 'egress', 'redirect',
                                     'dev', 'ifb0'])
                # Turn off tcp offload acceleration on the interfaces
                try:
                    subprocess.call(['sudo', 'ethtool', '-K', self.interface, 'tso', 'off', 'gso', 'off', 'gro', 'off'])
                    subprocess.call(['sudo', 'ethtool', '-K', self.in_interface, 'tso', 'off', 'gso', 'off', 'gro', 'off'])
                except Exception:
                    logging.exception('Error disabling tso on interfaces for traffic shaping')
                self.reset()
                ret = True
            else:
                logging.critical("Unable to identify default interface using 'route'")
        except Exception as err:
            logging.exception("Error configuring netem: %s", err.__str__())
        return ret

    def remove(self):
        """Uninstall traffic-shaping"""
        if self.interface:
            subprocess.call(['sudo', 'tc', 'qdisc', 'del', 'dev', self.interface,
                             'ingress'])
            if self.in_interface is not None and self.in_interface.startswith('ifb'):
                subprocess.call(['sudo', 'ip', 'link', 'set', 'dev', 'ifb0', 'down'])
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

    def configure(self, in_bps, out_bps, rtt, plr, shaperLimit):
        """Enable traffic-shaping"""
        ret = False
        if self.interface is not None and self.in_interface is not None:
            in_latency = rtt / 2
            if rtt % 2:
                in_latency += 1
            if self.configure_interface(self.in_interface, in_bps, in_latency, plr, shaperLimit):
                ret = self.configure_interface(self.interface, out_bps, rtt / 2, plr, shaperLimit)
        return ret

    def build_command_args(self, interface, bps, latency, plr, shaperLimit):
        args = ['sudo', 'tc', 'qdisc', 'add', 'dev', interface, 'root',
                'netem', 'delay', '{0:d}ms'.format(int(latency))]
        if bps > 0:
            kbps = int(bps / 1000)
            args.extend(['rate', '{0:d}kbit'.format(int(kbps))])
        if plr > 0:
            args.extend(['loss', '{0:.2f}%'.format(float(plr))])
        if shaperLimit > 0:
            args.extend(['limit', '{0:d}'.format(shaperLimit)])
        return args

    def configure_interface(self, interface, bps, latency, plr, shaperLimit):
        """Configure traffic-shaping for a single interface"""
        args = self.build_command_args(interface, bps, latency, plr, shaperLimit)
        logging.debug(' '.join(args))
        return subprocess.call(args) == 0

    def set_devtools(self, devtools):
        """Stub for configuring the devtools interface"""
        return

    def apply(self, target_id):
        """Stub for applying Chrome traffic-shaping"""
        return
