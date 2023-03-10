#!/usr/bin/env python
"""
Copyright 2019 WebPageTest LLC.
Copyright 2016 Google Inc.
Copyright 2020 Catchpoint Systems Inc.
Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
found in the LICENSE.md file.
"""
import glob
import gzip
import logging
import os
import re
import sys
if (sys.version_info >= (3, 0)):
    from time import monotonic
    from urllib.parse import urlsplit # pylint: disable=import-error
    GZIP_TEXT = 'wt'
    GZIP_READ_TEXT = 'rt'
else:
    from monotonic import monotonic
    from urlparse import urlsplit # pylint: disable=import-error
    GZIP_TEXT = 'w'
    GZIP_READ_TEXT = 'r'
try:
    import ujson as json
except BaseException:
    import json

class FirefoxLogParser(object):
    """Handle parsing of firefox logs"""
    def __init__(self):
        self.start_time = None
        self.start_day = None
        self.unique_id = 0
        self.int_map = {}
        for val in range(0, 100):
            self.int_map['{0:02d}'.format(val)] = float(val)
        self.dns = {}
        self.http = {'channels': {}, 'requests': {}, 'connections': {}, 'sockets': {}, 'streams': {}}
        self.logline = re.compile(r'^(?P<timestamp>\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) \w+ - '
                                  r'\[(?P<thread>[^\]]+)\]: (?P<level>\w)/(?P<category>[^ ]+) '
                                  r'(?P<message>[^\r\n]+)')

    def set_start_time(self, timestamp):
        """Store the start time"""
        self.start_day = int(timestamp[8:10])
        hour = int(timestamp[11:13])
        minute = int(timestamp[14:16])
        second = int(timestamp[17:19])
        usecond = float(int(timestamp[21:])) / 1000000
        self.start_time = float(hour * 3600 + minute * 60 + second) + usecond

    def process_logs(self, log_file, start_time):
        """Process multiple child logs and generate a resulting requests and page data file"""
        self.__init__()
        files = sorted(glob.glob(log_file + '*'))
        self.set_start_time(start_time)
        for path in files:
            try:
                self.process_log_file(path)
            except Exception:
                logging.exception('Error processing log file')
        return self.finish_processing()

    def finish_processing(self):
        """Do the post-parse processing"""
        logging.debug('Processing network requests from moz log')
        # Pass the HTTP/2 stream information to the requests
        for stream_key in self.http['streams']:
            stream = self.http['streams'][stream_key]
            if 'request_id' in stream and stream['request_id'] in self.http['requests']:
                request = self.http['requests'][stream['request_id']]
                if 'stream_id' in stream:
                    request['http2_stream_id'] = stream['stream_id']
                    if 'parent_stream_id' in stream:
                        request['http2_stream_dependency'] = stream['parent_stream_id']
                    if 'weight' in stream:
                        request['http2_stream_weight'] = stream['weight']
        requests = []
        # Pull out the network requests and sort them
        for request_id in self.http['requests']:
            request = self.http['requests'][request_id]
            if 'url' in request and request['url'][0:22] != 'http://127.0.0.1:8888/'\
                    and 'start' in request:
                request['id'] = request_id
                requests.append(dict(request))
        if len(requests):
            requests.sort(key=lambda x: x['start'] if 'start' in x else 0)
        # Attach the DNS lookups to the first request on each domain
        for domain in self.dns:
            if 'claimed' not in self.dns[domain]:
                for request in requests:
                    host = urlsplit(request['url']).hostname
                    if host == domain:
                        self.dns[domain]['claimed'] = True
                        if 'start' in self.dns[domain]:
                            request['dns_start'] = self.dns[domain]['start']
                        if 'end' in self.dns[domain]:
                            request['dns_end'] = self.dns[domain]['end']
                        break
        # Attach the socket connect events to the first request on each connection
        for request in requests:
            if 'connection' in request and request['connection'] in self.http['connections']:
                connection = self.http['connections'][request['connection']]
                if 'socket' in connection and connection['socket'] in self.http['sockets']:
                    socket = self.http['sockets'][connection['socket']]
                    if 'claimed' not in socket:
                        socket['claimed'] = True
                        if 'start' in socket:
                            request['connect_start'] = socket['start']
                        if 'end' in socket:
                            request['connect_end'] = socket['end']
                        if 'ssl_start' in connection and 'ssl_end' in connection:
                            request['ssl_start'] = connection['ssl_start']
                            request['ssl_end'] = connection['ssl_end']
        return requests

    def process_log_file(self, path):
        """Process a single log file"""
        logging.debug("Processing %s", path)
        start = monotonic()
        _, ext = os.path.splitext(path)
        line_count = 0
        if ext.lower() == '.gz':
            f_in = gzip.open(path, GZIP_READ_TEXT)
        else:
            f_in = open(path, 'r')
        for line in f_in:
            line_count += 1
            line = line.rstrip("\r\n")
            self.process_log_line(line)
        f_in.close()
        elapsed = monotonic() - start
        logging.debug("%0.3f s to process %s (%d lines)", elapsed, path, line_count)

    def process_log_line(self, line):
        """Process a single log line"""
        int_map = self.int_map
        timestamp = line[0:26]
        if len(timestamp) >= 26:
            msg = {}
            try:
                # %Y-%m-%d %H:%M:%S.%f - 2017-06-27 13:46:10.048844
                day = int_map[timestamp[8:10]]
                hour = int_map[timestamp[11:13]]
                minute = int_map[timestamp[14:16]]
                second = int_map[timestamp[17:19]]
                usecond = int_map[timestamp[20:22]] * 10000 + \
                            int_map[timestamp[22:24]] * 100 + int_map[timestamp[24:26]]
                event_time = (hour * 3600.0 + minute * 60.0 + second) + (usecond / 1000000)
                if day == self.start_day:
                    elapsed = event_time - self.start_time
                else:
                    elapsed = event_time + (float(3600 * 24) - self.start_time)
                msg['timestamp'] = elapsed
                if msg['timestamp'] >= 0:
                    offset = line.find(']: ', 32)
                    if offset >= 0:
                        try:
                            thread = line[34:offset]
                            separator = thread.find(':')
                            if separator >= 0:
                                thread = thread[separator + 1:].strip()
                            msg['thread'] = thread
                            msg['level'] = line[offset + 3:offset + 4]
                            msg_start = line.find(' ', offset + 5)
                            if msg_start >= 0:
                                msg['category'] = line[offset + 5:msg_start]
                                msg['message'] = line[msg_start + 1:]
                                if msg['category'] == 'nsHttp':
                                    if msg['thread'] == 'Main Thread':
                                        self.main_thread_http_entry(msg)
                                    elif msg['thread'] == 'Socket Thread':
                                        self.socket_thread_http_entry(msg)
                                elif msg['category'] == 'nsSocketTransport':
                                    self.socket_transport_entry(msg)
                                elif msg['category'] == 'nsHostResolver':
                                    self.dns_entry(msg)
                        except Exception:
                            logging.exception('Error processing log line')
            except Exception:
                pass

    def main_thread_http_entry(self, msg):
        """Process a single HTTP log line from the main thread"""
        # V/nsHttp HttpBaseChannel::Init [this=c30d000]
        if msg['message'].startswith('HttpBaseChannel::Init'):
            match = re.search(r'^HttpBaseChannel::Init \[this=(?P<channel>[\w\d]+)]',
                              msg['message'])
            if match:
                self.http['current_channel'] = match.groupdict().get('channel')
        # D/nsHttp nsHttpChannel::Init [this=c30d000]
        elif 'current_channel' in self.http and msg['message'].startswith('nsHttpChannel::Init'):
            del self.http['current_channel']
        # V/nsHttp uri=http://www.webpagetest.org/?bare=1
        elif 'current_channel' in self.http and  msg['message'].startswith('uri='):
            match = re.search(r'^uri=(?P<url>[^ \r\n]+)', msg['message'])
            if match:
                self.http['channels'][self.http['current_channel']] = {'url': match.groupdict().get('url'),
                                                                        'priority': 0}
        # request priority
        #  D/nsHttp nsHttpChannel::SetPriority 14aba4a00 p=-1
        elif msg['message'].startswith('nsHttpChannel::SetPriority '):
            match = re.search(r'^nsHttpChannel::SetPriority '
                            r'(?P<channel>[\w\d]+) p=(?P<priority>[\w\d\-+]+)',
                            msg['message'])
            if match:
                channel = match.groupdict().get('channel')
                self.http['channels'][channel]['priority'] = \
                        match.groupdict().get('priority')
                if self.http['channels'][channel].get('trans_id'):
                    trans_id = self.http['channels'][channel]['trans_id']
                    # existing trans so let's look at the requests to set priority
                    if trans_id in self.http['requests']:
                        self.http['requests'][trans_id]['priority'] = match.groupdict().get('priority')

        # V/nsHttp Creating nsHttpTransaction @0x7f88bb130400
        elif msg['message'].startswith('Creating nsHttpTransaction '):
            match = re.search(r'^Creating nsHttpTransaction @(?P<id>[\w\d]+)', msg['message'])
            if match:
                self.http['creating_trans_id'] = match.groupdict().get('id')
        # D/nsHttp nsHttpChannel c30d000 created nsHttpTransaction c138c00
        elif msg['message'].startswith('nsHttpChannel') and \
                msg['message'].find(' created nsHttpTransaction ') > -1:
            match = re.search(r'^nsHttpChannel (?P<channel>[\w\d]+) created '\
                              r'nsHttpTransaction (?P<id>[\w\d]+)', msg['message'])
            if match:
                channel = match.groupdict().get('channel')
                if channel in self.http['channels']:
                    url = self.http['channels'][channel]['url']
                    priority = self.http['channels'][channel]['priority']
                    if 'creating_trans_id' in self.http:
                        trans_id = self.http['creating_trans_id']
                        del self.http['creating_trans_id']
                    else:
                        trans_id = match.groupdict().get('id')
                    # connect the trans id with the channel so if priority changes we can track
                    self.http['channels'][channel]['trans_id'] = trans_id
                    # If there is already an existing transaction with the same ID,
                    # move it to a unique ID.
                    if trans_id in self.http['requests']:
                        tmp_request = self.http['requests'][trans_id]
                        del self.http['requests'][trans_id]
                        self.unique_id += 1
                        new_id = '{0}.{1:d}'.format(trans_id, self.unique_id)
                        self.http['requests'][new_id] = tmp_request
                    self.http['requests'][trans_id] = {'url': url,
                                                       'request_headers': [],
                                                       'response_headers': [],
                                                       'status': None,
                                                       'bytes_in': 0,
                                                       'priority': priority,
                                                       'chunks': []}
        # D/nsHttp nsHttpTransaction::Init [this=c138c00 caps=21]
        elif msg['message'].startswith('nsHttpTransaction::Init '):
            match = re.search(r'^nsHttpTransaction::Init \[this=(?P<id>[\w\d]+)', msg['message'])
            if match:
                trans_id = match.groupdict().get('id')
                self.http['current_transaction'] = trans_id
        # D/nsHttp nsHttpTransaction c138c00 SetRequestContext c15ba00
        elif 'current_transaction' in self.http and \
                msg['message'].startswith('nsHttpTransaction ') and \
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
        # V/nsHttp nsHttpConnection::Activate [this=ed6c450 trans=143f3c00 caps=21]
        if msg['message'].startswith('nsHttpConnection::Activate '):
            match = re.search(r'^nsHttpConnection::Activate \['
                              r'this=(?P<connection>[\w\d]+) '
                              r'trans=(?P<id>[\w\d]+)', msg['message'])
            if match:
                connection = match.groupdict().get('connection')
                trans_id = match.groupdict().get('id')
                if trans_id in self.http['requests']:
                    self.http['requests'][trans_id]['connection'] = connection
        # V/nsHttp nsHttpConnection::Init this=ed6c450
        elif msg['message'].startswith('nsHttpConnection::Init ') and \
                'current_socket' in self.http:
            match = re.search(r'^nsHttpConnection::Init '
                              r'this=(?P<connection>[\w\d]+)', msg['message'])
            if match:
                connection = match.groupdict().get('connection')
                socket = self.http['current_socket']
                self.http['connections'][connection] = {'socket': socket}
            del self.http['current_socket']
        elif msg['message'].startswith('nsHttpConnection::SetupSSL '):
            match = re.search(r'^nsHttpConnection::SetupSSL (?P<connection>[\w\d]+)',
                              msg['message'])
            if match:
                connection = match.groupdict().get('connection')
                if connection in self.http['connections']:
                    if 'ssl_start' not in self.http['connections'][connection]:
                        self.http['connections'][connection]['ssl_start'] = msg['timestamp']
        elif msg['message'].startswith('nsHttpConnection::HandshakeDone '):
            match = re.search(r'^nsHttpConnection::HandshakeDone \[this=(?P<connection>[\w\d]+)\]',
                              msg['message'])
            if match:
                connection = match.groupdict().get('connection')
                if connection in self.http['connections']:
                    if 'ssl_start' in self.http['connections'][connection]:
                        self.http['connections'][connection]['ssl_end'] = msg['timestamp']
        elif msg['message'].startswith('nsHttpTransaction::OnTransportStatus ') and \
                msg['message'].find(' SENDING_TO ') > -1:
            match = re.search(r'^nsHttpTransaction::OnTransportStatus (?P<id>[\w\d]+) SENDING_TO ',
                              msg['message'])
            if match:
                trans_id = match.groupdict().get('id')
                if trans_id in self.http['requests'] and \
                        'start' not in self.http['requests'][trans_id]:
                    self.http['requests'][trans_id]['start'] = msg['timestamp']
        elif msg['message'].startswith('nsHttpTransaction::OnSocketStatus ') and \
                msg['message'].find(' status=804b0005 progress=') > -1:
            match = re.search(r'^nsHttpTransaction::OnSocketStatus '\
                              r'\[this=(?P<id>[\w\d]+) status=804b0005 progress=(?P<bytes>[\d+]+)',
                              msg['message'])
            if match:
                trans_id = match.groupdict().get('id')
                byte_count = int(match.groupdict().get('bytes'))
                if byte_count > 0 and trans_id in self.http['requests'] and \
                        'start' not in self.http['requests'][trans_id]:
                    self.http['requests'][trans_id]['start'] = msg['timestamp']
        elif msg['message'].startswith('nsHttpTransaction::ProcessData '):
            match = re.search(r'^nsHttpTransaction::ProcessData \[this=(?P<id>[\w\d]+)',
                              msg['message'])
            if match:
                trans_id = match.groupdict().get('id')
                self.http['current_socket_transaction'] = trans_id
        elif msg['message'].startswith('nsHttpTransaction::HandleContent '):
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
                    self.http['requests'][trans_id]['chunks'].append(\
                        {'ts': msg['timestamp'], 'bytes': bytes_in})
        elif msg['message'].startswith('Http2Stream::Http2Stream '):
            match = re.search(r'^Http2Stream::Http2Stream '
                              r'(?P<stream>[\w\d]+) '
                              r'trans=(?P<id>[\w\d]+) ', msg['message'])
            if match:
                stream = match.groupdict().get('stream')
                trans_id = match.groupdict().get('id')
                if stream not in self.http['streams']:
                    self.http['streams'][stream] = {}
                if 'trans_id' not in self.http['streams'][stream]:
                    self.http['streams'][stream]['request_id'] = trans_id
        elif msg['message'].startswith('Http2Session::RegisterStreamID '):
            match = re.search(r'^Http2Session::RegisterStreamID '
                              r'session=[\w\d]+ '
                              r'stream=(?P<stream>[\w\d]+) '
                              r'id=(?P<id>0x[\w\d]+) ', msg['message'])
            if match:
                stream = match.groupdict().get('stream')
                stream_id = int(match.groupdict().get('id'), 16)
                if stream in self.http['streams']:
                    self.http['streams'][stream]['stream_id'] = stream_id
        elif msg['message'].startswith('Http2Stream::UpdatePriorityDependency '):
            match = re.search(r'^Http2Stream::UpdatePriorityDependency '
                              r'(?P<stream>[\w\d]+) '
                              r'depends on stream (?P<parent>0x[\w\d]+) ', msg['message'])
            if match:
                stream = match.groupdict().get('stream')
                parent_id = int(match.groupdict().get('parent'), 16)
                if stream in self.http['streams']:
                    self.http['streams'][stream]['parent_stream_id'] = parent_id
        elif msg['message'].startswith('Http2Stream '):
            match = re.search(r'^Http2Stream '
                              r'(?P<stream>[\w\d]+) '
                              r'Generating [\d]+ bytes of HEADERS for '
                              r'stream (?P<id>0x[\w\d]+) '
                              r'with priority weight (?P<weight>[\d]+) '
                              r'dep (?P<parent>0x[\w\d]+) ', msg['message'])
            if match:
                stream = match.groupdict().get('stream')
                stream_id = int(match.groupdict().get('id'), 16)
                weight = int(match.groupdict().get('weight'), 10)
                parent_id = int(match.groupdict().get('parent'), 16)
                if stream in self.http['streams']:
                    self.http['streams'][stream]['stream_id'] = stream_id
                    self.http['streams'][stream]['weight'] = weight
                    self.http['streams'][stream]['parent_stream_id'] = parent_id
        elif 'current_socket_transaction' in self.http and \
                msg['message'].startswith('nsHttpTransaction::ParseLine '):
            trans_id = self.http['current_socket_transaction']
            if trans_id in self.http['requests']:
                if trans_id in self.http['requests']:
                    if 'first_byte' not in self.http['requests'][trans_id]:
                        self.http['requests'][trans_id]['first_byte'] = msg['timestamp']
                    if 'end' not in self.http['requests'][trans_id] or \
                            msg['timestamp'] > self.http['requests'][trans_id]['end']:
                        self.http['requests'][trans_id]['end'] = msg['timestamp']
                match = re.search(r'^nsHttpTransaction::ParseLine \[(?P<line>.*)\]\s*$',
                                  msg['message'])
                if match:
                    line = match.groupdict().get('line')
                    self.http['requests'][trans_id]['response_headers'].append(line)
        elif 'current_socket_transaction' in self.http and \
                msg['message'].startswith('Have status line '):
            trans_id = self.http['current_socket_transaction']
            if trans_id in self.http['requests']:
                if trans_id in self.http['requests']:
                    if 'first_byte' not in self.http['requests'][trans_id]:
                        self.http['requests'][trans_id]['first_byte'] = msg['timestamp']
                    if 'end' not in self.http['requests'][trans_id] or \
                            msg['timestamp'] > self.http['requests'][trans_id]['end']:
                        self.http['requests'][trans_id]['end'] = msg['timestamp']
                match = re.search(r'^Have status line \[[^\]]*status=(?P<status>\d+)',
                                  msg['message'])
                if match:
                    status = int(match.groupdict().get('status'))
                    self.http['requests'][trans_id]['status'] = status

    def socket_transport_entry(self, msg):
        """Process a single socket transport line"""
        # nsSocketTransport::Init [this=143f4000 host=www.webpagetest.org:80 origin=www.webpagetest.org:80 proxy=:0]
        if msg['message'].startswith('nsSocketTransport::Init '):
            match = re.search(r'^nsSocketTransport::Init \['
                              r'this=(?P<socket>[\w\d]+) '
                              r'host=(?P<host>[^ :]+):(?P<port>\d+)', msg['message'])
            if match:
                socket = match.groupdict().get('socket')
                host = match.groupdict().get('host')
                port = match.groupdict().get('port')
                self.http['sockets'][socket] = {'host': host, 'port': port}
        # nsSocketTransport::SendStatus [this=143f4000 status=804b0007]
        elif msg['message'].startswith('nsSocketTransport::SendStatus '):
            match = re.search(r'^nsSocketTransport::SendStatus \['
                              r'this=(?P<socket>[\w\d]+) '
                              r'status=(?P<status>[\w\d]+)', msg['message'])
            if match:
                socket = match.groupdict().get('socket')
                status = match.groupdict().get('status')
                if status == '804b0007':
                    if socket not in self.http['sockets']:
                        self.http['sockets'][socket] = {}
                    if 'start' not in self.http['sockets'][socket]:
                        self.http['sockets'][socket]['start'] = msg['timestamp']
        # nsSocketTransport::OnSocketReady [this=143f4000 outFlags=2]
        elif msg['message'].startswith('nsSocketTransport::OnSocketReady '):
            match = re.search(r'^nsSocketTransport::OnSocketReady \['
                              r'this=(?P<socket>[\w\d]+) ', msg['message'])
            if match:
                socket = match.groupdict().get('socket')
                self.http['current_socket'] = socket
                if socket in self.http['sockets'] and 'end' not in self.http['sockets'][socket]:
                    self.http['sockets'][socket]['end'] = msg['timestamp']

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
    parser.add_argument('-s', '--start',
                        help="Start Time in UTC with microseconds YYYY-MM-DD HH:MM:SS.xxxxxx.")
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

    if not options.logfile or not options.start:
        parser.error("Input devtools file or start time is not specified.")

    parser = FirefoxLogParser()
    requests = parser.process_logs(options.logfile, options.start)
    if options.out:
        with open(options.out, 'w') as f_out:
            json.dump(requests, f_out, indent=4)

if __name__ == '__main__':
    #import cProfile
    #cProfile.run('main()', None, 2)
    main()
