#!/usr/bin/env python3
"""
Copyright 2019 WebPageTest LLC.
Copyright 2022 Google Inc.
Copyright 2022 Catchpoint Systems Inc.
Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
found in the LICENSE.md file.
"""
import base64
import gzip
import logging
import os
import re
from urllib.parse import urlparse
try:
    import ujson as json
except BaseException:
    import json

##########################################################################
#   Netlog processing
##########################################################################
class Netlog():
    """Main class"""
    def __init__(self):
        self.netlog = {'bytes_in': 0, 'bytes_out': 0, 'next_request_id': 1000000}
        self.netlog_requests = None
        self.marked_start_time = None
        self.start_time = None
        self.netlog_event_types = {}
        self.PRIORITY_MAP = {
            "VeryHigh": "Highest",
            "HIGHEST": "Highest",
            "MEDIUM": "High",
            "LOW": "Medium",
            "LOWEST": "Low",
            "IDLE": "Lowest",
            "VeryLow": "Lowest"
        }
        self.constants = None
        # Callbacks for streaming request state as events are processed
        self.on_request_created = None              # (request_id, request_info)
        self.on_request_headers_sent = None         # (request_id, request_headers)
        self.on_response_headers_received = None    # (request_id, response_headers)
        self.on_response_bytes_received = None      # (request_id, filtered_bytes)
        self.on_request_id_changed = None           # (request_id, new_request_id)

    def set_constants(self, constants):
        """Setup the event look-up tables"""
        self.constants = {}
        for key in constants:
            if isinstance(constants[key], dict) and key not in ['clientInfo']:
                # Reverse the lookup tables
                self.constants[key] = {}
                for name in constants[key]:
                    value = constants[key][name]
                    self.constants[key][value] = name
            else:
                self.constants[key] = constants[key]

    def add_event(self, event):
        """Hydrate a single event and process it"""
        try:
            self.hydrate_event(event)
            self.process_event(event)
        except Exception:
            logging.debug('error processing netlog event')

    def load_netlog(self, path):
        """Load and process the givent netlog"""
        with open(path, 'rt', encoding='utf-8') as f:
            started = False
            for line in f:
                try:
                    line = line.strip(", \r\n")
                    if started:
                        if line.startswith('{'):
                            event = json.loads(line)
                            self.add_event(event)
                    elif line.startswith('{"constants":'):
                        raw = json.loads(line + '}')
                        if raw and 'constants' in raw:
                            self.set_constants(raw['constants'])
                    elif line.startswith('"events": ['):
                        started = True
                except Exception:
                    logging.exception('Error processing netlog line')                

    def get_requests(self):
        return self.post_process_events()

    def hydrate_event(self, event):
        """Replace the lookup table values with the value names"""
        if self.constants is not None:
            const = self.constants
            if 'type' in event and 'logEventTypes' in const and event['type'] in const['logEventTypes']:
                event['type'] = const['logEventTypes'][event['type']]
            if 'phase' in event and 'logEventPhase' in const and event['phase'] in const['logEventPhase']:
                event['phase'] = const['logEventPhase'][event['phase']]
            if 'source' in event and isinstance(event['source'], dict):
                source = event['source']
                if 'type' in source and 'logSourceType' in const and source['type'] in const['logSourceType']:
                    source['type'] = const['logSourceType'][source['type']]
            if 'params' in event and isinstance(event['params'], dict):
                params = event['params']
                if 'cert_status' in params and 'certStatusFlag' in const:
                    cert_status = ''
                    for flag in const['certStatusFlag']:
                        if params['cert_status'] & flag:
                            if len(cert_status):
                                cert_status += ','
                            cert_status += const['certStatusFlag'][flag]
                    params['cert_status'] = cert_status
                if 'source_dependency' in params and isinstance(params['source_dependency'], dict):
                    src = event['params']['source_dependency']
                    if 'type' in src and 'logSourceType' in const and src['type'] in const['logSourceType']:
                        src['type'] = const['logSourceType'][src['type']]
                if 'dns_query_type' in params and 'dnsQueryType' in const and params['dns_query_type'] in const['dnsQueryType']:
                    params['dns_query_type'] = const['dnsQueryType'][params['dns_query_type']]
                if 'secure_dns_policy' in params and 'secureDnsMode' in const and params['secure_dns_policy'] in const['secureDnsMode']:
                    params['secure_dns_policy'] = const['secureDnsMode'][params['secure_dns_policy']]
                if 'secure_dns_mode' in params and 'secureDnsMode' in const and params['secure_dns_mode'] in const['secureDnsMode']:
                    params['secure_dns_mode'] = const['secureDnsMode'][params['secure_dns_mode']]
                if 'priority' in params and params['priority'] in self.PRIORITY_MAP:
                    params['priority'] = self.PRIORITY_MAP[params['priority']]
                if 'load_flags' in params and 'loadFlag' in const:
                    load_flags = ''
                    for flag in const['loadFlag']:
                        if params['load_flags'] & flag:
                            if len(load_flags):
                                load_flags += ','
                            load_flags += const['loadFlag'][flag]
                    params['load_flags'] = load_flags
                if 'net_error' in params and 'netError' in const and params['net_error'] in const['netError']:
                    params['net_error'] = const['netError'][params['net_error']]
            
    ##########################################################################
    #   Convert the raw events into requests
    ##########################################################################
    def post_process_events(self):
        """Post-process the raw netlog events into request data"""
        if self.netlog_requests is not None:
            return self.netlog_requests
        requests = []
        known_hosts = ['cache.pack.google.com', 'clients1.google.com', 'redirector.gvt1.com']
        last_time = 0
        if 'url_request' in self.netlog:
            for request_id in self.netlog['url_request']:
                request = self.netlog['url_request'][request_id]
                request['netlog_id'] = request_id
                request['fromNet'] = bool('start' in request)
                if 'start' in request and request['start'] > last_time:
                    last_time = request['start']
                if 'end' in request and request['end'] > last_time:
                    last_time = request['end']
                # build a URL from the request headers if one wasn't explicitly provided
                if 'url' not in request and 'request_headers' in request:
                    scheme = None
                    origin = None
                    path = None
                    if 'line' in request:
                        match = re.search(r'^[^\s]+\s([^\s]+)', request['line'])
                        if match:
                            path = match.group(1)
                    if 'group' in request:
                        scheme = 'http'
                        if request['group'].find('ssl/') >= 0:
                            scheme = 'https'
                    elif 'socket' in request and 'socket' in self.netlog and request['socket'] in self.netlog['socket']:
                        socket = self.netlog['socket'][request['socket']]
                        scheme = 'http'
                        if 'certificates' in socket or 'ssl_start' in socket:
                            scheme = 'https'
                    for header in request['request_headers']:
                        try:
                            index = header.find(u':', 1)
                            if index > 0:
                                key = header[:index].strip(u': ').lower()
                                value = header[index + 1:].strip(u': ')
                                if key == u'scheme':
                                    scheme = str(value)
                                elif key == u'host':
                                    origin = str(value)
                                elif key == u'authority':
                                    origin = str(value)
                                elif key == u'path':
                                    path = str(value)
                        except Exception:
                            logging.exception("Error generating url from request headers")
                    if scheme and origin and path:
                        request['url'] = scheme + u'://' + origin + path
                if 'url' in request and not request['url'].startswith('http://127.0.0.1') and \
                        not request['url'].startswith('http://192.168.10.'):
                    request_host = urlparse(request['url']).hostname
                    if request_host not in known_hosts:
                        known_hosts.append(request_host)
                    # Match orphaned request streams with their h2 sessions
                    if 'stream_id' in request and 'h2_session' not in request and 'url' in request:
                        for h2_session_id in self.netlog['h2_session']:
                            h2_session = self.netlog['h2_session'][h2_session_id]
                            if 'host' in h2_session:
                                session_host = h2_session['host'].split(':')[0]
                                if 'stream' in h2_session and \
                                        request['stream_id'] in h2_session['stream'] and \
                                        session_host == request_host and \
                                        'request_headers' in request and \
                                        'request_headers' in h2_session['stream'][request['stream_id']]:
                                    # See if the path header matches
                                    stream = h2_session['stream'][request['stream_id']]
                                    request_path = None
                                    stream_path = None
                                    for header in request['request_headers']:
                                        if header.startswith(':path:'):
                                            request_path = header
                                            break
                                    for header in stream['request_headers']:
                                        if header.startswith(':path:'):
                                            stream_path = header
                                            break
                                    if request_path is not None and request_path == stream_path:
                                        request['h2_session'] = h2_session_id
                                        break
                    # Copy any http/2 info over
                    if 'h2_session' in self.netlog and \
                            'h2_session' in request and \
                            request['h2_session'] in self.netlog['h2_session']:
                        h2_session = self.netlog['h2_session'][request['h2_session']]
                        if 'socket' in h2_session:
                            request['socket'] = h2_session['socket']
                        if 'stream_id' in request and \
                                'stream' in h2_session and \
                                request['stream_id'] in h2_session['stream']:
                            stream = h2_session['stream'][request['stream_id']]
                            if 'request_headers' in stream:
                                request['request_headers'] = stream['request_headers']
                            if 'response_headers' in stream:
                                request['response_headers'] = stream['response_headers']
                            if 'early_hint_headers' in stream:
                                request['early_hint_headers'] = stream['early_hint_headers']
                            if 'exclusive' in stream:
                                request['exclusive'] = 1 if stream['exclusive'] else 0
                            if 'parent_stream_id' in stream:
                                request['parent_stream_id'] = stream['parent_stream_id']
                            if 'weight' in stream:
                                request['weight'] = stream['weight']
                                if 'priority' not in request:
                                    if request['weight'] >= 256:
                                        request['priority'] = 'HIGHEST'
                                    elif request['weight'] >= 220:
                                        request['priority'] = 'MEDIUM'
                                    elif request['weight'] >= 183:
                                        request['priority'] = 'LOW'
                                    elif request['weight'] >= 147:
                                        request['priority'] = 'LOWEST'
                                    else:
                                        request['priority'] = 'IDLE'
                                    if request['priority'] in self.PRIORITY_MAP:
                                        request['priority'] = self.PRIORITY_MAP[request['priority']]
                            if 'first_byte' not in request and 'first_byte' in stream:
                                request['first_byte'] = stream['first_byte']
                            if 'end' not in request and 'end' in stream:
                                request['end'] = stream['end']
                            if stream['bytes_in'] > request['bytes_in']:
                                request['bytes_in'] = stream['bytes_in']
                                request['chunks'] = stream['chunks']
                    if 'phantom' not in request and 'request_headers' in request:
                        requests.append(request)
            # See if there were any connections for hosts that we didn't know abot that timed out
            if 'urls' in self.netlog:
                failed_hosts = {}
                if 'stream_job' in self.netlog:
                    for stream_job_id in self.netlog['stream_job']:
                        stream_job = self.netlog['stream_job'][stream_job_id]
                        if 'group' in stream_job and 'socket_start' in stream_job and 'socket' not in stream_job:
                            matches = re.match(r'^.*/([^:]+)\:\d+$', stream_job['group'])
                            if matches:
                                group_hostname = matches.group(1)
                                if group_hostname not in known_hosts and group_hostname not in failed_hosts:
                                    failed_hosts[group_hostname] = {'start': stream_job['socket_start']}
                                    if 'socket_end' in stream_job:
                                        failed_hosts[group_hostname]['end'] = stream_job['socket_end']
                                    else:
                                        failed_hosts[group_hostname]['end'] = max(stream_job['socket_start'], last_time)
                if failed_hosts:
                    for url in self.netlog['urls']:
                        host = urlparse(url).hostname
                        if host in failed_hosts:
                            request = {'url': url,
                                       'created': failed_hosts[host]['start'],
                                       'start': failed_hosts[host]['start'],
                                       'end': failed_hosts[host]['end'],
                                       'connect_start': failed_hosts[host]['start'],
                                       'connect_end': failed_hosts[host]['end'],
                                       'fromNet': True,
                                       'status': 12029}
                            requests.append(request)
            if len(requests):
                # Sort the requests by the start time
                requests.sort(key=lambda x: x['start'] if 'start' in x else x['created'])
                # Assign the socket connect time to the first request on each socket
                if 'socket' in self.netlog:
                    for request in requests or []:
                        if 'socket' in request and request['socket'] in self.netlog['socket']:
                            socket = self.netlog['socket'][request['socket']]
                            if 'address' in socket:
                                request['server_address'] = socket['address']
                            if 'source_address' in socket:
                                request['client_address'] = socket['source_address']
                            if 'group' in socket:
                                request['socket_group'] = socket['group']
                            if 'claimed' not in socket:
                                socket['claimed'] = True
                                if 'connect_start' in socket:
                                    request['connect_start'] = socket['connect_start']
                                if 'connect_end' in socket:
                                    request['connect_end'] = socket['connect_end']
                                if 'ssl_start' in socket:
                                    request['ssl_start'] = socket['ssl_start']
                                if 'ssl_end' in socket:
                                    request['ssl_end'] = socket['ssl_end']
                                if 'certificates' in socket:
                                    request['certificates'] = socket['certificates']
                                if 'h2_session' in request and request['h2_session'] in self.netlog['h2_session']:
                                    h2_session = self.netlog['h2_session'][request['h2_session']]
                                    if 'server_settings' in h2_session:
                                        request['http2_server_settings'] = h2_session['server_settings']
                                if 'tls_version' in socket:
                                    request['tls_version'] = socket['tls_version']
                                if 'tls_resumed' in socket:
                                    request['tls_resumed'] = socket['tls_resumed']
                                if 'tls_next_proto' in socket:
                                    request['tls_next_proto'] = socket['tls_next_proto']
                                if 'tls_cipher_suite' in socket:
                                    request['tls_cipher_suite'] = socket['tls_cipher_suite']

                # Assign the DNS lookup to the first request that connected to the DocumentSetDomain
                if 'dns' in self.netlog:
                    # Build a mapping of the DNS lookups for each domain
                    dns_lookups = {}
                    for dns_id in self.netlog['dns']:
                        dns = self.netlog['dns'][dns_id]
                        if 'host' in dns and 'start' in dns and 'end' in dns \
                                and dns['end'] >= dns['start'] and 'address_list' in dns:
                            hostname = dns['host']
                            separator = hostname.find(':')
                            if separator > 0:
                                hostname = hostname[:separator]
                            dns['elapsed'] = dns['end'] - dns['start']
                            if hostname not in dns_lookups:
                                dns_lookups[hostname] = dns
                            # collect all of the times for all of the DNS lookups for that host
                            if 'times' not in dns_lookups[hostname]:
                                dns_lookups[hostname]['times'] = []
                            dns_lookups[hostname]['times'].append({
                                'start': dns['start'],
                                'end': dns['end'],
                                'elapsed': dns['elapsed'],
                            })
                    # Go through the requests and assign the DNS lookups as needed
                    for request in requests or []:
                        if 'connect_start' in request:
                            hostname = urlparse(request['url']).hostname
                            if hostname in dns_lookups and 'claimed' not in dns_lookups[hostname]:
                                dns = dns_lookups[hostname]
                                dns['claimed'] = True
                                # Find the longest DNS time that completed before connect_start
                                if 'times' in dns_lookups[hostname]:
                                    elapsed = None
                                    for dns in dns_lookups[hostname]['times']:
                                        dns['end'] = min(dns['end'], request['connect_start'])
                                        if dns['end'] >= dns['start']:
                                            dns['elapsed'] = dns['end'] - dns['start']
                                            if elapsed is None or dns['elapsed'] > elapsed:
                                                elapsed = dns['elapsed']
                                                request['dns_start'] = dns['start']
                                                request['dns_end'] = dns['end']
                    # Make another pass for any DNS lookups that didn't establish a connection (HTTP/2 coalescing)
                    for request in requests or []:
                        hostname = urlparse(request['url']).hostname
                        if hostname in dns_lookups and 'claimed' not in dns_lookups[hostname]:
                            dns = dns_lookups[hostname]
                            dns['claimed'] = True
                            # Find the longest DNS time that completed before the request start
                            if 'times' in dns_lookups[hostname]:
                                elapsed = None
                                for dns in dns_lookups[hostname]['times']:
                                    dns['end'] = min(dns['end'], request['start'])
                                    if dns['end'] >= dns['start']:
                                        dns['elapsed'] = dns['end'] - dns['start']
                                        if elapsed is None or dns['elapsed'] > elapsed:
                                            elapsed = dns['elapsed']
                                            request['dns_start'] = dns['start']
                                            request['dns_end'] = dns['end']

                # Find the start timestamp if we didn't have one already
                times = ['dns_start', 'dns_end',
                         'connect_start', 'connect_end',
                         'ssl_start', 'ssl_end',
                         'start', 'created', 'first_byte', 'end']
                for request in requests or []:
                    for time_name in times:
                        if time_name in request and self.marked_start_time is None:
                            if self.start_time is None or request[time_name] < self.start_time:
                                self.start_time = request[time_name]
                # Go through and adjust all of the times to be relative in ms
                if self.start_time is not None:
                    for request in requests or []:
                        for time_name in times:
                            if time_name in request:
                                request[time_name] = \
                                        request[time_name] - self.start_time
                        for key in ['chunks', 'chunks_in', 'chunks_out']:
                            if key in request:
                                for chunk in request[key]:
                                    if 'ts' in chunk:
                                        chunk['ts'] = chunk['ts'] - self.start_time
                else:
                    requests = []
        if not len(requests):
            requests = None
        # Add the netlog request ID as a nanosecond addition to the start time
        # so that sorting by start time for requests that start within the same
        # millisecond is still correct.
        for request in requests or []:
            if 'start' in request and 'netlog_id' in request:
                request['start'] = float(request['start']) + (float(request['netlog_id'] % 10000) / 1000000.0)
        self.netlog_requests = requests
        return requests

    ##########################################################################
    #   Event Processing
    ##########################################################################
    def process_event(self, event):
        if 'time' in event and 'type' in event and 'phase' in event and \
                'source' in event and 'id' in event['source'] and 'type' in event['source']:
            try:
                event['time'] = int(event['time'])
                event_type = None
                name = event['type']
                event_type = event['source']['type']
                if event_type is not None:
                    if event_type == 'HOST_RESOLVER_IMPL_JOB' or \
                            name.startswith('HOST_RESOLVER'):
                        self.process_dns_event(event)
                    elif event_type == 'CONNECT_JOB' or \
                            event_type == 'SSL_CONNECT_JOB' or \
                            event_type == 'TRANSPORT_CONNECT_JOB':
                        self.process_connect_job_event(event)
                    elif event_type == 'HTTP_STREAM_JOB':
                        self.process_stream_job_event(event)
                    elif event_type == 'HTTP2_SESSION':
                        self.process_http2_session_event(event)
                    elif event_type == 'QUIC_SESSION':
                        self.process_quic_session_event(event)
                    elif event_type == 'SOCKET':
                        self.process_socket_event(event)
                    elif event_type == 'UDP_SOCKET':
                        self.process_udp_socket_event(event)
                    elif event_type == 'URL_REQUEST':
                        self.process_url_request_event(event)
                    elif event_type == 'DISK_CACHE_ENTRY':
                        self.process_disk_cache_event(event)
            except Exception:
                logging.exception('Error processing netlog event')

    def process_connect_job_event(self, event):
        """Connect jobs link sockets to DNS lookups/group names"""
        if 'connect_job' not in self.netlog:
            self.netlog['connect_job'] = {}
        request_id = event['source']['id']
        if request_id not in self.netlog['connect_job']:
            self.netlog['connect_job'][request_id] = {'created': event['time']}
        params = event['params'] if 'params' in event else {}
        entry = self.netlog['connect_job'][request_id]
        name = event['type']
        if name == 'TRANSPORT_CONNECT_JOB_CONNECT' and event['phase'] == 'PHASE_BEGIN':
            entry['connect_start'] = event['time']
        if name == 'TRANSPORT_CONNECT_JOB_CONNECT' and event['phase'] == 'PHASE_END':
            entry['connect_end'] = event['time']
        if 'source_dependency' in params and 'id' in params['source_dependency']:
            if name == 'CONNECT_JOB_SET_SOCKET':
                socket_id = params['source_dependency']['id']
                entry['socket'] = socket_id
                if 'socket' in self.netlog and socket_id in self.netlog['socket']:
                    if 'group' in entry:
                        self.netlog['socket'][socket_id]['group'] = entry['group']
                    if 'dns' in entry:
                        self.netlog['socket'][socket_id]['dns'] = entry['dns']
        if 'group_name' in params:
            entry['group'] = params['group_name']
        if 'group_id' in params:
            entry['group'] = params['group_id']

    def process_stream_job_event(self, event):
        """Strem jobs leank requests to sockets"""
        if 'stream_job' not in self.netlog:
            self.netlog['stream_job'] = {}
        request_id = event['source']['id']
        if request_id not in self.netlog['stream_job']:
            self.netlog['stream_job'][request_id] = {'created': event['time']}
        params = event['params'] if 'params' in event else {}
        entry = self.netlog['stream_job'][request_id]
        name = event['type']
        if 'group_name' in params:
            entry['group'] = params['group_name']
        if 'group_id' in params:
            entry['group'] = params['group_id']
        if name == 'HTTP_STREAM_REQUEST_STARTED_JOB':
            entry['start'] = event['time']
        if name == 'TCP_CLIENT_SOCKET_POOL_REQUESTED_SOCKET':
            entry['socket_start'] = event['time']
        if 'source_dependency' in params and 'id' in params['source_dependency']:
            if name == 'SOCKET_POOL_BOUND_TO_SOCKET':
                socket_id = params['source_dependency']['id']
                entry['socket_end'] = event['time']
                entry['socket'] = socket_id
                if 'url_request' in entry and entry['urlrequest'] in self.netlog['urlrequest']:
                    self.netlog['urlrequest'][entry['urlrequest']]['socket'] = socket_id
                    if 'group' in entry:
                        self.netlog['urlrequest'][entry['urlrequest']]['group'] = entry['group']
            if name == 'HTTP_STREAM_JOB_BOUND_TO_REQUEST':
                url_request_id = params['source_dependency']['id']
                entry['url_request'] = url_request_id
                if 'socket_end' not in entry:
                    entry['socket_end'] = event['time']
                if url_request_id in self.netlog['url_request']:
                    url_request = self.netlog['url_request'][url_request_id]
                    if 'group' in entry:
                        url_request['group'] = entry['group']
                    if 'socket' in entry:
                        url_request['socket'] = entry['socket']
                    if 'h2_session' in entry:
                        url_request['h2_session'] = entry['h2_session']
            if name == 'HTTP2_SESSION_POOL_IMPORTED_SESSION_FROM_SOCKET' or \
                    name == 'HTTP2_SESSION_POOL_FOUND_EXISTING_SESSION' or \
                    name == 'HTTP2_SESSION_POOL_FOUND_EXISTING_SESSION_FROM_IP_POOL':
                h2_session_id = params['source_dependency']['id']
                entry['h2_session'] = h2_session_id
                if 'socket_end' not in entry:
                    entry['socket_end'] = event['time']
                if h2_session_id in self.netlog['h2_session'] and 'socket' in self.netlog['h2_session'][h2_session_id]:
                    entry['socket'] = self.netlog['h2_session'][h2_session_id]['socket']
                if 'url_request' in entry and entry['urlrequest'] in self.netlog['urlrequest']:
                    self.netlog['urlrequest'][entry['urlrequest']]['h2_session'] = h2_session_id

    def process_http2_session_event(self, event):
        """Raw H2 session information (linked to sockets and requests)"""
        if 'h2_session' not in self.netlog:
            self.netlog['h2_session'] = {}
        session_id = event['source']['id']
        if session_id not in self.netlog['h2_session']:
            self.netlog['h2_session'][session_id] = {'stream': {}}
        params = event['params'] if 'params' in event else {}
        entry = self.netlog['h2_session'][session_id]
        name = event['type']
        if 'source_dependency' in params and 'id' in params['source_dependency']:
            if name == 'HTTP2_SESSION_INITIALIZED':
                socket_id = params['source_dependency']['id']
                entry['socket'] = socket_id
                if 'socket' in self.netlog and socket_id in self.netlog['socket']:
                    self.netlog['socket']['h2_session'] = session_id
        if 'host' not in entry and 'host' in params:
            entry['host'] = params['host']
        if 'protocol' not in entry and 'protocol' in params:
            entry['protocol'] = params['protocol']
        if 'stream_id' in params:
            stream_id = params['stream_id']
            if stream_id not in entry['stream']:
                entry['stream'][stream_id] = {'bytes_in': 0, 'chunks': []}
            stream = entry['stream'][stream_id]
            if 'exclusive' in params:
                stream['exclusive'] = params['exclusive']
            if 'parent_stream_id' in params:
                stream['parent_stream_id'] = params['parent_stream_id']
            if 'weight' in params:
                stream['weight'] = params['weight']
            if 'url' in params:
                stream['url'] = params['url'].split('#', 1)[0]
                if 'url_request' in stream:
                    request_id = stream['url_request']
                    if 'url_request' in self.netlog and request_id in self.netlog['url_request']:
                        request = self.netlog['url_request'][request_id]
                        request['url'] = params['url'].split('#', 1)[0]
            if name == 'HTTP2_SESSION_RECV_DATA' and 'size' in params:
                stream['end'] = event['time']
                if 'first_byte' not in stream:
                    stream['first_byte'] = event['time']
                stream['bytes_in'] += params['size']
                stream['chunks'].append({'ts': event['time'], 'bytes': params['size']})
            if name == 'HTTP2_SESSION_SEND_HEADERS':
                if 'start' not in stream:
                    stream['start'] = event['time']
                if 'headers' in params:
                    stream['request_headers'] = params['headers']
                    if 'url_request' in stream and self.on_request_headers_sent is not None:
                        self.on_request_headers_sent(str(stream['url_request']), params['headers'])
            if name == 'HTTP2_SESSION_RECV_HEADERS':
                if 'first_byte' not in stream:
                    stream['first_byte'] = event['time']
                stream['end'] = event['time']
                if 'headers' in params:
                    stream['response_headers'] = params['headers']
                    if 'url_request' in stream and self.on_response_headers_received is not None:
                        self.on_response_headers_received(str(stream['url_request']), params['headers'])
            if name == 'HTTP2_STREAM_ADOPTED_PUSH_STREAM' and 'url' in params and 'url_request' in self.netlog:
                # Clone the fake urlrequest entry to the real one and delete the fake entry
                old_request = stream['url_request'] if 'url_request' in stream else None
                url = params['url'].split('#', 1)[0]
                for request_id in self.netlog['url_request']:
                    request = self.netlog['url_request'][request_id]
                    if 'url' in request and url == request['url'] and 'start' not in request:
                        new_request = request_id
                        break
                if old_request and new_request and old_request != new_request and old_request in self.netlog['url_request'] and new_request in self.netlog['url_request']:
                    old = self.netlog['url_request'][old_request]
                    new = self.netlog['url_request'][new_request]
                    for key in old:
                        new[key] = old[key]
                    stream['url_request'] = new_request
                    del self.netlog['url_request'][old_request]
        if name == 'HTTP2_SESSION_RECV_PUSH_PROMISE' and 'promised_stream_id' in params:
            # Create a fake request to match the push
            if 'url_request' not in self.netlog:
                self.netlog['url_request'] = {}
            request_id = self.netlog['next_request_id']
            self.netlog['next_request_id'] += 1
            self.netlog['url_request'][request_id] = {'bytes_in': 0,
                                                      'chunks': [],
                                                      'created': event['time']}
            request = self.netlog['url_request'][request_id]
            stream_id = params['promised_stream_id']
            if stream_id not in entry['stream']:
                entry['stream'][stream_id] = {'bytes_in': 0, 'chunks': []}
            stream = entry['stream'][stream_id]
            if 'headers' in params:
                stream['request_headers'] = params['headers']
                # synthesize a URL from the request headers
                scheme = None
                authority = None
                path = None
                for header in params['headers']:
                    match = re.search(r':scheme: (.+)', header)
                    if match:
                        scheme = match.group(1)
                    match = re.search(r':authority: (.+)', header)
                    if match:
                        authority = match.group(1)
                    match = re.search(r':path: (.+)', header)
                    if match:
                        path = match.group(1)
                if scheme is not None and authority is not None and path is not None:
                    url = '{0}://{1}{2}'.format(scheme, authority, path).split('#', 1)[0]
                    request['url'] = url
                    stream['url'] = url
            request['protocol'] = 'HTTP/2'
            request['h2_session'] = session_id
            request['stream_id'] = stream_id
            request['start'] = event['time']
            request['pushed'] = True
            stream['pushed'] = True
            stream['url_request'] = request_id
            if 'socket' in entry:
                request['socket'] = entry['socket']
        if name == 'HTTP2_SESSION_RECV_SETTING' and 'id' in params and 'value' in params:
            setting_id = None
            match = re.search(r'\d+ \((.+)\)', params['id'])
            if match:
                setting_id = match.group(1)
                if 'server_settings' not in entry:
                    entry['server_settings'] = {}
                entry['server_settings'][setting_id] = params['value']

    def process_quic_session_event(self, event):
        """Raw QUIC session information (linked to sockets and requests)"""
        if 'quic_session' not in self.netlog:
            self.netlog['quic_session'] = {}
        session_id = event['source']['id']
        if session_id not in self.netlog['quic_session']:
            self.netlog['quic_session'][session_id] = {'stream': {}}
        params = event['params'] if 'params' in event else {}
        entry = self.netlog['quic_session'][session_id]
        name = event['type']
        if 'host' not in entry and 'host' in params:
            entry['host'] = params['host']
        if 'port' not in entry and 'port' in params:
            entry['port'] = params['port']
        if 'version' not in entry and 'version' in params:
            entry['version'] = params['version']
        if 'peer_address' not in entry and 'peer_address' in params:
            entry['peer_address'] = params['peer_address']
        if 'self_address' not in entry and 'self_address' in params:
            entry['self_address'] = params['self_address']
        if name == 'QUIC_SESSION_PACKET_SENT' and 'connect_start' not in entry:
            entry['connect_start'] = event['time']
        if name == 'QUIC_SESSION_VERSION_NEGOTIATED' and 'connect_end' not in entry:
            entry['connect_end'] = event['time']
            if 'version' in params:
                entry['version'] = params['version']
        if name == 'CERT_VERIFIER_REQUEST' and 'connect_end' in entry:
            if 'tls_start' not in entry:
                entry['tls_start'] = entry['connect_end']
            if 'tls_end' not in entry:
                entry['tls_end'] = event['time']
        if 'stream_id' in params:
            stream_id = params['stream_id']
            if stream_id not in entry['stream']:
                entry['stream'][stream_id] = {'bytes_in': 0, 'chunks': []}
            stream = entry['stream'][stream_id]
            if name == 'QUIC_CHROMIUM_CLIENT_STREAM_SEND_REQUEST_HEADERS':
                if 'start' not in stream:
                    stream['start'] = event['time']
                if 'headers' in params:
                    stream['request_headers'] = params['headers']
            if name == 'QUIC_CHROMIUM_CLIENT_STREAM_READ_RESPONSE_HEADERS':
                if 'first_byte' not in stream:
                    stream['first_byte'] = event['time']
                stream['end'] = event['time']
                if 'headers' in params:
                    stream['response_headers'] = params['headers']

    def process_dns_event(self, event):
        if 'dns' not in self.netlog:
            self.netlog['dns'] = {}
        request_id = event['source']['id']
        if request_id not in self.netlog['dns']:
            self.netlog['dns'][request_id] = {}
        params = event['params'] if 'params' in event else {}
        entry = self.netlog['dns'][request_id]
        name = event['type']
        if 'source_dependency' in params and 'id' in params['source_dependency']:
            parent_id = params['source_dependency']['id']
            if 'connect_job' in self.netlog and parent_id in self.netlog['connect_job']:
                self.netlog['connect_job'][parent_id]['dns'] = request_id
        if name == 'HOST_RESOLVER_IMPL_REQUEST' and 'phase' in event:
            if event['phase'] == 'PHASE_BEGIN':
                if 'start' not in entry or event['time'] < entry['start']:
                    entry['start'] = event['time']
            if event['phase'] == 'PHASE_END':
                if 'end' not in entry or event['time'] > entry['end']:
                    entry['end'] = event['time']
        if 'start' not in entry and name == 'HOST_RESOLVER_IMPL_ATTEMPT_STARTED':
            entry['start'] = event['time']
        if 'start' not in entry and name == 'HOST_RESOLVER_MANAGER_ATTEMPT_STARTED':
            entry['start'] = event['time']
        if name == 'HOST_RESOLVER_IMPL_ATTEMPT_FINISHED':
            entry['end'] = event['time']
        if name == 'HOST_RESOLVER_MANAGER_ATTEMPT_FINISHED':
            entry['end'] = event['time']
        if name == 'HOST_RESOLVER_IMPL_CACHE_HIT':
            if 'end' not in entry or event['time'] > entry['end']:
                entry['end'] = event['time']
        if 'host' not in entry and 'host' in params:
            entry['host'] = params['host']
        if 'address_list' in params:
            entry['address_list'] = params['address_list']

    def process_socket_event(self, event):
        if 'socket' not in self.netlog:
            self.netlog['socket'] = {}
        request_id = event['source']['id']
        if request_id not in self.netlog['socket']:
            self.netlog['socket'][request_id] = {'bytes_out': 0, 'bytes_in': 0,
                                                 'chunks_out': [], 'chunks_in': []}
        params = event['params'] if 'params' in event else {}
        entry = self.netlog['socket'][request_id]
        name = event['type']
        if 'address' in params:
            entry['address'] = params['address']
        if 'source_address' in params:
            entry['source_address'] = params['source_address']
        if 'connect_start' not in entry and name == 'TCP_CONNECT_ATTEMPT' and \
                event['phase'] == 'PHASE_BEGIN':
            entry['connect_start'] = event['time']
        if name == 'TCP_CONNECT_ATTEMPT' and event['phase'] == 'PHASE_END':
            entry['connect_end'] = event['time']
        if name == 'SSL_CONNECT':
            if 'connect_end' not in entry:
                entry['connect_end'] = event['time']
            if 'ssl_start' not in entry and event['phase'] == 'PHASE_BEGIN':
                entry['ssl_start'] = event['time']
            if event['phase'] == 'PHASE_END':
                entry['ssl_end'] = event['time']
            if 'version' in params:
                entry['tls_version'] = params['version']
            if 'is_resumed' in params:
                entry['tls_resumed'] = params['is_resumed']
            if 'next_proto' in params:
                entry['tls_next_proto'] = params['next_proto']
            if 'cipher_suite' in params:
                entry['tls_cipher_suite'] = params['cipher_suite']
        if name == 'SOCKET_BYTES_SENT' and 'byte_count' in params:
            if 'connect_end' not in entry:
                entry['connect_end'] = event['time']
            entry['bytes_out'] += params['byte_count']
            entry['chunks_out'].append({'ts': event['time'], 'bytes': params['byte_count']})
        if name == 'SOCKET_BYTES_RECEIVED' and 'byte_count' in params:
            entry['bytes_in'] += params['byte_count']
            entry['chunks_in'].append({'ts': event['time'], 'bytes': params['byte_count']})
        if name == 'SSL_CERTIFICATES_RECEIVED' and 'certificates' in params:
            if 'certificates' not in entry:
                entry['certificates'] = []
            entry['certificates'].extend(params['certificates'])

    def process_udp_socket_event(self, event):
        if 'socket' not in self.netlog:
            self.netlog['socket'] = {}
        request_id = event['source']['id']
        if request_id not in self.netlog['socket']:
            self.netlog['socket'][request_id] = {'bytes_out': 0, 'bytes_in': 0,
                                                 'chunks_out': [], 'chunks_in': []}
        params = event['params'] if 'params' in event else {}
        entry = self.netlog['socket'][request_id]
        name = event['type']
        if name == 'UDP_CONNECT' and 'address' in params:
            entry['address'] = params['address']
        if name == 'UDP_LOCAL_ADDRESS' and 'address' in params:
            entry['source_address'] = params['address']
        if 'connect_start' not in entry and name == 'UDP_CONNECT' and \
                event['phase'] == 'PHASE_BEGIN':
            entry['connect_start'] = event['time']
        if name == 'UDP_CONNECT' and event['phase'] == 'PHASE_END':
            entry['connect_end'] = event['time']
        if name == 'UDP_BYTES_SENT' and 'byte_count' in params:
            entry['bytes_out'] += params['byte_count']
            entry['chunks_out'].append({'ts': event['time'], 'bytes': params['byte_count']})
        if name == 'UDP_BYTES_RECEIVED' and 'byte_count' in params:
            entry['bytes_in'] += params['byte_count']
            entry['chunks_in'].append({'ts': event['time'], 'bytes': params['byte_count']})

    def process_url_request_event(self, event):
        if 'url_request' not in self.netlog:
            self.netlog['url_request'] = {}
        request_id = event['source']['id']
        if request_id not in self.netlog['url_request']:
            self.netlog['url_request'][request_id] = {'bytes_in': 0,
                                                      'chunks': [],
                                                      'created': event['time']}
        params = event['params'] if 'params' in event else {}
        entry = self.netlog['url_request'][request_id]
        name = event['type']
        if 'priority' in params:
            if params['priority'] in self.PRIORITY_MAP:
                params['priority'] = self.PRIORITY_MAP[params['priority']]
            entry['priority'] = params['priority']
            if 'initial_priority' not in entry:
                entry['initial_priority'] = params['priority']
        if 'method' in params:
            entry['method'] = params['method']
        if 'url' in params:
            entry['url'] = params['url'].split('#', 1)[0]
        if 'start' not in entry and name == 'HTTP_TRANSACTION_SEND_REQUEST':
            entry['start'] = event['time']
            if self.on_request_created is not None:
                self.on_request_created(str(request_id), entry)
        if 'headers' in params and name == 'HTTP_TRANSACTION_SEND_REQUEST_HEADERS':
            entry['request_headers'] = params['headers']
            if 'line' in params:
                entry['line'] = params['line']
            if 'start' not in entry:
                entry['start'] = event['time']
                if self.on_request_created is not None:
                    self.on_request_created(str(request_id), entry)
            if self.on_request_headers_sent is not None:
                self.on_request_headers_sent(str(request_id), entry['request_headers'])
        if 'headers' in params and name == 'HTTP_TRANSACTION_HTTP2_SEND_REQUEST_HEADERS':
            if isinstance(params['headers'], dict):
                entry['request_headers'] = []
                for key in params['headers']:
                    entry['request_headers'].append('{0}: {1}'.format(key, params['headers'][key]))
            else:
                entry['request_headers'] = params['headers']
            entry['protocol'] = 'HTTP/2'
            if 'line' in params:
                entry['line'] = params['line']
            if 'start' not in entry:
                entry['start'] = event['time']
                if self.on_request_created is not None:
                    self.on_request_created(str(request_id), entry)
            if self.on_request_headers_sent is not None:
                self.on_request_headers_sent(str(request_id), entry['request_headers'])
        if 'headers' in params and name == 'HTTP_TRANSACTION_QUIC_SEND_REQUEST_HEADERS':
            if isinstance(params['headers'], dict):
                entry['request_headers'] = []
                for key in params['headers']:
                    entry['request_headers'].append('{0}: {1}'.format(key, params['headers'][key]))
            else:
                entry['request_headers'] = params['headers']
            if 'line' in params:
                entry['line'] = params['line']
            entry['protocol'] = 'QUIC'
            if 'start' not in entry:
                entry['start'] = event['time']
                if self.on_request_created is not None:
                    self.on_request_created(str(request_id), entry)
            if self.on_request_headers_sent is not None:
                self.on_request_headers_sent(str(request_id), entry['request_headers'])
        if 'headers' in params and name == 'HTTP_TRANSACTION_READ_RESPONSE_HEADERS':
            entry['response_headers'] = params['headers']
            if 'first_byte' not in entry:
                entry['first_byte'] = event['time']
            entry['end'] = event['time']
            if self.on_response_headers_received is not None:
                self.on_response_headers_received(str(request_id), entry['response_headers'])
        if 'headers' in params and name == 'HTTP_TRANSACTION_READ_EARLY_HINTS_RESPONSE_HEADERS':
            entry['early_hint_headers'] = params['headers']
            entry['end'] = event['time']
        if 'byte_count' in params and name == 'URL_REQUEST_JOB_BYTES_READ':
            entry['has_raw_bytes'] = True
            entry['end'] = event['time']
            entry['bytes_in'] += params['byte_count']
            entry['chunks'].append({'ts': event['time'], 'bytes': params['byte_count']})
        if 'byte_count' in params and name == 'URL_REQUEST_JOB_FILTERED_BYTES_READ':
            entry['end'] = event['time']
            if 'uncompressed_bytes_in' not in entry:
                entry['uncompressed_bytes_in'] = 0
            entry['uncompressed_bytes_in'] += params['byte_count']
            if 'has_raw_bytes' not in entry or not entry['has_raw_bytes']:
                entry['bytes_in'] += params['byte_count']
                entry['chunks'].append({'ts': event['time'], 'bytes': params['byte_count']})
            elif entry['chunks']:
                entry['chunks'][-1]['inflated'] = params['byte_count']
            if 'bytes' in params and self.on_response_bytes_received is not None:
                try:
                    raw_bytes = base64.b64decode(params['bytes'])
                    self.on_response_bytes_received(str(request_id), raw_bytes)
                except Exception:
                    logging.exception('Error decoding netlog response bytes')
        if 'stream_id' in params:
            entry['stream_id'] = params['stream_id']
        if name == 'URL_REQUEST_REDIRECTED':
            new_id = self.netlog['next_request_id']
            self.netlog['next_request_id'] += 1
            self.netlog['url_request'][new_id] = entry
            del self.netlog['url_request'][request_id]
            if self.on_request_id_changed is not None:
                self.on_request_id_changed(str(request_id), str(new_id))
            # Remap any pointers to the urlrequest to point to the new ID
            if 'stream_job' in self.netlog:
                for job_id in self.netlog['stream_job']:
                    job = self.netlog['stream_job'][job_id]
                    if 'url_request' in job and job['url_request'] == request_id:
                        job['url_request'] = new_id
            if 'h2_session' in self.netlog:
                for session_id in self.netlog['h2_session']:
                    session = self.netlog['h2_session'][session_id]
                    if 'stream' in session:
                        for stream_id in session['stream']:
                            stream = session['stream'][stream_id]
                            if 'url_request' in stream and stream['url_request'] == request_id:
                                stream['url_request'] = new_id
    
    def process_disk_cache_event(self, event):
        """Disk cache events"""
        if 'params' in event and 'key' in event['params']:
            url = event['params']['key']
            space_index = url.rfind(' ')
            if space_index >= 0:
                url = url[space_index + 1:]
            if 'urls' not in self.netlog:
                self.netlog['urls'] = {}
            if url not in self.netlog['urls']:
                self.netlog['urls'][url] = {'start': event['time']}

##########################################################################
#   CLI Entry Point
##########################################################################
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Chrome netlog parser.', prog='netlog')
    parser.add_argument('-v', '--verbose', action='count',
                        help="Increase verbosity (specify multiple times for more). -vvvv for full debug output.")
    parser.add_argument('-o', '--out', help="Output requests file (defaults to stdout).")
    parser.add_argument('file', type=str, help="Input netlog file path")
    options = parser.parse_args()

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

    if not options.file:
        parser.error("Input netlog file is not specified.")
    
    netlog = Netlog()
    netlog.load_netlog(options.file)
    requests = netlog.get_requests()
    if options.out:
        try:
            _, ext = os.path.splitext(options.out)
            if ext.lower() == '.gz':
                with gzip.open(options.out, 'wt', encoding='utf-8') as f:
                    json.dump(requests, f)
            else:
                with open(options.out, 'wt', encoding='utf-8') as f:
                    json.dump(requests, f)
        except BaseException:
            logging.exception("Error writing to " + options.out)
    else:
        print(json.dumps(requests, indent=4, sort_keys=True))


if '__main__' == __name__:
    #import cProfile
    #cProfile.run('main()', None, 2)
    main()
