#!/usr/bin/python
"""
Copyright 2016 Google Inc. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
from datetime import datetime
import glob
import gzip
import logging
import os
import re

class FirefoxLogParser(object):
    """Handle parsing of firefox logs"""
    def __init__(self):
        self.dns = {}
        self.http = {'channels': {}, 'requests': {}}
        self.result = {'pageData': {}, 'requests': []}
        self.logline = re.compile(r'^(?P<timestamp>\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) \w+ - '
                                  r'\[(?P<thread>[^\]]+)\]: (?P<level>\w)/(?P<category>[^ ]+) '
                                  r'(?P<message>[^\r\n]+)')

    def process_logs(self, log_file, out_file, optimization_file=None, is_cached=False):
        """Process multiple child logs and generate a resulting requests and page data file"""
        self.__init__()
        files = glob.glob(log_file + '*')
        for path in files:
            try:
                self.process_log_file(path)
            except Exception:
                pass
        pass

    def process_log_file(self, path):
        """Process a single log file"""
        logging.debug("Processing %s", path)
        _, ext = os.path.splitext(path)
        if ext.lower() == '.gz':
            f_in = gzip.open(path, 'rb')
        else:
            f_in = open(path, 'r')
        epoch = datetime.utcfromtimestamp(0)
        for line in f_in:
            parts = self.logline.search(line)
            if parts:
                msg = {}
                timestamp = parts.groupdict().get('timestamp')
                parsed_timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
                msg['timestamp'] = (parsed_timestamp - epoch).total_seconds()
                msg['thread'] = parts.groupdict().get('thread')
                msg['level'] = parts.groupdict().get('level')
                msg['category'] = parts.groupdict().get('category')
                msg['message'] = parts.groupdict().get('message')
                if msg['category'] == 'nsHttp':
                    if msg['thread'] == 'Main Thread':
                        self.main_thread_http_entry(msg)
                    elif msg['thread'] == 'Socket Thread':
                        self.socket_thread_http_entry(msg)
                elif msg['category'] == 'nsHostResolver':
                    self.dns_entry(msg)
        f_in.close()

    def main_thread_http_entry(self, msg):
        """Process a single HTTP log line from the main thread"""
        # V/nsHttp HttpBaseChannel::Init [this=c30d000]
        if msg['message'].find('HttpBaseChannel::Init') == 0:
            match = re.search(r'^HttpBaseChannel::Init \[this=(?P<channel>[\w\d]+)]',
                              msg['message'])
            if match:
                self.http['current_channel'] = match.groupdict().get('channel')
        # D/nsHttp nsHttpChannel::Init [this=c30d000]
        elif 'current_channel' in self.http and msg['message'].find('nsHttpChannel::Init') == 0:
            del self.http['current_channel']
        # V/nsHttp uri=http://www.webpagetest.org/?bare=1
        elif 'current_channel' in self.http and  msg['message'].find('uri=') == 0:
            match = re.search(r'^uri=(?P<url>[^ ]+)', msg['message'])
            if match:
                self.http['channels'][self.http['current_channel']] = \
                        match.groupdict().get('url')
        # D/nsHttp nsHttpChannel c30d000 created nsHttpTransaction c138c00
        elif msg['message'].find('nsHttpChannel') == 0 and \
                msg['message'].find(' created nsHttpTransaction ') > -1:
            match = re.search(r'^nsHttpChannel (?P<channel>[\w\d]+) created '\
                              r'nsHttpTransaction (?P<id>[\w\d]+)', msg['message'])
            if match:
                channel = match.groupdict().get('channel')
                if channel in self.http['channels']:
                    url = self.http['channels'][channel]
                    del self.http['channels'][channel]
                    trans_id = match.groupdict().get('id')
                    self.http['requests'][trans_id] = {'url': url,
                                                       'request_headers': [],
                                                       'response_headers': [],
                                                       'status': None,
                                                       'bytes_in': 0}
        # D/nsHttp nsHttpTransaction::Init [this=c138c00 caps=21]
        elif msg['message'].find('nsHttpTransaction::Init ') == 0:
            match = re.search(r'^nsHttpTransaction::Init \[this=(?P<id>[\w\d]+)', msg['message'])
            if match:
                trans_id = match.groupdict().get('id')
                self.http['current_transaction'] = trans_id
        # D/nsHttp nsHttpTransaction c138c00 SetRequestContext c15ba00
        elif 'current_transaction' in self.http and \
                msg['message'].find('nsHttpTransaction ') == 0 and \
                msg['message'].find(' SetRequestContext  ') > -1:
            del self.http['current_transaction']
        # I/nsHttp http request [
        elif 'current_transaction' in self.http and msg['message'] == 'http request [':
            self.http['request_headers'] = self.http['current_transaction']
        elif 'request_headers' in self.http and msg['message'] == ']':
            del self.http['request_headers']
        # Individual request headers
        elif 'request_headers' in self.http and msg['message'][0:2] == '  ':
            trans_id = self.http['request_headers']
            if trans_id in self.http['requests']:
                self.http['requests'][trans_id]['request_headers'].append(msg['message'][2:])

    def socket_thread_http_entry(self, msg):
        """Process a single HTTP log line from the socket thread"""
        if msg['message'].find('nsHttpTransaction::OnTransportStatus ') == 0 and \
                msg['message'].find(' SENDING_TO ') > -1:
            match = re.search(r'^nsHttpTransaction::OnTransportStatus (?P<id>[\w\d]+) SENDING_TO ',
                              msg['message'])
            if match:
                trans_id = match.groupdict().get('id')
                if trans_id in self.http['requests']:
                    self.http['requests'][trans_id]['start'] = msg['timestamp']
        elif msg['message'].find('nsHttpTransaction::ProcessData ') == 0:
            match = re.search(r'^nsHttpTransaction::ProcessData \[this=(?P<id>[\w\d]+)',
                              msg['message'])
            if match:
                trans_id = match.groupdict().get('id')
                self.http['current_socket_transaction'] = trans_id
        elif msg['message'].find('nsHttpTransaction::HandleContent ') == 0:
            if 'current_socket_transaction' in self.http:
                del self.http['current_socket_transaction']
            match = re.search(r'^nsHttpTransaction::HandleContent \['
                              r'this=(?P<id>[\w\d]+) '
                              r'count=(?P<len>[\d]+) read=', msg['message'])
            if match:
                trans_id = match.groupdict().get('id')
                if trans_id in self.http['requests']:
                    bytes_in = int(match.groupdict().get('len'))
                    if 'first_byte' not in self.http['requests'][trans_id]:
                        self.http['requests'][trans_id]['first_byte'] = msg['timestamp']
                    if 'end' not in self.http['requests'][trans_id] or \
                            msg['timestamp'] > self.http['requests'][trans_id]['end']:
                        self.http['requests'][trans_id]['end'] = msg['timestamp']
                    self.http['requests'][trans_id]['bytes_in'] += bytes_in
        elif 'current_socket_transaction' in self.http and \
                msg['message'].find('nsHttpTransaction::ParseLine ') == 0:
            trans_id = self.http['current_socket_transaction']
            if trans_id in self.http['requests']:
                match = re.search(r'^nsHttpTransaction::ParseLine \[(?P<line>.*)\]\s*$',
                                  msg['message'])
                if match:
                    line = match.groupdict().get('line')
                    self.http['requests'][trans_id]['response_headers'].append(line)
        elif 'current_socket_transaction' in self.http and \
                msg['message'].find('Have status line ') == 0:
            trans_id = self.http['current_socket_transaction']
            if trans_id in self.http['requests']:
                match = re.search(r'^Have status line \[[^\]]*status=(?P<status>\d+)',
                                  msg['message'])
                if match:
                    status = int(match.groupdict().get('status'))
                    self.http['requests'][trans_id]['status'] = status

    def dns_entry(self, msg):
        """Process a single DNS log line"""
        if msg['message'].find('Calling getaddrinfo') > -1:
            match = re.search(r'Calling getaddrinfo for host \[(?P<host>[^\]]+)\]', msg['message'])
            if match:
                hostname = match.groupdict().get('host')
                if hostname not in self.dns:
                    self.dns[hostname] = {'start': msg['timestamp']}
        elif msg['message'].find('lookup completed for host') > -1:
            match = re.search(r'lookup completed for host \[(?P<host>[^\]]+)\]', msg['message'])
            if match:
                hostname = match.groupdict().get('host')
                if hostname in self.dns and 'end' not in self.dns[hostname]:
                    self.dns[hostname]['end'] = msg['timestamp']

def main():
    """ Main entry-point when running on the command-line"""
    import argparse
    parser = argparse.ArgumentParser(description='Chrome trace parser.',
                                     prog='trace-parser')
    parser.add_argument('-v', '--verbose', action='count',
                        help="Increase verbosity (specify multiple times for more)" \
                             ". -vvvv for full debug output.")
    parser.add_argument('-l', '--logfile', help="File name for the mozilla log.")
    parser.add_argument('-p', '--optimization', help="Input optimization results file (optional).")
    parser.add_argument('-c', '--cached', action='store_true', default=False,
                        help="Test was of a cached page.")
    parser.add_argument('-o', '--out', help="Output requests json file.")
    options, _ = parser.parse_known_args()

    # Set up logging
    log_level = logging.CRITICAL
    if options.verbose == 1:
        log_level = logging.ERROR
    elif options.verbose == 2:
        log_level = logging.WARNING
    elif options.verbose == 3:
        log_level = logging.INFO
    elif options.verbose >= 4:
        log_level = logging.DEBUG
    logging.basicConfig(
        level=log_level, format="%(asctime)s.%(msecs)03d - %(message)s", datefmt="%H:%M:%S")

    if not options.logfile or not options.out:
        parser.error("Input devtools or output file is not specified.")

    parser = FirefoxLogParser()
    parser.process_logs(options.logfile, options.out, options.optimization, options.cached)

if __name__ == '__main__':
    main()
