#!/usr/bin/env python
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
import gzip
import logging
import os
import re
import time
import urlparse

# try a fast json parser if it is installed
try:
    import ujson as json
except BaseException:
    import json

class DevToolsParser(object):
    """Main class"""
    def __init__(self, options):
        self.devtools_file = options['devtools']
        self.netlog_requests_file = options['netlog'] if 'netlog' in options else None
        self.optimization = options['optimization'] if 'optimization' in options else None
        self.user_timing_file = options['user'] if 'user' in options else None
        self.coverage = options['coverage'] if 'coverage' in options else None
        self.cpu_times = options['cpu'] if 'cpu' in options else None
        self.cached = options['cached'] if 'cached' in options else False
        self.out_file = options['out']
        self.result = {'pageData': {}, 'requests': []}
        self.request_ids = {}
        self.script_ids = {}

    def process(self):
        """Main entry point for processing"""
        logging.debug("Processing raw devtools events")
        raw_requests, raw_page_data = self.extract_net_requests()
        if len(raw_requests) or len(raw_page_data):
            logging.debug("Extracting requests and page data")
            self.process_requests(raw_requests, raw_page_data)
            logging.debug("Adding netlog requests")
            self.process_netlog_requests()
            logging.debug("Updating page-level stats from user timing")
            self.process_user_timing()
            logging.debug("Calculating page-level stats")
            self.process_page_data()
            logging.debug("Adding optimization results")
            self.process_optimization_results()
            logging.debug("Adding code coverage results")
            self.process_code_coverage()
            logging.debug("Calculating cpu times")
            self.process_cpu_times()
            logging.debug("Writing result")
            self.make_utf8(self.result)
            self.write()

    def make_utf8(self, data):
        """Convert the given array to utf8"""
        if isinstance(data, dict):
            for key in data:
                entry = data[key]
                if isinstance(entry, dict) or isinstance(entry, list):
                    self.make_utf8(entry)
                elif isinstance(entry, str):
                    try:
                        data[key] = unicode(entry)
                    except Exception:
                        pass
        elif isinstance(data, list):
            for key in xrange(len(data)):
                entry = data[key]
                if isinstance(entry, dict) or isinstance(entry, list):
                    self.make_utf8(entry)
                elif isinstance(entry, str):
                    try:
                        data[key] = unicode(entry)
                    except Exception:
                        pass

    def write(self):
        """Write out the resulting json data"""
        if self.out_file is not None:
            if len(self.result['pageData']) or len(self.result['requests']):
                try:
                    _, ext = os.path.splitext(self.out_file)
                    if ext.lower() == '.gz':
                        with gzip.open(self.out_file, 'wb') as f_out:
                            json.dump(self.result, f_out)
                    else:
                        with open(self.out_file, 'w') as f_out:
                            json.dump(self.result, f_out)
                except Exception:
                    logging.critical("Error writing to " + self.out_file)

    def extract_net_requests(self):
        """Load the events we are interested in"""
        has_request_headers = False
        net_requests = []
        page_data = {'endTime': 0}
        _, ext = os.path.splitext(self.devtools_file)
        if ext.lower() == '.gz':
            f_in = gzip.open(self.devtools_file, 'rb')
        else:
            f_in = open(self.devtools_file, 'r')
        raw_events = json.load(f_in)
        # sort all of the events by timestamp
        if len(raw_events):
            raw_events.sort(key=lambda x: x['params']['timestamp'] if \
                ('params' in x and 'timestamp' in x['params']) else 9999999)
        f_in.close()
        if raw_events is not None and len(raw_events):
            end_timestamp = None
            first_timestamp = None
            raw_requests = {}
            id_map = {}
            for raw_event in raw_events:
                if 'method' in raw_event and 'params' in raw_event:
                    method = raw_event['method']
                    params = raw_event['params']
                    request_id = None
                    original_id = None
                    if 'requestId' in params:
                        request_id = params['requestId']
                        original_id = request_id
                        if request_id in id_map:
                            request_id += '-' + str(id_map[request_id])
                    # Pull out the script ID's
                    if method == 'Debugger.scriptParsed' and 'scriptId' in params:
                        script_id = params['scriptId']
                        script_url = None
                        if script_id not in self.script_ids:
                            if 'stackTrace' in params and 'callFrames' in params['stackTrace']:
                                for frame in params['stackTrace']['callFrames']:
                                    if 'url' in frame and frame['url']:
                                        if script_url is None:
                                            script_url = frame['url']
                                        if 'scriptId' in frame and frame['scriptId'] and frame['scriptId'] not in self.script_ids:
                                            self.script_ids[frame['scriptId']] = script_url
                            if script_url is None and 'url' in params and params['url']:
                                script_url = params['url']
                            if script_url is not None:
                                self.script_ids[script_id] = script_url
                    # Handle the events without timestamps (which will be sorted to the end)
                    if method == 'Page.frameNavigated' and 'frame' in params and \
                            'id' in params['frame'] and 'parentId' not in params['frame']:
                        page_data['main_frame'] = params['frame']['id']
                    if method == 'Network.requestServedFromCache' and 'requestId' in params and \
                            request_id is not None and request_id in raw_requests:
                        raw_requests[request_id]['fromNet'] = False
                        raw_requests[request_id]['fromCache'] = True
                    # Adjust all of the timestamps to be relative to the start of navigation
                    # and in milliseconds
                    if first_timestamp is None and 'timestamp' in params and \
                            method == 'Network.requestWillBeSent':
                        first_timestamp = params['timestamp']
                    if first_timestamp is not None and 'timestamp' in params:
                        if params['timestamp'] >= first_timestamp:
                            params['timestamp'] -= first_timestamp
                            params['timestamp'] *= 1000.0
                        else:
                            continue
                    if method == 'Page.loadEventFired' and 'timestamp' in params and \
                            ('onload' not in page_data or
                             params['timestamp'] > page_data['onload']):
                        page_data['onload'] = params['timestamp']
                    if 'timestamp' in params and request_id is not None:
                        timestamp = params['timestamp']
                        if method == 'Network.requestWillBeSent' and 'request' in params and \
                                'url' in params['request'] and \
                                params['request']['url'][:4] == 'http':
                            request = params['request']
                            request['raw_id'] = original_id
                            request['startTime'] = timestamp
                            if 'frameId' in params:
                                request['frame_id'] = params['frameId']
                            elif 'main_frame' in page_data:
                                request['frame_id'] = page_data['main_frame']
                            if 'initiator' in params:
                                request['initiator'] = params['initiator']
                            # Redirects re-use the same ID so we need to fake a new request
                            if request_id in raw_requests:
                                if 'redirectResponse' in params:
                                    if 'endTime' not in raw_requests[request_id] or \
                                            timestamp > raw_requests[request_id]['endTime']:
                                        raw_requests[request_id]['endTime'] = timestamp
                                    if 'firstByteTime' not in raw_requests[request_id]:
                                        raw_requests[request_id]['firstByteTime'] = timestamp
                                    # iOS incorrectly sets the fromNet flag to false for resources
                                    # from cache but it doesn't have any send headers for those
                                    # requests so use that as an indicator.
                                    raw_requests[request_id]['fromNet'] = False
                                    if 'fromDiskCache' in params['redirectResponse'] and \
                                            not params['redirectResponse']['fromDiskCache'] and \
                                            'headers' in raw_requests[request_id] and \
                                            len(raw_requests[request_id]['headers']):
                                        raw_requests[request_id]['fromNet'] = True
                                    raw_requests[request_id]['response'] = \
                                        params['redirectResponse']
                                count = 0
                                if original_id in id_map:
                                    count = id_map[original_id]
                                id_map[original_id] = count + 1
                                new_id = original_id + '-' + str(id_map[original_id])
                                request_id = new_id
                            request['id'] = request_id
                            raw_requests[request_id] = dict(request)
                        elif request_id in raw_requests:
                            request = raw_requests[request_id]
                            if 'endTime' not in request or timestamp > request['endTime']:
                                request['endTime'] = timestamp
                            if method == 'Network.dataReceived':
                                if 'firstByteTime' not in request:
                                    request['firstByteTime'] = timestamp
                                if 'bytesInData' not in request:
                                    request['bytesInData'] = 0
                                if 'dataLength' in params:
                                    request['bytesInData'] += params['dataLength']
                                if 'bytesInEncoded' not in request:
                                    request['bytesInEncoded'] = 0
                                if 'encodedDataLength' in params and params['encodedDataLength']:
                                    if 'bytesFinished' not in request:
                                        request['bytesInEncoded'] += params['encodedDataLength']
                                        if 'chunks' not in request:
                                            request['chunks'] = []
                                        request['chunks'].append({'ts': timestamp, 'bytes': params['encodedDataLength']})
                                elif 'dataLength' in params and params['dataLength']:
                                    if 'chunks' not in request:
                                        request['chunks'] = []
                                    request['chunks'].append({'ts': timestamp, 'bytes': params['dataLength']})
                            if method == 'Network.responseReceived' and 'response' in params:
                                if not has_request_headers and \
                                        'requestHeaders' in params['response']:
                                    has_request_headers = True
                                if 'firstByteTime' not in request:
                                    request['firstByteTime'] = timestamp
                                # the timing data for cached resources is completely bogus
                                if 'fromCache' in request and 'timing' in params['response']:
                                    del params['response']['timing']
                                # iOS incorrectly sets the fromNet flag to false for resources
                                # from cache but it doesn't have any send headers for those
                                # requests so use that as an indicator.
                                request['fromNet'] = False
                                if 'fromDiskCache' in params['response'] and \
                                        not params['response']['fromDiskCache'] and \
                                        'headers' in request and len(request['headers']):
                                    request['fromNet'] = True
                                # Chrome reports some phantom duplicate requests
                                '''
                                if has_request_headers and \
                                        'requestHeaders' not in params['response']:
                                    url = request['url']
                                    if url not in self.request_ids:
                                        self.request_ids[url] = []
                                    self.request_ids[url].append(request_id)
                                    request['fromNet'] = False
                                '''
                                request['response'] = params['response']
                            if method == 'Network.loadingFinished':
                                if 'firstByteTime' not in request:
                                    request['firstByteTime'] = timestamp
                                if 'encodedDataLength' in params:
                                    request['bytesInEncoded'] = params['encodedDataLength']
                                    request['bytesFinished'] = True
                            if method == 'Network.loadingFailed' and 'response' not in request and \
                                    ('fromCache' not in request or not request['fromCache']):
                                if 'blockedReason' not in params and \
                                        ('canceled' not in params or not params['canceled']):
                                    # Special case ERR_CONNECTION_REFUSED.
                                    # Request blocking is done by mapping domains to localhost
                                    # which can cause ERR_CONNECTION_REFUSED errors.
                                    # Real failures will still be in the netlog.
                                    if 'errorText' in params and \
                                            params['errorText'].find('ERR_CONNECTION_REFUSED'):
                                        request['fromNet'] = False
                                    else:
                                        request['fromNet'] = True
                                        request['errorCode'] = 12999
                                        if 'firstByteTime' not in request:
                                            request['firstByteTime'] = timestamp
                                        if 'errorText' in params:
                                            request['error'] = params['errorText']
                                        if 'error' in params:
                                            request['errorCode'] = params['error']
                                else:
                                    request['fromNet'] = False
                    if method == 'Page.domContentEventFired' and 'timestamp' in params and \
                            'domContentLoadedEventStart' not in page_data:
                        page_data['domContentLoadedEventStart'] = params['timestamp']
                        page_data['domContentLoadedEventEnd'] = params['timestamp']
            # go through and error-out any requests that started but never got
            # a response or error
            for request_id in raw_requests:
                request = raw_requests[request_id]
                if 'endTime' not in request:
                    request['fromNet'] = True
                    request['errorCode'] = 12999
            # pull out just the requests that were served on the wire
            for request_id in raw_requests:
                request = raw_requests[request_id]
                # Adjust the start time to use the reported timing of actual activity
                if 'fromCache' not in request and 'response' in request and \
                        'timing' in request['response'] and 'startTime' in request:
                    min_time = None
                    for key in request['response']['timing']:
                        value = float(request['response']['timing'][key])
                        if key != 'requestTime' and value >= 0:
                            value += request['startTime']
                            request['response']['timing'][key] = value
                            if min_time is None or value < min_time:
                                min_time = value
                    if min_time is not None and min_time > request['startTime']:
                        request['startTime'] = min_time
                    # Set the overall page start time
                    if 'startTime' not in page_data or \
                            request['startTime'] < page_data['startTime']:
                        page_data['startTime'] = request['startTime']
                if 'endTime' in request and request['endTime'] > page_data['endTime']:
                    page_data['endTime'] = request['endTime']
                if 'fromNet' in request and request['fromNet']:
                    net_requests.append(dict(request))
        # sort the requests by start time
        if len(net_requests):
            net_requests.sort(key=lambda x: x['startTime'] if 'startTime' in x else 0)
        return net_requests, page_data


    def process_requests(self, raw_requests, raw_page_data):
        """Process the raw requests into high-level requests"""
        self.result = {'pageData': {}, 'requests': []}
        if 'startTime' not in raw_page_data:
            raw_page_data['startTime'] = 0
        page_data = self.result['pageData']
        requests = self.result['requests']
        page_data['loadTime'] = 0
        page_data['docTime'] = 0
        page_data['fullyLoaded'] = 0
        page_data['bytesOut'] = 0
        page_data['bytesOutDoc'] = 0
        page_data['bytesIn'] = 0
        page_data['bytesInDoc'] = 0
        page_data['requests'] = 0
        page_data['requestsFull'] = 0
        page_data['requestsDoc'] = 0
        page_data['responses_200'] = 0
        page_data['responses_404'] = 0
        page_data['responses_other'] = 0
        page_data['result'] = 0
        page_data['testStartOffset'] = 0
        page_data['cached'] = 1 if self.cached else 0
        page_data['optimization_checked'] = 0
        page_data['start_epoch'] = raw_page_data['startTime']
        if 'main_frame' in raw_page_data:
            page_data['main_frame'] = raw_page_data['main_frame']
        if 'onload' in raw_page_data:
            page_data['loadTime'] = \
                int(round(raw_page_data['onload'] - raw_page_data['startTime']))
            page_data['docTime'] = page_data['loadTime']
            page_data['loadEventStart'] = page_data['loadTime']
            page_data['loadEventEnd'] = page_data['loadTime']
        if 'domContentLoadedEventStart' in raw_page_data:
            page_data['domContentLoadedEventStart'] = \
                int(round(raw_page_data['domContentLoadedEventStart'] -
                          raw_page_data['startTime']))
            if 'domContentLoadedEventEnd' in raw_page_data:
                page_data['domContentLoadedEventEnd'] = \
                    int(round(raw_page_data['domContentLoadedEventEnd'] -
                              raw_page_data['startTime']))
            else:
                page_data['domContentLoadedEventEnd'] = page_data['domContentLoadedEventStart']
        if 'loadEventStart' in raw_page_data:
            page_data['loadEventStart'] = \
                int(round(raw_page_data['loadEventStart'] - raw_page_data['startTime']))
            if 'loadEventEnd' in raw_page_data:
                page_data['loadEventEnd'] = \
                    int(round(raw_page_data['loadEventEnd'] - raw_page_data['startTime']))
            else:
                page_data['loadEventEnd'] = page_data['loadEventStart']
        # go through and pull out the requests, calculating the page stats as we go
        connections = {}
        dns_times = {}
        for raw_request in raw_requests:
            if 'url' in raw_request:
                url = raw_request['url'].split('#', 1)[0]
                parts = urlparse.urlsplit(url)
                request = {'type': 3, 'id': raw_request['id'], 'request_id': raw_request['id']}
                request['ip_addr'] = ''
                request['full_url'] = url
                request['is_secure'] = 1 if parts.scheme == 'https' else 0
                request['method'] = raw_request['method'] if 'method' in raw_request else ''
                request['host'] = parts.netloc
                request['url'] = parts.path
                if 'raw_id' in raw_request:
                    request['raw_id'] = raw_request['raw_id']
                if 'frame_id' in raw_request:
                    request['frame_id'] = raw_request['frame_id']
                if len(parts.query):
                    request['url'] += '?' + parts.query
                request['responseCode'] = -1
                if 'response' in raw_request and 'status' in raw_request['response']:
                    request['responseCode'] = raw_request['response']['status']
                request['load_ms'] = -1
                start_time = raw_request['startTime'] - raw_page_data['startTime']
                if 'response' in raw_request and 'timing' in raw_request['response'] and \
                        'sendStart' in raw_request['response']['timing'] and \
                        raw_request['response']['timing']['sendStart'] >= 0:
                    start_time = raw_request['response']['timing']['sendStart']
                    if 'fullyLoaded' not in page_data or start_time > page_data['fullyLoaded']:
                        page_data['fullyLoaded'] = int(round(start_time))
                if 'endTime' in raw_request:
                    end_time = raw_request['endTime'] - raw_page_data['startTime']
                    request['load_ms'] = int(round(end_time - start_time))
                    if 'fullyLoaded' not in page_data or end_time > page_data['fullyLoaded']:
                        page_data['fullyLoaded'] = int(round(end_time))
                request['ttfb_ms'] = -1
                if 'firstByteTime' in raw_request:
                    request['ttfb_ms'] = int(round(raw_request['firstByteTime'] -
                                                   raw_request['startTime']))
                request['load_start'] = int(round(start_time - raw_page_data['startTime']))
                request['load_start_float'] = start_time - raw_page_data['startTime']
                request['bytesIn'] = 0
                request['objectSize'] = ''
                if 'bytesIn' in raw_request:
                    request['bytesIn'] = int(round(raw_request['bytesIn']))
                if 'bytesInEncoded' in raw_request and raw_request['bytesInEncoded'] > 0:
                    request['objectSize'] = str(int(round(raw_request['bytesInEncoded'])))
                    request['bytesIn'] = int(round(raw_request['bytesInEncoded']))
                if 'bytesInData' in raw_request:
                    if request['objectSize'] == '':
                        request['objectSize'] = str(int(round(raw_request['bytesInData'])))
                    if request['bytesIn'] == 0:
                        request['bytesIn'] = int(round(raw_request['bytesInData']))
                    request['objectSizeUncompressed'] = int(round(raw_request['bytesInData']))
                if 'chunks' in raw_request:
                    request['chunks'] = []
                    for chunk in raw_request['chunks']:
                        ts = int(round(chunk['ts'] - raw_page_data['startTime']))
                        request['chunks'].append({'ts': ts, 'bytes': chunk['bytes']})

                # if we didn't get explicit bytes, fall back to any responses that
                # had content-length headers
                if request['bytesIn'] == 0 and 'response' in raw_request and \
                        'headers' in raw_request['response'] and \
                        'Content-Length' in raw_request['response']['headers']:
                    request['bytesIn'] = int(re.search(r'\d+', \
                        raw_request['response']['headers']['Content-Length']).group())
                request['expires'] = self.get_response_header(raw_request, \
                        'Expires').replace("\n", ", ").replace("\r", "")
                request['cacheControl'] = self.get_response_header(raw_request, \
                        'Cache-Control').replace("\n", ", ").replace("\r", "")
                request['contentType'] = self.get_response_header(raw_request, \
                        'Content-Type').split(';')[0]
                request['contentEncoding'] = self.get_response_header(raw_request, \
                        'Content-Encoding').replace("\n", ", ").replace("\r", "")
                object_size = self.get_response_header(raw_request, \
                        'Content-Length').split("\n")[0].replace("\r", "")
                if object_size is not None and len(object_size):
                    request['objectSize'] = object_size
                if request['objectSize'] is None or len(request['objectSize']) == 0:
                    request['objectSize'] = str(request['bytesIn'])
                if len(request['objectSize']):
                    request['objectSize'] = int(re.search(r'\d+', request['objectSize']).group())
                request['socket'] = -1
                if 'response' in raw_request and 'connectionId' in raw_request['response']:
                    request['socket'] = raw_request['response']['connectionId']
                if 'response' in raw_request and 'remoteIPAddress' in raw_request['response']:
                    request['ip_addr'] = raw_request['response']['remoteIPAddress']
                if 'response' in raw_request and 'protocol' in raw_request['response']:
                    request['protocol'] = raw_request['response']['protocol']
                    if request['protocol'] == 'h2':
                        request['protocol'] = 'HTTP/2'
                request['dns_start'] = -1
                request['dns_end'] = -1
                request['connect_start'] = -1
                request['connect_end'] = -1
                request['ssl_start'] = -1
                request['ssl_end'] = -1
                if 'response' in raw_request and 'timing' in raw_request['response']:
                    timing = raw_request['response']['timing']
                    if 'sendStart' in timing and 'receiveHeadersEnd' in timing and \
                            timing['receiveHeadersEnd'] >= timing['sendStart']:
                        request['ttfb_ms'] = int(round(timing['receiveHeadersEnd'] -
                                                       timing['sendStart']))
                        if request['load_ms'] >= 0:
                            request['load_ms'] = max(request['ttfb_ms'], request['load_ms'])
                    # Add the socket timing (always assigned to the first request on a connection)
                    if request['socket'] != -1 and request['socket'] not in connections:
                        connections[request['socket']] = timing
                        if 'dnsStart' in timing and 'dnsStart' >= 0:
                            dns_key = request['host']
                            if dns_key not in dns_times:
                                dns_times[dns_key] = True
                                request['dns_start'] = int(round(timing['dnsStart'] -
                                                                 raw_page_data['startTime']))
                                if 'dnsEnd' in timing and timing['dnsEnd'] >= 0:
                                    request['dns_end'] = int(round(timing['dnsEnd'] -
                                                                   raw_page_data['startTime']))
                        if 'connectStart' in timing and timing['connectStart'] >= 0:
                            request['connect_start'] = int(round(timing['connectStart'] -
                                                                 raw_page_data['startTime']))
                            if 'connectEnd' in timing and timing['connectEnd'] >= 0:
                                request['connect_end'] = int(round(timing['connectEnd'] -
                                                                   raw_page_data['startTime']))
                        if 'sslStart' in timing and timing['sslStart'] >= 0:
                            request['ssl_start'] = int(round(timing['sslStart'] -
                                                             raw_page_data['startTime']))
                            if request['connect_end'] > request['ssl_start']:
                                request['connect_end'] = request['ssl_start']
                            if 'sslEnd' in timing and timing['sslEnd'] >= 0:
                                request['ssl_end'] = int(round(timing['sslEnd'] -
                                                               raw_page_data['startTime']))
                            if 'securityDetails' in raw_request['response']:
                                request['securityDetails'] = \
                                    raw_request['response']['securityDetails']
                request['initiator'] = ''
                request['initiator_line'] = ''
                request['initiator_column'] = ''
                request['initiator_type'] = ''
                if 'initiator' in raw_request:
                    if 'type' in raw_request['initiator']:
                        request['initiator_type'] = raw_request['initiator']['type']
                    if 'url' in raw_request['initiator']:
                        request['initiator'] = raw_request['initiator']['url']
                        if 'lineNumber' in raw_request['initiator']:
                            request['initiator_line'] = raw_request['initiator']['lineNumber']
                    elif 'stack' in raw_request['initiator'] and \
                            'callFrames' in raw_request['initiator']['stack'] and \
                            raw_request['initiator']['stack']['callFrames']:
                        for frame in raw_request['initiator']['stack']['callFrames']:
                            if 'url' in frame and frame['url']:
                                request['initiator'] = frame['url']
                                if 'lineNumber' in frame:
                                    request['initiator_line'] = frame['lineNumber']
                                if 'columnNumber' in frame:
                                    request['initiator_column'] = frame['columnNumber']
                                if 'functionName' in frame and frame['functionName']:
                                    request['initiator_function'] = frame['functionName']
                                break
                            elif 'scriptId' in frame and frame['scriptId'] and frame['scriptId'] in self.script_ids:
                                request['initiator'] = self.script_ids[frame['scriptId']]
                                break
                if 'initialPriority' in raw_request:
                    request['priority'] = raw_request['initialPriority']
                    request['initial_priority'] = raw_request['initialPriority']
                request['server_rtt'] = None
                request['headers'] = {'request': [], 'response': []}
                if 'response' in raw_request and 'requestHeadersText' in raw_request['response']:
                    for line in raw_request['response']['requestHeadersText'].splitlines():
                        line = line.encode('utf-8').strip()
                        if len(line):
                            request['headers']['request'].append(line)
                elif 'response' in raw_request and 'requestHeaders' in raw_request['response']:
                    for key in raw_request['response']['requestHeaders']:
                        for value in raw_request['response']['requestHeaders'][key].splitlines():
                            try:
                                request['headers']['request'].append(\
                                    u'{0}: {1}'.format(key.encode('utf-8'),
                                                       value.encode('utf-8').strip()))
                            except Exception:
                                pass
                elif 'headers' in raw_request:
                    for key in raw_request['headers']:
                        for value in raw_request['headers'][key].splitlines():
                            try:
                                request['headers']['request'].append(\
                                    u'{0}: {1}'.format(key.encode('utf-8'),
                                                       value.encode('utf-8').strip()))
                            except Exception:
                                pass
                if 'response' in raw_request and 'headersText' in raw_request['response']:
                    for line in raw_request['response']['headersText'].splitlines():
                        line = line.encode('utf-8').strip()
                        if len(line):
                            request['headers']['response'].append(line)
                elif 'response' in raw_request and 'headers' in raw_request['response']:
                    for key in raw_request['response']['headers']:
                        for value in raw_request['response']['headers'][key].splitlines():
                            try:
                                request['headers']['response'].append(\
                                    u'{0}: {1}'.format(key.encode('utf-8'),
                                                       value.encode('utf-8').strip()))
                            except Exception:
                                pass
                request['bytesOut'] = len("\r\n".join(request['headers']['request']))
                request['score_cache'] = -1
                request['score_cdn'] = -1
                request['score_gzip'] = -1
                request['score_cookies'] = -1
                request['score_keep-alive'] = -1
                request['score_minify'] = -1
                request['score_combine'] = -1
                request['score_compress'] = -1
                request['score_etags'] = -1
                request['dns_ms'] = -1
                request['connect_ms'] = -1
                request['ssl_ms'] = -1
                request['gzip_total'] = None
                request['gzip_save'] = None
                request['minify_total'] = None
                request['minify_save'] = None
                request['image_total'] = None
                request['image_save'] = None
                request['cache_time'] = None
                request['cdn_provider'] = None
                request['server_count'] = None
                if 'URL' not in page_data and len(request['full_url']):
                    page_data['URL'] = request['full_url']
                if 'startTime' in raw_request:
                    start_offset = int(round(raw_request['startTime'] - \
                                             raw_page_data['startTime']))
                    if 'fullyLoaded' not in page_data or \
                            start_offset > page_data['fullyLoaded']:
                        page_data['fullyLoaded'] = start_offset
                if 'endTime' in raw_request:
                    end_offset = int(round(raw_request['endTime'] - \
                                           raw_page_data['startTime']))
                    if 'fullyLoaded' not in page_data or \
                            end_offset > page_data['fullyLoaded']:
                        page_data['fullyLoaded'] = end_offset
                if request['load_start'] >= 0:
                    requests.append(dict(request))
        page_data['connections'] = len(connections)
        if len(requests):
            requests.sort(key=lambda x: x['load_start_float'])

    def get_response_header(self, request, header):
        """Pull a specific header value from the response headers"""
        value = ''
        if 'response' in request and 'headers' in request['response']:
            headers = request['response']['headers']
            if header in headers:
                value = headers[header]
            else:
                for key in headers:
                    if key.lower() == header.lower():
                        value = headers[key]
                        break
        return value

    def mergeHeaders(self, dest, headers):
        """Merge the headers list into the dest array of existing headers"""
        for header in headers:
            key_len = header.find(':', 1)
            if key_len >= 0:
                key = header[:key_len]
                dupe = False
                for dest_header in dest:
                    key_len = dest_header.find(':', 1)
                    if key_len >= 0:
                        dest_key = dest_header[:key_len]
                        if dest_key == key:
                            dupe = True
                            break
                if not dupe:
                    dest.append(header)

    def process_netlog_requests(self):
        """Merge the data from the netlog requests file"""
        page_data = self.result['pageData']
        requests = self.result['requests']
        mapping = {'dns_start': 'dns_start',
                   'dns_end': 'dns_end',
                   'connect_start': 'connect_start',
                   'connect_end': 'connect_end',
                   'ssl_start': 'ssl_start',
                   'ssl_end': 'ssl_end',
                   'start': 'load_start',
                   'priority': 'priority',
                   'protocol': 'protocol',
                   'socket': 'socket',
                   'socket_group': 'socket_group',
                   'stream_id': 'http2_stream_id',
                   'parent_stream_id': 'http2_stream_dependency',
                   'weight': 'http2_stream_weight',
                   'exclusive': 'http2_stream_exclusive',
                   'chunks': 'chunks',
                   'chunks_in': 'chunks_in',
                   'chunks_out': 'chunks_out'}
        if self.netlog_requests_file is not None and os.path.isfile(self.netlog_requests_file):
            _, ext = os.path.splitext(self.netlog_requests_file)
            if ext.lower() == '.gz':
                f_in = gzip.open(self.netlog_requests_file, 'rb')
            else:
                f_in = open(self.netlog_requests_file, 'r')
            netlog = json.load(f_in)
            f_in.close()
            keep_requests = []
            for request in requests:
                if 'request_id' not in request and 'id' in request:
                    request['request_id'] = request['id']
                if 'full_url' in request:
                    matched = False
                    for entry in netlog:
                        if 'url' in entry and 'start' in entry and 'claimed' not in entry and \
                                entry['url'] == request['full_url']:
                            entry['claimed'] = True
                            for key in mapping:
                                try:
                                    if key in entry:
                                        if type(entry[key]) is list:
                                            request[mapping[key]] = entry[key]
                                        elif re.match(r'^\d+\.?(\d+)?$', str(entry[key]).strip()):
                                            request[mapping[key]] = \
                                                    int(round(float(str(entry[key]).strip())))
                                        else:
                                            request[mapping[key]] = str(entry[key])
                                except Exception:
                                    pass
                            if 'start' in entry:
                                request['load_start_float'] = float(str(entry['start']).strip())
                            if 'certificates' in entry:
                                request['certificates'] = entry['certificates']
                            if 'first_byte' in entry:
                                request['ttfb_ms'] = int(round(entry['first_byte'] -
                                                               entry['start']))
                            if 'end' in entry:
                                request['load_ms'] = int(round(entry['end'] -
                                                               entry['start']))
                            if 'pushed' in entry and entry['pushed']:
                                request['was_pushed'] = 1
                            if 'request_headers' in entry:
                                if 'headers' not in request:
                                    request['headers'] = {'request': [], 'response': []}
                                self.mergeHeaders(request['headers']['request'], entry['request_headers'])
                            if 'response_headers' in entry:
                                if 'headers' not in request:
                                    request['headers'] = {'request': [], 'response': []}
                                self.mergeHeaders(request['headers']['response'], entry['response_headers'])
                                for header in entry['response_headers']:
                                    matches = re.search(r'^HTTP\/1[^\s]+ (\d+)', header)
                                    if matches:
                                        request['responseCode'] = int(matches.group(1))
                                    matches = re.search(r'^:status: (\d+)', header)
                                    if matches:
                                        request['responseCode'] = int(matches.group(1))
                                    matches = re.search(r'^content-type: (.+)', header, re.IGNORECASE)
                                    if matches:
                                        request['contentType'] = matches.group(1).split(';')[0]
                                    matches = re.search(r'^cache-control: (.+)', header, re.IGNORECASE)
                                    if matches:
                                        request['cacheControl'] = matches.group(1)
                                    matches = re.search(r'^content-encoding: (.+)', header, re.IGNORECASE)
                                    if matches:
                                        request['contentEncoding'] = matches.group(1)
                                    matches = re.search(r'^expires: (.+)', header, re.IGNORECASE)
                                    if matches:
                                        request['expires'] = matches.group(1)
                            if 'bytes_in' in entry:
                                request['bytesIn'] = int(entry['bytes_in'])
                                request['objectSize'] = int(entry['bytes_in'])
                            if 'server_address' in entry:
                                parts = entry['server_address'].rsplit(':', 1)
                                if len(parts) == 2:
                                    request['ip_addr'] = parts[0]
                                    request['server_port'] = parts[1]
                            if 'client_address' in entry:
                                parts = entry['client_address'].rsplit(':', 1)
                                if len(parts) == 2:
                                    request['client_port'] = parts[1]
                            keep_requests.append(request)
                            break
            # Just keep the requests that had matching entries in the netlog
            self.result['requests'] = keep_requests
            requests = self.result['requests']

            # Add any requests we didn't know about
            index = 0
            for entry in netlog:
                if 'claimed' not in entry and 'url' in entry and 'start' in entry:
                    index += 1
                    request = {'type': 3, 'full_url': entry['url']}
                    parts = urlparse.urlsplit(entry['url'])
                    request['id'] = '99999.99999.{0:d}'.format(index)
                    request['is_secure'] = 1 if parts.scheme == 'https' else 0
                    request['host'] = parts.netloc
                    request['url'] = parts.path
                    if len(parts.query):
                        request['url'] += '?' + parts.query
                    request['responseCode'] = -1
                    request['score_cache'] = -1
                    request['score_cdn'] = -1
                    request['score_gzip'] = -1
                    request['score_cookies'] = -1
                    request['score_keep-alive'] = -1
                    request['score_minify'] = -1
                    request['score_combine'] = -1
                    request['score_compress'] = -1
                    request['score_etags'] = -1
                    request['dns_ms'] = -1
                    request['connect_ms'] = -1
                    request['ssl_ms'] = -1
                    request['gzip_total'] = None
                    request['gzip_save'] = None
                    request['minify_total'] = None
                    request['minify_save'] = None
                    request['image_total'] = None
                    request['image_save'] = None
                    request['cache_time'] = None
                    request['cdn_provider'] = None
                    request['server_count'] = None
                    request['type'] = 3
                    request['dns_start'] = -1
                    request['dns_end'] = -1
                    request['connect_start'] = -1
                    request['connect_end'] = -1
                    request['ssl_start'] = -1
                    request['ssl_end'] = -1
                    # See if we have a request ID for the phantom request
                    url = request['full_url']
                    if url in self.request_ids:
                        if len(self.request_ids[url]):
                            request['id'] = self.request_ids[url].pop(0)
                    if 'main_frame' in page_data:
                        request['frame_id'] = page_data['main_frame']
                    for key in mapping:
                        try:
                            if key in entry:
                                if type(entry[key]) is list:
                                    request[mapping[key]] = entry[key]
                                elif re.match(r'\d+\.?(\d+)?', str(entry[key])):
                                    request[mapping[key]] = int(round(float(entry[key])))
                                else:
                                    request[mapping[key]] = str(entry[key])
                        except Exception:
                            pass
                    if 'first_byte' in entry:
                        request['ttfb_ms'] = int(round(entry['first_byte'] -
                                                       entry['start']))
                    if 'end' in entry:
                        request['load_ms'] = int(round(entry['end'] -
                                                       entry['start']))
                    if 'pushed' in entry and entry['pushed']:
                        request['was_pushed'] = 1
                    if 'start' in entry:
                        request['load_start_float'] = float(str(entry['start']).strip())
                    request['headers'] = {'request': [], 'response': []}
                    if 'request_headers' in entry:
                        request['headers']['request'] = list(entry['request_headers'])
                    if 'response_headers' in entry:
                        request['headers']['response'] = list(entry['response_headers'])
                        for header in entry['response_headers']:
                            matches = re.search(r'^HTTP\/1[^\s]+ (\d+)', header)
                            if matches:
                                request['responseCode'] = int(matches.group(1))
                            matches = re.search(r'^:status: (\d+)', header)
                            if matches:
                                request['responseCode'] = int(matches.group(1))
                            matches = re.search(r'^content-type: (.+)', header, re.IGNORECASE)
                            if matches:
                                request['contentType'] = matches.group(1).split(';')[0]
                            matches = re.search(r'^cache-control: (.+)', header, re.IGNORECASE)
                            if matches:
                                request['cacheControl'] = matches.group(1)
                            matches = re.search(r'^content-encoding: (.+)', header, re.IGNORECASE)
                            if matches:
                                request['contentEncoding'] = matches.group(1)
                            matches = re.search(r'^expires: (.+)', header, re.IGNORECASE)
                            if matches:
                                request['expires'] = matches.group(1)
                    if 'bytes_in' in entry:
                        request['bytesIn'] = int(entry['bytes_in'])
                        request['objectSize'] = int(entry['bytes_in'])
                    if 'certificates' in entry:
                        request['certificates'] = entry['certificates']
                    if 'server_address' in entry:
                        parts = entry['server_address'].rsplit(':', 1)
                        if len(parts) == 2:
                            request['ip_addr'] = parts[0]
                            request['server_port'] = parts[1]
                    if 'client_address' in entry:
                        parts = entry['client_address'].rsplit(':', 1)
                        if len(parts) == 2:
                            request['client_port'] = parts[1]
                    request['bytesOut'] = 0
                    request['request_id'] = request['id']
                    request['raw_id'] = request['id']
                    requests.append(request)
        if len(requests):
            requests.sort(key=lambda x: x['load_start_float'])
        if 'main_frame' in page_data:
            index = 0
            main_request = None
            for request in requests:
                if main_request is None and 'full_url' in request and \
                        len(request['full_url']) and 'frame_id' in request and \
                        request['frame_id'] == page_data['main_frame'] and \
                        'responseCode' in request and \
                        (request['responseCode'] == 200 or request['responseCode'] == 304):
                    if 'contentType' not in request or \
                            (request['contentType'].find('ocsp-response') < 0 and \
                             request['contentType'].find('ca-cert') < 0):
                        main_request = request
                        request['final_base_page'] = True
                        request['is_base_page'] = True
                        page_data['final_base_page_request'] = index
                        page_data['final_base_page_request_id'] = request['id']
                        page_data['final_url'] = request['full_url']
                        page_data['URL'] = request['full_url']
                        break
                index += 1

    def process_page_data(self):
        """Walk through the sorted requests and generate the page-level stats"""
        page_data = self.result['pageData']
        requests = self.result['requests']
        page_data['bytesOut'] = 0
        page_data['bytesOutDoc'] = 0
        page_data['bytesIn'] = 0
        page_data['bytesInDoc'] = 0
        page_data['requests'] = 0
        page_data['requestsFull'] = 0
        page_data['requestsDoc'] = 0
        page_data['responses_200'] = 0
        page_data['responses_404'] = 0
        page_data['responses_other'] = 0
        page_data['fullyLoaded'] = page_data['docTime'] if 'docTime' in page_data else 0
        for request in requests:
            if 'TTFB' not in page_data and 'load_start' in request and 'ttfb_ms' in request and \
                    request['ttfb_ms'] >= 0 and 'responseCode' in request and \
                    (request['responseCode'] == 200 or request['responseCode'] == 304):
                if 'contentType' not in request or \
                        (request['contentType'].find('ocsp-response') < 0 and \
                            request['contentType'].find('ca-cert') < 0):
                    page_data['TTFB'] = int(round(request['load_start'] + request['ttfb_ms']))
                    if request['ssl_end'] >= request['ssl_start'] and \
                            request['ssl_start'] >= 0:
                        page_data['basePageSSLTime'] = int(round(request['ssl_end'] - \
                                                                 request['ssl_start']))
            if 'bytesOut' in request:
                page_data['bytesOut'] += request['bytesOut']
            if 'bytesIn' in request:
                page_data['bytesIn'] += request['bytesIn']
            page_data['requests'] += 1
            page_data['requestsFull'] += 1
            if request['load_start'] < page_data['docTime']:
                if 'bytesOut' in request:
                    page_data['bytesOutDoc'] += request['bytesOut']
                if 'bytesIn' in request:
                    page_data['bytesInDoc'] += request['bytesIn']
                page_data['requestsDoc'] += 1
            if 'responseCode' in request and request['responseCode'] == 200:
                page_data['responses_200'] += 1
            elif 'responseCode' in request and request['responseCode'] == 404:
                page_data['responses_404'] += 1
                page_data['result'] = 99999
            else:
                page_data['responses_other'] += 1
            if 'load_start' in request:
                end_time = request['load_start']
                if 'load_ms' in request:
                    end_time += request['load_ms']
                if end_time > page_data['fullyLoaded']:
                    page_data['fullyLoaded'] = end_time
        if page_data['responses_200'] == 0:
            if len(requests) > 0 and 'responseCode' in requests[0] and \
                    requests[0]['responseCode'] >= 400:
                page_data['result'] = requests[0]['responseCode']
            elif 'docTime' in page_data and page_data['docTime'] > 0:
                page_data['result'] = 0
            else:
                page_data['result'] = 12999

    def process_user_timing(self):
        """Walk through the sorted requests and generate the page-level stats"""
        page_data = self.result['pageData']
        if self.user_timing_file is not None and os.path.isfile(self.user_timing_file):
            _, ext = os.path.splitext(self.user_timing_file)
            if ext.lower() == '.gz':
                f_in = gzip.open(self.user_timing_file, 'rb')
            else:
                f_in = open(self.user_timing_file, 'r')
            user_timing_events = json.load(f_in)
            f_in.close()
            if user_timing_events:
                user_timing_events.sort(key=lambda x: x['ts'] if 'ts' in x else 0)
            main_frame = None
            navigation_start = None
            names = [
                'firstLayout',
                'firstPaint',
                'firstContentfulPaint',
                'firstTextPaint',
                'firstImagePaint',
                'firstMeaningfulPaint',
                'domInteractive',
                'domContentLoadedEventStart',
                'domContentLoadedEventEnd',
                'loadEventStart',
                'loadEventEnd'
            ]
            for event in user_timing_events:
                if 'args' in event and 'frame' in event['args'] and \
                        'name' in event and 'ts' in event:
                    if main_frame is None:
                        if event['name'] in ['navigationStart', 'fetchStart']:
                            main_frame = event['args']['frame']
                    if main_frame is not None and event['args']['frame'] == main_frame:
                        if navigation_start is None:
                            if event['name'] in ['navigationStart', 'fetchStart']:
                                navigation_start = event['ts']
                        else:
                            elapsed = int(round(float(event['ts'] - navigation_start) / 1000.0))
                            for name in names:
                                if event['name'] == name:
                                    page_data[name] = elapsed
                                    if name == 'loadEventStart':
                                        page_data['loadTime'] = elapsed
                                        page_data['docTime'] = elapsed

    def process_optimization_results(self):
        """Merge the data from the optimization checks file"""
        page_data = self.result['pageData']
        requests = self.result['requests']
        if self.optimization is not None and os.path.isfile(self.optimization):
            _, ext = os.path.splitext(self.optimization)
            if ext.lower() == '.gz':
                f_in = gzip.open(self.optimization, 'rb')
            else:
                f_in = open(self.optimization, 'r')
            optimization_results = json.load(f_in)
            f_in.close()
            page_data['score_cache'] = -1
            page_data['score_cdn'] = -1
            page_data['score_gzip'] = -1
            page_data['score_cookies'] = -1
            page_data['score_keep-alive'] = -1
            page_data['score_minify'] = -1
            page_data['score_combine'] = -1
            page_data['score_compress'] = -1
            page_data['score_etags'] = -1
            page_data['score_progressive_jpeg'] = -1
            page_data['gzip_total'] = 0
            page_data['gzip_savings'] = 0
            page_data['minify_total'] = -1
            page_data['minify_savings'] = -1
            page_data['image_total'] = 0
            page_data['image_savings'] = 0
            page_data['optimization_checked'] = 1
            page_data['base_page_cdn'] = ''
            cache_count = 0
            cache_total = 0
            cdn_count = 0
            cdn_total = 0
            keep_alive_count = 0
            keep_alive_total = 0
            progressive_total_bytes = 0
            progressive_bytes = 0
            for request in requests:
                if request['responseCode'] == 200:
                    request_id = str(request['id'])
                    pos = request_id.find('-')
                    if pos > 0:
                        request_id = request_id[:pos]
                    if request_id in optimization_results:
                        opt = optimization_results[request_id]
                        if 'cache' in opt:
                            request['score_cache'] = opt['cache']['score']
                            request['cache_time'] = opt['cache']['time']
                            if request['score_cache'] >= 0:
                                cache_count += 1
                                cache_total += request['score_cache']
                        if 'cdn' in opt:
                            request['score_cdn'] = opt['cdn']['score']
                            request['cdn_provider'] = opt['cdn']['provider']
                            if request['score_cdn'] >= 0:
                                cdn_count += 1
                                cdn_total += request['score_cdn']
                            if 'is_base_page' in request and request['is_base_page'] and \
                                    request['cdn_provider'] is not None:
                                page_data['base_page_cdn'] = request['cdn_provider']
                        if 'keep_alive' in opt:
                            request['score_keep-alive'] = opt['keep_alive']['score']
                            if request['score_keep-alive'] >= 0:
                                keep_alive_count += 1
                                keep_alive_total += request['score_keep-alive']
                        if 'gzip' in opt:
                            savings = opt['gzip']['size'] - opt['gzip']['target_size']
                            request['score_gzip'] = opt['gzip']['score']
                            request['gzip_total'] = opt['gzip']['size']
                            request['gzip_save'] = savings
                            if request['score_gzip'] >= 0:
                                page_data['gzip_total'] += opt['gzip']['size']
                                page_data['gzip_savings'] += savings
                        if 'image' in opt:
                            savings = opt['image']['size'] - opt['image']['target_size']
                            request['score_compress'] = opt['image']['score']
                            request['image_total'] = opt['image']['size']
                            request['image_save'] = savings
                            if request['score_compress'] >= 0:
                                page_data['image_total'] += opt['image']['size']
                                page_data['image_savings'] += savings
                        if 'progressive' in opt:
                            size = opt['progressive']['size']
                            request['jpeg_scan_count'] = opt['progressive']['scan_count']
                            progressive_total_bytes += size
                            if request['jpeg_scan_count'] > 1:
                                request['score_progressive_jpeg'] = 100
                                progressive_bytes += size
                            elif size < 10240:
                                request['score_progressive_jpeg'] = 50
                            else:
                                request['score_progressive_jpeg'] = 0
            if cache_count > 0:
                page_data['score_cache'] = int(round(cache_total / cache_count))
            if cdn_count > 0:
                page_data['score_cdn'] = int(round(cdn_total / cdn_count))
            if keep_alive_count > 0:
                page_data['score_keep-alive'] = int(round(keep_alive_total / keep_alive_count))
            if page_data['gzip_total'] > 0:
                page_data['score_gzip'] = 100 - int(page_data['gzip_savings'] * 100 /
                                                    page_data['gzip_total'])
            if page_data['image_total'] > 0:
                page_data['score_compress'] = 100 - int(page_data['image_savings'] * 100 /
                                                        page_data['image_total'])
            if progressive_total_bytes > 0:
                page_data['score_progressive_jpeg'] = int(round(progressive_bytes * 100 /
                                                                progressive_total_bytes))

    def process_code_coverage(self):
        """Merge the data from the code coverage file"""
        try:
            page_data = self.result['pageData']
            requests = self.result['requests']
            if self.coverage is not None and os.path.isfile(self.coverage):
                _, ext = os.path.splitext(self.coverage)
                if ext.lower() == '.gz':
                    f_in = gzip.open(self.coverage, 'rb')
                else:
                    f_in = open(self.coverage, 'r')
                coverage = json.load(f_in)
                f_in.close()
                if coverage:
                    categories = ['JS', 'CSS']
                    page_coverage = {}
                    for category in categories:
                        page_coverage['{0}_bytes'.format(category)] = 0
                        page_coverage['{0}_bytes_used'.format(category)] = 0
                        page_coverage['{0}_percent_used'.format(category)] = 100.0
                    valid = False
                    for url in coverage:
                        for category in categories:
                            total = '{0}_bytes'.format(category)
                            used = '{0}_bytes_used'.format(category)
                            if total in coverage[url]:
                                page_coverage[total] += coverage[url][total]
                                valid = True
                            if used in coverage[url]:
                                page_coverage[used] += coverage[url][used]
                                valid = True
                        for request in requests:
                            if 'full_url' in request and request['full_url'] == url:
                                request['code_coverage'] = dict(coverage[url])
                    if valid:
                        for category in categories:
                            total = '{0}_bytes'.format(category)
                            used = '{0}_bytes_used'.format(category)
                            pct = '{0}_percent_used'.format(category)
                            if page_coverage[total] > 0:
                                page_coverage[pct] = float((page_coverage[used] * 10000) \
                                        / page_coverage[total]) / 100.0
                        page_data['code_coverage'] = dict(page_coverage)
        except Exception:
            pass

    def process_cpu_times(self):
        """Calculate the main thread CPU times from the time slices file"""
        try:
            import math
            page_data = self.result['pageData']
            if 'fullyLoaded' in page_data and page_data['fullyLoaded']:
                end = page_data['fullyLoaded']
            doc = page_data['docTime'] if 'docTime' in page_data else 0
            if end > 0 and self.cpu_times is not None and os.path.isfile(self.cpu_times):
                _, ext = os.path.splitext(self.cpu_times)
                if ext.lower() == '.gz':
                    f_in = gzip.open(self.cpu_times, 'rb')
                else:
                    f_in = open(self.cpu_times, 'r')
                cpu = json.load(f_in)
                f_in.close()
                if cpu and 'main_thread' in cpu and 'slices' in cpu and \
                        cpu['main_thread'] in cpu['slices'] and 'slice_usecs' in cpu:
                    busy = 0
                    busy_doc = 0
                    usecs = cpu['slice_usecs']
                    page_data['cpuTimes'] = {}
                    page_data['cpuTimesDoc'] = {}
                    all_slices = cpu['slices'][cpu['main_thread']]
                    for name in all_slices:
                        page_data['cpuTimes'][name] = 0
                        page_data['cpuTimesDoc'][name] = 0
                        slices = all_slices[name]
                        last_slice = min(int(math.ceil((end * 1000) / usecs)), len(slices))
                        for index in xrange(last_slice):
                            slice_time = float(slices[index]) / 1000.0
                            page_data['cpuTimes'][name] += slice_time
                            busy += slice_time
                            if index * usecs < doc * 1000:
                                page_data['cpuTimesDoc'][name] += slice_time
                                busy_doc += slice_time
                    page_data['cpuTimes'][u'Idle'] = max(end - busy, 0)
                    page_data['cpuTimesDoc'][u'Idle'] = max(doc - busy_doc, 0)
                    # round everything to the closest int
                    for category in ['cpuTimes', 'cpuTimesDoc']:
                        for name in page_data[category]:
                            page_data[category][name] = int(round(page_data[category][name]))
                    # Create top-level cpu entries as well
                    for name in page_data['cpuTimes']:
                        entry = u'cpu.{0}'.format(name)
                        page_data[entry] = page_data['cpuTimes'][name]
                    pass
        except Exception:
            pass

def main():
    """Main entry point"""
    import argparse
    parser = argparse.ArgumentParser(description='Chrome trace parser.',
                                     prog='trace-parser')
    parser.add_argument('-v', '--verbose', action='count',
                        help="Increase verbosity (specify multiple times for more)" \
                             ". -vvvv for full debug output.")
    parser.add_argument('-d', '--devtools', help="Input devtools file.")
    parser.add_argument('-n', '--netlog', help="Input netlog requests file (optional).")
    parser.add_argument('-p', '--optimization', help="Input optimization results file (optional).")
    parser.add_argument('-u', '--user', help="Input user timing file (optional).")
    parser.add_argument('--coverage', help="Input code coverage file (optional).")
    parser.add_argument('--cpu', help="Input cpu time slices file (optional).")
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

    if not options.devtools or not options.out:
        parser.error("Input devtools or output file is not specified.")

    start = time.time()
    opt = {'devtools': options.devtools,
           'netlog': options.netlog,
           'optimization': options.optimization,
           'user': options.user,
           'coverage': options.coverage,
           'cpu': options.cpu,
           'cached': options.cached,
           'out': options.out}
    devtools = DevToolsParser(opt)
    devtools.process()
    end = time.time()
    elapsed = end - start
    logging.debug("Devtools processing time: %0.3f", elapsed)

if __name__ == '__main__':
    #  import cProfile
    #  cProfile.run('main()', None, 2)
    main()
