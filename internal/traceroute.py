# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Logic for running a traceroute test"""
import gzip
import logging
import os
import platform
import re
import subprocess
import sys
if (sys.version_info >= (3, 0)):
    from urllib.parse import urlparse # pylint: disable=import-error
    GZIP_TEXT = 'wt'
else:
    from urlparse import urlparse # pylint: disable=import-error
    GZIP_TEXT = 'w'

class Traceroute(object):
    """Traceroute (desktop)"""
    def __init__(self, options, job):
        self.options = options
        self.job = job

    def prepare(self, job, task):
        """Prepare the browser"""
        pass

    def launch(self, job, task):
        """Launch the browser"""
        pass

    def run_task(self, task):
        """Run an individual test"""
        if 'url' in self.job:
            results = None
            hostname = urlparse(self.job['url']).hostname
            if platform.system() == 'Windows':
                last_hop, results = self.windows_traceroute(hostname)
            else:
                last_hop, results = self.unix_traceroute(hostname)
            if last_hop > 0 and results is not None and len(results):
                out_file = os.path.join(task['dir'], task['prefix']) + '_traceroute.txt.gz'
                with gzip.open(out_file, GZIP_TEXT, 7) as f_out:
                    f_out.write('Hop,IP,ms,FQDN\n')
                    if 0 in results:
                        f_out.write('-1,{0},0,{1}\n'.format(results[0]['addr'], hostname))
                    else:
                        f_out.write('-1,,0,{0}\n'.format(hostname))
                    for hop in range(1, last_hop + 1):
                        if hop in results:
                            entry = results[hop]
                            f_out.write('{0:d},{1},{2},{3}\n'.format(hop, entry['addr'],
                                                                     entry['ms'],
                                                                     entry['hostname']))
                        else:
                            f_out.write('{0:d},,,\n'.format(hop))

    def windows_traceroute(self, hostname):
        """Run a traceroute on Windows"""
        ret = {}
        last_hop = 0
        command = ['tracert', '-h', '30', '-w', '1000', hostname]
        logging.debug(' '.join(command))
        if (sys.version_info >= (3, 0)):
            out = subprocess.check_output(command, encoding='UTF-8')
        else:
            out = subprocess.check_output(command)
        lines = out.splitlines()
        dest = re.compile(r'^Tracing route to.*\[([\d\.]+)\]')
        timeout = re.compile(r'^\s*(\d+).*Request timed out')
        address_only = re.compile(r'^\s*(\d+)\s+'
                                  r'\<?\*?(\d*)[\sms]+\<?\*?(\d*)[\sms]+\<?\*?(\d*)[\sms]+'
                                  r'([\d\.]+)')
        with_hostname = re.compile(r'^\s*(\d+)\s+'
                                   r'\<?\*?(\d*)[\sms]+\<?\*?(\d*)[\sms]+\<?\*?(\d*)[\sms]+'
                                   r'([^\s]*)\s+\[([\d\.]+)\]')
        for line in lines:
            logging.debug(line)
            try:
                fields = with_hostname.search(line)
                if fields:
                    hop = int(fields.group(1))
                    hop_time = None if not len(fields.group(2)) else int(fields.group(2))
                    next_time = None if not len(fields.group(3)) else int(fields.group(3))
                    if next_time is not None:
                        if hop_time is None or next_time < hop_time:
                            hop_time = next_time
                    next_time = None if not len(fields.group(4)) else int(fields.group(4))
                    if next_time is not None:
                        if hop_time is None or next_time < hop_time:
                            hop_time = next_time
                    report_time = '{0:d}'.format(hop_time) if hop_time is not None else ''
                    ret[hop] = {'ms': report_time, 'hostname': fields.group(5),
                                'addr': fields.group(6)}
                    if hop > last_hop:
                        last_hop = hop
                else:
                    fields = address_only.search(line)
                    if fields:
                        hop = int(fields.group(1))
                        hop_time = None if not len(fields.group(2)) else int(fields.group(2))
                        next_time = None if not len(fields.group(3)) else int(fields.group(3))
                        if next_time is not None:
                            if hop_time is None or next_time < hop_time:
                                hop_time = next_time
                        next_time = None if not len(fields.group(4)) else int(fields.group(4))
                        if next_time is not None:
                            if hop_time is None or next_time < hop_time:
                                hop_time = next_time
                        report_time = '{0:d}'.format(hop_time) if hop_time is not None else ''
                        ret[hop] = {'ms': report_time, 'hostname': '', 'addr': fields.group(5)}
                        if hop > last_hop:
                            last_hop = hop
                    else:
                        fields = timeout.search(line)
                        if fields:
                            hop = int(fields.group(1))
                            ret[hop] = {'ms': '', 'hostname': '', 'addr': ''}
                        else:
                            fields = dest.search(line)
                            if fields:
                                ret[0] = {'ms': '', 'hostname': hostname, 'addr': fields.group(1)}
            except Exception:
                logging.exception('Error processing traceroute')
        return last_hop, ret

    def unix_traceroute(self, hostname):
        """Run a traceroute on a system that supports bsd traceroute"""
        ret = {}
        last_hop = 0
        ret = {}
        last_hop = 0
        command = ['traceroute', '-m', '30', '-w', '1', hostname]
        logging.debug(' '.join(command))
        if (sys.version_info >= (3, 0)):
            out = subprocess.check_output(command, encoding='UTF-8')
        else:
            out = subprocess.check_output(command)
        lines = out.splitlines()
        dest = re.compile(r'^traceroute to [^\(]+\(([\d\.]+)\)')
        timeout = re.compile(r'^\s*(\d+)\s+\*\s+\*\s+\*')
        success = re.compile(r'^\s*(\d+)\s+([^\s]+)\s+\(([\d\.]+)\)\s+'
                             r'\*?([\d\.]*)[\sms]+\*?([\d\.]*)[\sms]+\*?([\d\.]*)[\sms]+')
        for line in lines:
            logging.debug(line)
            try:
                fields = success.search(line)
                if fields:
                    hop = int(fields.group(1))
                    hop_time = None if not len(fields.group(4)) else float(fields.group(4))
                    next_time = None if not len(fields.group(5)) else float(fields.group(5))
                    if next_time is not None:
                        if hop_time is None or next_time < hop_time:
                            hop_time = next_time
                    next_time = None if not len(fields.group(6)) else float(fields.group(6))
                    if next_time is not None:
                        if hop_time is None or next_time < hop_time:
                            hop_time = next_time
                    report_time = '{0:0.3f}'.format(hop_time) if hop_time is not None else ''
                    ret[hop] = {'ms': report_time, 'hostname': fields.group(2),
                                'addr': fields.group(3)}
                    if hop > last_hop:
                        last_hop = hop
                else:
                    fields = timeout.search(line)
                    if fields:
                        hop = int(fields.group(1))
                        ret[hop] = {'ms': '', 'hostname': '', 'addr': ''}
                    else:
                        fields = dest.search(line)
                        if fields:
                            ret[0] = {'ms': '', 'hostname': hostname, 'addr': fields.group(1)}
            except Exception:
                logging.exception('Error processing traceroute')
        return last_hop, ret

    def run_lighthouse_test(self, task):
        """Stub for lighthouse test"""
        pass

    def stop(self, job, task):
        """Stop the browser"""
        pass

    def clear_profile(self, task):
        """Stub for clearing profile"""
        pass

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        pass

    def on_stop_recording(self, _):
        """Notification that recording is done"""
        pass

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        pass
