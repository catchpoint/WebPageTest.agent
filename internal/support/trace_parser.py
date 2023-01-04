#!/usr/bin/env python
"""
Copyright 2019 WebPageTest LLC.
Copyright 2016 Google Inc.
Copyright 2020 Catchpoint Systems Inc.
Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
found in the LICENSE.md file.
"""
import gzip
import logging
import math
import os
import re
import sys
import time
if (sys.version_info >= (3, 0)):
    from urllib.parse import urlparse # pylint: disable=import-error
    unicode = str
    GZIP_TEXT = 'wt'
    GZIP_READ_TEXT = 'rt'
else:
    from urlparse import urlparse # pylint: disable=import-error
    GZIP_TEXT = 'w'
    GZIP_READ_TEXT = 'r'

# try a fast json parser if it is installed
try:
    import ujson as json
except BaseException:
    import json

##########################################################################
#   Trace processing
##########################################################################
class Trace():
    """Main class"""
    def __init__(self):
        self.thread_stack = {}
        self.ignore_threads = {}
        self.threads = {}
        self.user_timing = []
        self.event_names = {}
        self.event_name_lookup = {}
        self.scripts = None
        self.timeline_events = []
        self.timeline_requests = {}
        self.trace_events = []
        self.interactive = None
        self.long_tasks = None
        self.interactive_start = 0
        self.interactive_end = None
        self.start_time = None
        self.marked_start_time = None
        self.end_time = None
        self.cpu = {'main_thread': None, 'main_threads':[], 'subframes': [], 'valid': False}
        self.page_data = {'values': {}, 'times': {}}
        self.feature_usage = None
        self.feature_usage_start_time = None
        self.netlog = {'bytes_in': 0, 'bytes_out': 0, 'next_request_id': 1000000}
        self.netlog_requests = None
        self.netlog_event_types = {}
        self.v8stats = None
        self.v8stack = {}
        self.PRIORITY_MAP = {
            "VeryHigh": "Highest",
            "HIGHEST": "Highest",
            "MEDIUM": "High",
            "LOW": "Medium",
            "LOWEST": "Low",
            "IDLE": "Lowest",
            "VeryLow": "Lowest"
        }
        return

    ##########################################################################
    #   Output Logging
    ##########################################################################
    def write_json(self, out_file, json_data):
        """Write out one of the internal structures as a json blob"""
        try:
            _, ext = os.path.splitext(out_file)
            if ext.lower() == '.gz':
                with gzip.open(out_file, GZIP_TEXT) as f:
                    json.dump(json_data, f)
            else:
                with open(out_file, 'w') as f:
                    json.dump(json_data, f)
        except BaseException:
            logging.exception("Error writing to " + out_file)

    def WriteUserTiming(self, out_file, dom_tree=None, performance_timing=None):
        self.post_process_netlog_events()
        out = self.post_process_user_timing(dom_tree, performance_timing)
        if out is not None:
            self.write_json(out_file, out)

    def WriteCPUSlices(self, out_file):
        if self.cpu['valid']:
            self.write_json(out_file, self.cpu)

    def WriteScriptTimings(self, out_file):
        if self.scripts is not None:
            self.write_json(out_file, self.scripts)

    def WriteFeatureUsage(self, out_file):
        self.post_process_netlog_events()
        out = self.post_process_feature_usage()
        if out is not None:
            self.write_json(out_file, out)
    
    def WritePageData(self, out_file):
        if len(self.page_data) and self.start_time is not None:
            out = {}
            for key in self.page_data['values']:
                out[key] = self.page_data['values'][key]
            for key in self.page_data['times']:
                value = self.page_data['times'][key]
                if value >= self.start_time:
                    out[key] = int(round(float(value - self.start_time) / 1000.0))
            if len(out):
                self.write_json(out_file, out)

    def WriteInteractive(self, out_file):
        # Generate the interactive periods from the long-task data
        if self.end_time and self.start_time and self.long_tasks is not None:
            interactive = []
            end_time = int(math.ceil(float(self.end_time - self.start_time) / 1000.0))
            if not self.long_tasks:
                interactive.append([0, end_time])
            else:
                last_end = 0
                for task in self.long_tasks:
                    elapsed = task[0] - last_end
                    if elapsed > 0:
                        interactive.append([last_end, task[0]])
                    last_end = task[1]
                elapsed = end_time - last_end
                if elapsed > 0:
                    interactive.append([last_end, end_time])
            self.write_json(out_file, interactive)
    
    def WriteLongTasks(self, out_file):
        if self.long_tasks is not None:
            self.write_json(out_file, self.long_tasks)

    def WriteNetlog(self, out_file):
        out = self.post_process_netlog_events()
        if out is not None:
            self.write_json(out_file, out)
    
    def WriteTimelineRequests(self, out_file):
        if self.timeline_requests:
            self.write_json(out_file, self.timeline_requests)

    def WriteV8Stats(self, out_file):
        if self.v8stats is not None:
            self.v8stats["main_thread"] = self.cpu['main_thread']
            self.v8stats["main_threads"] = self.cpu['main_threads']
            self.write_json(out_file, self.v8stats)

    ##########################################################################
    #   Top-level processing
    ##########################################################################
    def Process(self, trace):
        f = None
        line_mode = False
        self.__init__()
        logging.debug("Loading trace: %s", trace)
        try:
            _, ext = os.path.splitext(trace)
            if ext.lower() == '.gz':
                f = gzip.open(trace, GZIP_READ_TEXT)
            else:
                f = open(trace, 'r')
            for line in f:
                try:
                    trace_event = json.loads(line.strip("\r\n\t ,"))
                    if not line_mode and 'traceEvents' in trace_event:
                        for sub_event in trace_event['traceEvents']:
                            self.FilterTraceEvent(sub_event)
                    else:
                        line_mode = True
                        self.FilterTraceEvent(trace_event)
                except BaseException:
                    logging.exception('Error processing trace line')
        except BaseException:
            logging.exception("Error processing trace " + trace)
        if f is not None:
            f.close()
        self.ProcessTraceEvents()

    def ProcessTimeline(self, timeline):
        self.__init__()
        self.cpu['main_thread'] = '0'
        self.threads['0'] = {}
        events = None
        f = None
        try:
            _, ext = os.path.splitext(timeline)
            if ext.lower() == '.gz':
                f = gzip.open(timeline, GZIP_READ_TEXT)
            else:
                f = open(timeline, 'r')
            events = json.load(f)
            if events:
                # convert the old format timeline events into our internal
                # representation
                for event in events:
                    if 'method' in event and 'params' in event:
                        if self.start_time is None:
                            if event['method'] == 'Network.requestWillBeSent' and \
                                    'timestamp' in event['params']:
                                self.start_time = event['params']['timestamp'] * 1000000.0
                                self.end_time = event['params']['timestamp'] * 1000000.0
                        else:
                            if 'timestamp' in event['params']:
                                t = event['params']['timestamp'] * 1000000.0
                                if t > self.end_time:
                                    self.end_time = t
                            if event['method'] == 'Timeline.eventRecorded' and \
                                    'record' in event['params']:
                                e = self.ProcessOldTimelineEvent(
                                    event['params']['record'], None)
                                if e is not None:
                                    self.timeline_events.append(e)
                self.ProcessTimelineEvents()
        except BaseException:
            logging.exception("Error processing timeline " + timeline)
        if f is not None:
            f.close()

    def FilterTraceEvent(self, trace_event):
        cat = trace_event['cat']
        if cat == 'toplevel' or cat == 'ipc,toplevel':
            return
        if cat == 'devtools.timeline' or \
                cat == '__metadata' or \
                cat.find('devtools.timeline') >= 0 or \
                cat.find('blink.feature_usage') >= 0 or \
                cat.find('blink.user_timing') >= 0 or \
                cat.find('blink.resource') >= 0 or \
                cat.find('loading') >= 0 or \
                cat.find('navigation') >= 0 or \
                cat.find('rail') >= 0 or \
                cat.find('netlog') >= 0 or \
                cat.find('v8') >= 0:
            self.trace_events.append(trace_event)

    def ProcessTraceEvents(self):
        # sort the raw trace events by timestamp and then process them
        if len(self.trace_events):
            logging.debug("Sorting %d trace events", len(self.trace_events))
            self.trace_events.sort(key=lambda trace_event: trace_event['ts'])
            logging.debug("Processing trace events")
            for trace_event in self.trace_events:
                self.ProcessTraceEvent(trace_event)
            self.trace_events = []

        # Post-process the netlog events (may shift the start time)
        logging.debug("Processing netlog events")
        self.post_process_netlog_events()
        # Do the post-processing on timeline events
        logging.debug("Processing timeline events")
        self.ProcessTimelineEvents()
        logging.debug("Done processing trace events")      

    def ProcessTraceEvent(self, trace_event):
        cat = trace_event['cat']
        if 'ts' in trace_event:
            trace_event['ts'] = int(trace_event['ts'])
        if cat.find('blink.user_timing') >= 0 or cat.find('rail') >= 0 or \
                cat.find('loading') >= 0 or cat.find('navigation') >= 0:
            keep = False
            if 'args' in trace_event and \
                    'data' in trace_event['args'] and \
                    'inMainFrame' in trace_event['args']['data'] and \
                    trace_event['args']['data']['inMainFrame']:
                keep = True
            elif 'args' in trace_event and 'frame' in trace_event['args']:
                keep = True
            elif 'name' in trace_event and trace_event['name'] in [
                    'navigationStart', 'unloadEventStart', 'redirectStart', 'domLoading']:
                keep = True
            if keep:
                self.user_timing.append(trace_event)
            if self.marked_start_time is None and \
                    'name' in trace_event and \
                    trace_event['name'].find('navigationStart') >= 0:
                if self.start_time is None or trace_event['ts'] < self.start_time:
                    self.start_time = trace_event['ts']
            if self.cpu['main_thread'] is None and 'name' in trace_event and \
                    trace_event['name'] in ['navigationStart', 'fetchStart']:
                thread = '{0}:{1}'.format(trace_event['pid'], trace_event['tid'])
                self.cpu['main_thread'] = thread
                if thread not in self.cpu['main_threads']:
                    self.cpu['main_threads'].append(thread)
            if 'args' in trace_event and \
                    'data' in trace_event['args'] and \
                    'inMainFrame' in trace_event['args']['data'] and \
                    trace_event['args']['data']['inMainFrame']:
                thread = '{0}:{1}'.format(trace_event['pid'], trace_event['tid'])
                if thread not in self.cpu['main_threads']:
                    self.cpu['main_threads'].append(thread)
        if cat == '__metadata' and 'name' in trace_event and \
                trace_event['name'] == 'process_labels' and \
                'pid' in trace_event and 'args' in trace_event and \
                'labels' in trace_event['args'] and \
                trace_event['args']['labels'].startswith('Subframe:'):
            self.cpu['subframes'].append(str(trace_event['pid']))
        if cat == '__metadata' and 'name' in trace_event and \
                trace_event['name'] == 'thread_name' and \
                'args' in trace_event and \
                'name' in trace_event['args'] and \
                trace_event['args']['name'] == 'CrRendererMain':
            thread = '{0}:{1}'.format(trace_event['pid'], trace_event['tid'])
            if thread not in self.cpu['main_threads']:
                self.cpu['main_threads'].append(thread)
        if cat == 'netlog' or cat.find('netlog') >= 0:
            self.ProcessNetlogEvent(trace_event)
        elif cat == 'devtools.timeline' or cat.find('devtools.timeline') >= 0 or cat.find('blink.resource') >= 0:
            self.ProcessTimelineTraceEvent(trace_event)
        elif cat.find('blink.feature_usage') >= 0:
            self.ProcessFeatureUsageEvent(trace_event)
        elif cat.find('content') >= 0:
            self.ProcessContentEvent(trace_event)
        if cat.find('v8') >= 0:
            self.ProcessV8Event(trace_event)
    
    def post_process_user_timing(self, dom_tree, performance_timing):
        out = None
        if self.user_timing is not None:
            self.user_timing.sort(key=lambda trace_event: trace_event['ts'])
            out = []
            candidates = {}
            lcp_event = None
            for event in self.user_timing:
                try:
                    consumed = False
                    if event['cat'].find('loading') >= 0 and 'name' in event:
                        event['name'] = event['name'].replace('GlobalFirstContentfulPaint', 'FirstContentfulPaint')
                        if event['name'].startswith('NavStartToLargestContentfulPaint'):
                            consumed = True
                            if event['name'].find('Invalidate') >= 0:
                                lcp_event = None
                            elif event['name'].find('Candidate') >= 0:
                                lcp_event = dict(event)
                        elif event['name'].find('::') >= 0:
                            consumed = True
                            (name, trigger) = event['name'].split('::', 1)
                            name = name[:1].upper() + name[1:]
                            event['name'] = name
                            key = name
                            try:
                                if 'args' in event:
                                    if 'frame' in event['args']:
                                        key += ':' + event['args']['frame']
                                    if 'data' in event['args'] and 'candidateIndex' in event['args']['data']:
                                        if isinstance(event['args']['data']['candidateIndex'], int):
                                            key += '.{0:d}'.format(event['args']['data']['candidateIndex'])
                                        elif isinstance(event['args']['data']['candidateIndex'], str):
                                            key += '.' + event['args']['data']['candidateIndex']
                            except Exception:
                                logging.exception('Error processing user timing event key')
                            if trigger == 'Candidate':
                                candidates[key] = dict(event)
                            elif trigger == 'Invalidate' and key in candidates:
                                del candidates[key]
                    if not consumed:
                        out.append(event)
                except Exception:
                    logging.exception('Error processing user timing event')
            has_lcp = False
            for name in candidates:
                if name.startswith('LargestContentfulPaint'):
                    has_lcp = True
                    break
            if lcp_event is not None and not has_lcp:
                lcp_event['name'] = 'LargestContentfulPaint'
                out.append(lcp_event)
            for name in candidates:
                out.append(candidates[name])
            if dom_tree is not None:
                for event in out:
                    if 'args' in event and 'data' in event['args'] and 'DOMNodeId' in event['args']['data']:
                        node_info = self.FindDomNodeInfo(dom_tree, event['args']['data']['DOMNodeId'])
                        if node_info is not None:
                            event['args']['data']['node'] = node_info
            if performance_timing:
                for event in out:
                    if 'args' in event and 'data' in event['args']:
                        if 'size' in event['args']['data'] and event['name'].startswith('LargestContentfulPaint'):
                            for perf_entry in performance_timing:
                                if 'entryType' in perf_entry and perf_entry['entryType'] == 'largest-contentful-paint' and 'size' in perf_entry and perf_entry['size'] == event['args']['data']['size'] and 'consumed' not in perf_entry:
                                    perf_entry['consumed'] = True
                                    if 'url' in perf_entry and len(perf_entry['url']) and 'url' not in event['args']['data']:
                                        event['args']['data']['url'] = perf_entry['url']
                                    if 'element' in perf_entry:
                                        event['args']['data']['element'] = perf_entry['element']
                        elif 'score' in event['args']['data'] and event['name'].startswith('LayoutShift'):
                            for perf_entry in performance_timing:
                                if 'entryType' in perf_entry and perf_entry['entryType'] == 'layout-shift' and 'value' in perf_entry and perf_entry['value'] == event['args']['data']['score'] and 'consumed' not in perf_entry:
                                    perf_entry['consumed'] = True
                                    if 'sources' in perf_entry:
                                        event['args']['data']['sources'] = perf_entry['sources']
            out.append({'startTime': self.start_time})
        return out

    def FindDomNodeInfo(self, dom_tree, node_id):
        """Get the information for the given DOM node"""
        info = None
        try:
            if dom_tree is not None:
                if 'documents' in dom_tree:
                    node_index = None
                    for document in dom_tree['documents']:
                        if 'nodes' in document and node_index is None:
                            if 'backendNodeId' in document['nodes']:
                                for index in range(len(document['nodes']['backendNodeId'])):
                                    if document['nodes']['backendNodeId'][index] == node_id:
                                        node_index = index
                                        break
                            if node_index is not None:
                                info = {}
                                if 'strings' in dom_tree:
                                    if 'nodeName' in document['nodes'] and node_index < len(document['nodes']['nodeName']):
                                        string_index = document['nodes']['nodeName'][node_index]
                                        if string_index >= 0 and string_index < len(dom_tree['strings']):
                                            info['nodeType'] = dom_tree['strings'][string_index]
                                    if 'nodeValue' in document['nodes'] and node_index < len(document['nodes']['nodeValue']):
                                        string_index = document['nodes']['nodeValue'][node_index]
                                        if string_index >= 0 and string_index < len(dom_tree['strings']):
                                            info['nodeValue'] = dom_tree['strings'][string_index]
                                    if 'attributes' in document['nodes'] and node_index < len(document['nodes']['attributes']):
                                        attribute = None
                                        for string_index in document['nodes']['attributes'][node_index]:
                                            string_value = ''
                                            if string_index >= 0 and string_index < len(dom_tree['strings']):
                                                string_value = dom_tree['strings'][string_index]
                                            if attribute is None:
                                                attribute = string_value
                                            else:
                                                if attribute:
                                                    if 'attributes' not in info:
                                                        info['attributes'] = {}
                                                    if attribute in info['attributes']:
                                                        info['attributes'][attribute] += ' ' + string_value
                                                    else:
                                                        info['attributes'][attribute] = string_value
                                                attribute = None
                                    if 'currentSourceURL' in document['nodes']:
                                        if 'index' in document['nodes']['currentSourceURL'] and 'value' in document['nodes']['currentSourceURL']:
                                            for index in range(len(document['nodes']['currentSourceURL']['index'])):
                                                if document['nodes']['currentSourceURL']['index'][index] == node_index:
                                                    if index < len(document['nodes']['currentSourceURL']['value']):
                                                        string_index = document['nodes']['currentSourceURL']['value'][index]
                                                        if string_index >= 0 and string_index < len(dom_tree['strings']):
                                                            info['sourceURL'] = dom_tree['strings'][string_index]
                                                    break
                                if 'layout' in document:
                                    if 'nodeIndex' in document['layout']:
                                        for index in range(len(document['layout']['nodeIndex'])):
                                            if document['layout']['nodeIndex'][index] == node_index:
                                                if 'bounds' in document['layout'] and index < len(document['layout']['bounds']):
                                                    info['bounds'] = document['layout']['bounds'][index]
                                                if 'text' in document['layout'] and index < len(document['layout']['text']):
                                                    string_index = document['layout']['text'][index]
                                                    if string_index >= 0 and string_index < len(dom_tree['strings']):
                                                        info['layoutText'] = dom_tree['strings'][string_index]
                                                if 'style_names' in dom_tree and 'styles' in document['layout'] and index < len(document['layout']['styles']) and len(document['layout']['styles'][index]) == len(dom_tree['style_names']):
                                                    if 'styles' not in info:
                                                        info['styles'] = {}
                                                    for style_index in range(len(document['layout']['styles'][index])):
                                                        string_index = document['layout']['styles'][index][style_index]
                                                        if string_index >= 0 and string_index < len(dom_tree['strings']):
                                                            info['styles'][dom_tree['style_names'][style_index]] = dom_tree['strings'][string_index]
                                return info
                                            
        except Exception:
            logging.exception("Error looking up DOM Node")
        return info

    ##########################################################################
    #   Timeline
    ##########################################################################
    def ProcessTimelineTraceEvent(self, trace_event):
        thread = '{0}:{1}'.format(trace_event['pid'], trace_event['tid'])
        if trace_event['name'] == 'thread_name' and \
                'args' in trace_event and \
                'name' in trace_event['args'] and \
                trace_event['args']['name'] == 'CrRendererMain' and \
                thread not in self.cpu['main_threads']:
            self.cpu['main_threads'].append(thread)
        
        # Watch for the marker indicating the start time
        if trace_event['name'] == 'ResourceSendRequest' and \
                'args' in trace_event and \
                'data' in trace_event['args'] and \
                'url' in trace_event['args']['data'] and \
                trace_event['args']['data']['url'] == 'http://127.0.0.1:8888/wpt-start-recording':
            self.marked_start_time = trace_event['ts']
            self.start_time = trace_event['ts']

        # Keep track of the main thread
        if 'args' in trace_event and 'data' in trace_event['args'] and \
                thread not in self.ignore_threads:
            if 'url' in trace_event['args']['data'] and \
                    trace_event['args']['data']['url'].startswith('http://127.0.0.1:8888'):
                self.ignore_threads[thread] = True
            if self.cpu['main_thread'] is None or 'isMainFrame' in trace_event['args']['data']:
                if ('isMainFrame' in trace_event['args']['data'] and \
                     trace_event['args']['data']['isMainFrame']) or \
                   (trace_event['name'] == 'ResourceSendRequest' and \
                    'url' in trace_event['args']['data']):
                    if thread not in self.threads:
                        self.threads[thread] = {}
                    if self.marked_start_time is None:
                        if self.start_time is None or trace_event['ts'] < self.start_time:
                            self.start_time = trace_event['ts']
                    self.cpu['main_thread'] = thread
                    if thread not in self.cpu['main_threads']:
                        self.cpu['main_threads'].append(thread)
                    if 'dur' not in trace_event:
                        trace_event['dur'] = 1

        # Make sure each thread has a numerical ID
        if self.cpu['main_thread'] is not None and \
                thread not in self.threads and \
                thread not in self.ignore_threads and \
                trace_event['name'] != 'Program':
            self.threads[thread] = {}
        
        # Keep track of request events reported by the timeline
        request_id = None
        has_id = False
        if 'args' in trace_event and 'data' in trace_event['args'] and 'requestId' in trace_event['args']['data']:
            request_id = trace_event['args']['data']['requestId']
            has_id = True
        elif 'args' in trace_event and 'url' in trace_event['args']:
            request_id = trace_event['args']['url']
        if request_id is not None:
            if request_id not in self.timeline_requests:
                self.timeline_requests[request_id] = {}
            request = self.timeline_requests[request_id]
            request['has_id'] = has_id
            if 'args' in trace_event and 'url' in trace_event['args']:
                request['url'] = trace_event['args']['url']
            if trace_event['name'] == 'Network.requestIntercepted':
                if 'overwrittenURL' in trace_event['args']['data']:
                    request['overwrittenURL'] = trace_event['args']['data']['overwrittenURL']
            if trace_event['name'] == 'ResourceSendRequest':
                if 'priority' in trace_event['args']['data']:
                    request['priority'] = trace_event['args']['data']['priority']
                    if request['priority'] in self.PRIORITY_MAP:
                        request['priority'] = self.PRIORITY_MAP[request['priority']]
                if 'frame' in trace_event['args']['data']:
                    request['frame'] = trace_event['args']['data']['frame']
                if 'renderBlocking' in trace_event['args']['data']:
                    request['renderBlocking'] = trace_event['args']['data']['renderBlocking']
            if trace_event['name'] == 'ResourceFetcher::WarnUnusedPreloads':
                request['preloadUnused'] = 'true'
            if trace_event['name'] == 'ResourceFetcher::PrintPreloadMismatch':
                request['preloadMismatch'] = 'true'
        # Build timeline events on a stack. 'B' begins an event, 'E' ends an
        # event
        if (thread in self.threads and (
                'dur' in trace_event or trace_event['ph'] == 'B' or trace_event['ph'] == 'E')):
            trace_event['thread'] = self.threads[thread]
            if thread not in self.thread_stack:
                self.thread_stack[thread] = []
            if trace_event['name'] not in self.event_names:
                self.event_names[trace_event['name']] = len(self.event_names)
                self.event_name_lookup[self.event_names[trace_event['name']]] = trace_event['name']
            if trace_event['name'] not in self.threads[thread]:
                self.threads[thread][trace_event['name']] = self.event_names[trace_event['name']]
            e = None
            if trace_event['ph'] == 'E':
                if len(self.thread_stack[thread]) > 0:
                    e = self.thread_stack[thread].pop()
                    if e['n'] == self.event_names[trace_event['name']]:
                        e['e'] = trace_event['ts']
            else:
                e = {'t': thread, 'n': self.event_names[trace_event['name']], 's': trace_event['ts']}
                if trace_event['name'] in ['EvaluateScript', 'v8.compile', 'v8.parseOnBackground'] and \
                        'args' in trace_event and 'data' in trace_event['args'] and \
                        'url' in trace_event['args']['data'] and \
                        trace_event['args']['data']['url'].startswith('http'):
                    e['js'] = trace_event['args']['data']['url']
                if trace_event['name'] == 'FunctionCall' and 'args' in trace_event and 'data' in trace_event['args']:
                    if 'scriptName' in trace_event['args']['data'] and trace_event['args']['data']['scriptName'].startswith(
                            'http'):
                        e['js'] = trace_event['args']['data']['scriptName']
                    elif 'url' in trace_event['args']['data'] and trace_event['args']['data']['url'].startswith('http'):
                        e['js'] = trace_event['args']['data']['url'].split('#', 1)[0]
                if trace_event['ph'] == 'B':
                    self.thread_stack[thread].append(e)
                    e = None
                elif 'dur' in trace_event:
                    e['e'] = e['s'] + trace_event['dur']

            if e is not None and 'e' in e and e['s'] >= self.start_time and e['e'] >= e['s']:
                if self.end_time is None or e['e'] > self.end_time:
                    self.end_time = e['e']
                # attach it to a parent event if there is one
                if len(self.thread_stack[thread]) > 0:
                    parent = self.thread_stack[thread].pop()
                    if 'c' not in parent:
                        parent['c'] = []
                    parent['c'].append(e)
                    self.thread_stack[thread].append(parent)
                else:
                    self.timeline_events.append(e)

    def ProcessOldTimelineEvent(self, event, type, depth=0):
        e = None
        thread = '0'
        if 'type' in event:
            type = event['type']
        if type not in self.event_names:
            self.event_names[type] = len(self.event_names)
            self.event_name_lookup[self.event_names[type]] = type
        if type not in self.threads[thread]:
            self.threads[thread][type] = self.event_names[type]
        start = None
        end = None
        if 'startTime' in event and 'endTime' in event:
            start = event['startTime'] * 1000000.0
            end = event['endTime'] * 1000000.0
        if 'callInfo' in event:
            if 'startTime' in event['callInfo'] and 'endTime' in event['callInfo']:
                start = event['callInfo']['startTime'] * 1000000.0
                end = event['callInfo']['endTime'] * 1000000.0
        if start is not None and end is not None and end >= start and type is not None:
            # Keep track of the long tasks
            if self.long_tasks is None:
                self.long_tasks = []
            elapsed = end - start
            if depth == 0 and elapsed > 50000:
                # make sure this isn't contained within an existing event
                ms_start = int(math.floor(start / 1000.0))
                ms_end = int(math.ceil(end / 1000.0))
                if not self.long_tasks:
                    # Empty list of long tasks
                    self.long_tasks.append([ms_start, ms_end])
                else:
                    last_start = self.long_tasks[-1][0]
                    last_end = self.long_tasks[-1][1]
                    if ms_start >= last_end:
                        # This task is entirely after the last long task we know about
                        self.long_tasks.append([ms_start, ms_end])
                    elif ms_end > last_end:
                        # task extends beyond the previous end of the long tasks but overlaps
                        del self.long_tasks[-1]
                        if ms_start >= last_start:
                            self.long_tasks.append([last_start, ms_end])
                        else:
                            self.long_tasks.append([ms_start, ms_end])
            if end > self.end_time:
                self.end_time = end
            e = {'t': thread,
                 'n': self.event_names[type], 's': start, 'e': end}
            if 'callInfo' in event and 'url' in event and event['url'].startswith(
                    'http'):
                e['js'] = event['url'].split('#', 1)[0]
            elif 'data' in event and 'url' in event['data'] and \
                    event['data']['url'].startswith('http'):
                e['js'] = event['data']['url'].split('#', 1)[0]
            elif 'data' in event and 'scriptName' in event['data'] and \
                    event['data']['scriptName'].startswith('http'):
                e['js'] = event['data']['scriptName'].split('#', 1)[0]
            elif 'stackTrace' in event and event['stackTrace']:
                for stack_frame in event['stackTrace']:
                    if 'url' in stack_frame and stack_frame['url'].startswith('http'):
                        e['js'] = stack_frame['url'].split('#', 1)[0]
                        break
            # Process profile child events
            if 'data' in event and 'profile' in event['data'] and 'rootNodes' in event['data']['profile']:
                for child in event['data']['profile']['rootNodes']:
                    c = self.ProcessOldTimelineEvent(child, type, depth + 1)
                    if c is not None:
                        if 'c' not in e:
                            e['c'] = []
                        e['c'].append(c)
            # recursively process any child events
            if 'children' in event:
                for child in event['children']:
                    c = self.ProcessOldTimelineEvent(child, type, depth + 1)
                    if c is not None:
                        if 'c' not in e:
                            e['c'] = []
                        e['c'].append(c)
        return e

    def ProcessTimelineEvents(self):
        if len(self.timeline_events) and self.end_time > self.start_time:
            # Figure out how big each slice should be in usecs. Size it to a
            # power of 10 where we have at least 2000 slices
            if self.interactive is None:
                self.interactive = []
            exp = 0
            last_exp = 0
            slice_count = self.end_time - self.start_time
            while slice_count > 2000:
                last_exp = exp
                exp += 1
                slice_count = int(
                    math.ceil(float(self.end_time - self.start_time) / float(pow(10, exp))))
            self.cpu['total_usecs'] = self.end_time - self.start_time
            self.cpu['slice_usecs'] = int(pow(10, last_exp))
            slice_count = int(math.ceil(
                float(self.end_time - self.start_time) / float(self.cpu['slice_usecs'])))

            # Create the empty time slices for all of the threads
            self.cpu['slices'] = {}
            for thread in self.threads.keys():
                self.cpu['slices'][thread] = {'total': [0.0] * slice_count}
                for name in self.threads[thread].keys():
                    self.cpu['slices'][thread][name] = [0.0] * slice_count

            # Go through all of the timeline events recursively and account for
            # the time they consumed
            for timeline_event in self.timeline_events:
                self.ProcessTimelineEvent(timeline_event, None)
            if self.interactive_end is not None and self.interactive_end - \
                    self.interactive_start > 500000:
                self.interactive.append([int(math.ceil(self.interactive_start / 1000.0)),
                                         int(math.floor(self.interactive_end / 1000.0))])

            # Go through all of the fractional times and convert the float
            # fractional times to integer usecs
            for thread in self.cpu['slices'].keys():
                del self.cpu['slices'][thread]['total']
                for name in self.cpu['slices'][thread].keys():
                    for slice in range(len(self.cpu['slices'][thread][name])):
                        self.cpu['slices'][thread][name][slice] =\
                            int(self.cpu['slices'][thread][name]
                                [slice] * self.cpu['slice_usecs'])

            # Pick the candidate main thread with the most activity
            main_threads = list(self.cpu['main_threads'])
            if len(main_threads) == 0:
                main_threads = self.cpu['slices'].keys()
            main_thread = None
            main_thread_cpu = 0
            for thread in main_threads:
                try:
                    thread_cpu = 0
                    if thread in self.cpu['slices']:
                        for name in self.cpu['slices'][thread].keys():
                            for slice in range(len(self.cpu['slices'][thread][name])):
                                thread_cpu += self.cpu['slices'][thread][name][slice]
                        if main_thread is None or thread_cpu > main_thread_cpu:
                            main_thread = thread
                            main_thread_cpu = thread_cpu
                except Exception:
                    logging.exception('Error processing thread')
            if main_thread is not None:
                self.cpu['main_thread'] = main_thread

    def ProcessTimelineEvent(self, timeline_event, parent, stack=None):
        start = timeline_event['s'] - self.start_time
        end = timeline_event['e'] - self.start_time
        if self.long_tasks is None:
            self.long_tasks = []
        self.cpu['valid'] = True
        if stack is None:
            stack = {}
        if end > start:
            elapsed = end - start
            thread = timeline_event['t']
            name = self.event_name_lookup[timeline_event['n']]

            # Keep track of periods on the main thread where at least 500ms are
            # available with no tasks longer than 50ms
            if 'main_thread' in self.cpu and thread == self.cpu['main_thread']:
                if elapsed > 50000:
                    if start - self.interactive_start > 500000:
                        self.interactive.append(
                            [int(math.ceil(self.interactive_start / 1000.0)),
                             int(math.floor(start / 1000.0))])
                    self.interactive_start = end
                    self.interactive_end = None
                else:
                    self.interactive_end = end
            
            # Keep track of the long-duration top-level tasks
            if parent is None and elapsed > 50000 and thread in self.cpu['main_threads']:
                # make sure this isn't contained within an existing event
                ms_start = int(math.floor(start / 1000.0))
                ms_end = int(math.ceil(end / 1000.0))
                if not self.long_tasks:
                    # Empty list of long tasks
                    self.long_tasks.append([ms_start, ms_end])
                else:
                    last_start = self.long_tasks[-1][0]
                    last_end = self.long_tasks[-1][1]
                    if ms_start >= last_end:
                        # This task is entirely after the last long task we know about
                        self.long_tasks.append([ms_start, ms_end])
                    elif ms_end > last_end:
                        # task extends beyond the previous end of the long tasks but overlaps
                        del self.long_tasks[-1]
                        if ms_start >= last_start:
                            self.long_tasks.append([last_start, ms_end])
                        else:
                            self.long_tasks.append([ms_start, ms_end])

            if 'js' in timeline_event:
                script = timeline_event['js']
                js_start = start / 1000.0
                js_end = end / 1000.0
                if self.scripts is None:
                    self.scripts = {}
                if 'main_thread' not in self.scripts and 'main_thread' in self.cpu:
                    self.scripts['main_thread'] = self.cpu['main_thread']
                if thread not in self.scripts:
                    self.scripts[thread] = {}
                if script not in self.scripts[thread]:
                    self.scripts[thread][script] = {}
                if name not in self.scripts[thread][script]:
                    self.scripts[thread][script][name] = []
                if thread not in stack:
                    stack[thread] = {}
                if script not in stack[thread]:
                    stack[thread][script] = {}
                if name not in stack[thread][script]:
                    stack[thread][script][name] = []
                # make sure the script duration isn't already covered by a
                # parent event
                new_duration = True
                if len(stack[thread][script][name]):
                    for period in stack[thread][script][name]:
                        if len(period) >= 2 and js_start >= period[0] and js_end <= period[1]:
                            new_duration = False
                            break
                if new_duration:
                    self.scripts[thread][script][name].append([js_start, js_end])
                    stack[thread][script][name].append([js_start, js_end])

            slice_usecs = self.cpu['slice_usecs']
            first_slice = int(float(start) / float(slice_usecs))
            last_slice = int(float(end) / float(slice_usecs))
            for slice_number in range(first_slice, last_slice + 1):
                slice_start = slice_number * slice_usecs
                slice_end = slice_start + slice_usecs
                used_start = max(slice_start, start)
                used_end = min(slice_end, end)
                slice_elapsed = used_end - used_start
                self.AdjustTimelineSlice(
                    thread, slice_number, name, parent, slice_elapsed)

            # Recursively process any child events
            if 'c' in timeline_event:
                for child in timeline_event['c']:
                    self.ProcessTimelineEvent(child, name, dict(stack))

    # Add the time to the given slice and subtract the time from a parent event
    def AdjustTimelineSlice(self, thread, slice_number, name, parent, elapsed):
        try:
            # Don't bother adjusting if both the current event and parent are the same category
            # since they would just cancel each other out.
            if name != parent:
                fraction = min(1.0, float(elapsed) /
                               float(self.cpu['slice_usecs']))
                self.cpu['slices'][thread][name][slice_number] += fraction
                self.cpu['slices'][thread]['total'][slice_number] += fraction
                if parent is not None and \
                        self.cpu['slices'][thread][parent][slice_number] >= fraction:
                    self.cpu['slices'][thread][parent][slice_number] -= fraction
                    self.cpu['slices'][thread]['total'][slice_number] -= fraction
                # Make sure we didn't exceed 100% in this slice
                self.cpu['slices'][thread][name][slice_number] = min(
                    1.0, self.cpu['slices'][thread][name][slice_number])

                # make sure we don't exceed 100% for any slot
                if self.cpu['slices'][thread]['total'][slice_number] > 1.0:
                    available = max(0.0, 1.0 - fraction)
                    for slice_name in self.cpu['slices'][thread].keys():
                        if slice_name != name:
                            self.cpu['slices'][thread][slice_name][slice_number] =\
                                min(self.cpu['slices'][thread]
                                    [slice_name][slice_number], available)
                            available = max(0.0, available - \
                                            self.cpu['slices'][thread][slice_name][slice_number])
                    self.cpu['slices'][thread]['total'][slice_number] = min(
                        1.0, max(0.0, 1.0 - available))
        except BaseException:
            logging.exception('Error adjusting timeline slice')

    ##########################################################################
    #   Blink Content Events
    ##########################################################################
    def ProcessContentEvent(self, trace_event):
        if 'name' in trace_event:
            if trace_event['name'] == 'WebContentsImpl::UpdateTitle':
                if 'titleTime' not in self.page_data['times']:
                    self.page_data['times']['titleTime'] = trace_event['ts']

    ##########################################################################
    #   Blink Features
    ##########################################################################
    def ProcessFeatureUsageEvent(self, trace_event):
        global BLINK_FEATURES
        if 'name' in trace_event and\
                'args' in trace_event and\
                'feature' in trace_event['args'] and\
            (trace_event['name'] == 'FeatureFirstUsed' or trace_event['name'] == 'CSSFirstUsed'):
            if self.feature_usage is None:
                self.feature_usage = {
                    'Features': {}, 'CSSFeatures': {}, 'AnimatedCSSFeatures': {}}
            id = '{0:d}'.format(trace_event['args']['feature'])
            if trace_event['name'] == 'FeatureFirstUsed':
                if id in BLINK_FEATURES:
                    name = BLINK_FEATURES[id]
                else:
                    name = 'Feature_{0}'.format(id)
                if id not in self.feature_usage['Features']:
                    self.feature_usage['Features'][id] = {'name': name, 'firstUsed': []}
                self.feature_usage['Features'][id]['firstUsed'].append(trace_event['ts'])
            elif trace_event['name'] == 'CSSFirstUsed':
                if id in CSS_FEATURES:
                    name = CSS_FEATURES[id]
                else:
                    name = 'CSSFeature_{0}'.format(id)
                if id not in self.feature_usage['CSSFeatures']:
                    self.feature_usage['CSSFeatures'][id] = {'name': name, 'firstUsed': []}
                self.feature_usage['CSSFeatures'][id]['firstUsed'].append(trace_event['ts'])
            elif trace_event['name'] == 'AnimatedCSSFirstUsed':
                if id in CSS_FEATURES:
                    name = CSS_FEATURES[id]
                else:
                    name = 'CSSFeature_{0}'.format(id)
                if id not in self.feature_usage['AnimatedCSSFeatures']:
                    self.feature_usage['AnimatedCSSFeatures'][id] = {'name': name, 'firstUsed': []}
                self.feature_usage['AnimatedCSSFeatures'][id]['firstUsed'].append(trace_event['ts'])
    
    def post_process_feature_usage(self):
        out = None
        if self.feature_usage is not None and self.start_time is not None:
            out = {}
            for category in self.feature_usage:
                out[category] = {}
                for id in self.feature_usage[category]:
                    feature_time = None
                    for ts in self.feature_usage[category][id]['firstUsed']:
                        timestamp = float('{0:0.3f}'.format((ts - self.start_time) / 1000.0))
                        if timestamp > 0:
                            if feature_time is None or timestamp < feature_time:
                                feature_time = timestamp
                    if feature_time is not None:
                        out[category][id] = {'name': self.feature_usage[category][id]['name'], 'firstUsed': feature_time}
        return out

    ##########################################################################
    #   Netlog
    ##########################################################################
    def ProcessNetlogEvent(self, trace_event):
        if 'args' in trace_event and 'id' in trace_event and 'name' in trace_event:
            try:
                if isinstance(trace_event['id'], (str, unicode)):
                    trace_event['id'] = int(trace_event['id'], 16)
                event_type = None
                name = trace_event['name']
                if 'source_type' in trace_event['args']:
                    event_type = trace_event['args']['source_type']
                    if name not in self.netlog_event_types:
                        self.netlog_event_types[name] = event_type
                elif name in self.netlog_event_types:
                    event_type = self.netlog_event_types[name]
                if event_type is not None:
                    if event_type == 'HOST_RESOLVER_IMPL_JOB' or \
                            trace_event['name'].startswith('HOST_RESOLVER'):
                        self.ProcessNetlogDnsEvent(trace_event)
                    elif event_type == 'CONNECT_JOB' or \
                            event_type == 'SSL_CONNECT_JOB' or \
                            event_type == 'TRANSPORT_CONNECT_JOB':
                        self.ProcessNetlogConnectJobEvent(trace_event)
                    elif event_type == 'HTTP_STREAM_JOB':
                        self.ProcessNetlogStreamJobEvent(trace_event)
                    elif event_type == 'HTTP2_SESSION':
                        self.ProcessNetlogHttp2SessionEvent(trace_event)
                    elif event_type == 'QUIC_SESSION':
                        self.ProcessNetlogQuicSessionEvent(trace_event)
                    elif event_type == 'SOCKET':
                        self.ProcessNetlogSocketEvent(trace_event)
                    elif event_type == 'UDP_SOCKET':
                        self.ProcessNetlogUdpSocketEvent(trace_event)
                    elif event_type == 'URL_REQUEST':
                        self.ProcessNetlogUrlRequestEvent(trace_event)
                    elif event_type == 'DISK_CACHE_ENTRY':
                        self.ProcessNetlogDiskCacheEvent(trace_event)
            except Exception:
                logging.exception('Error processing netlog event')

    def post_process_netlog_events(self):
        """Post-process the raw netlog events into request data"""
        if self.netlog_requests is not None:
            return self.netlog_requests
        requests = []
        known_hosts = ['cache.pack.google.com', 'clients1.google.com', 'redirector.gvt1.com']
        last_time = 0
        if 'url_request' in self.netlog:
            for request_id in self.netlog['url_request']:
                request = self.netlog['url_request'][request_id]
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
                                    scheme = unicode(value)
                                elif key == u'host':
                                    origin = unicode(value)
                                elif key == u'authority':
                                    origin = unicode(value)
                                elif key == u'path':
                                    path = unicode(value)
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
                    for request in requests:
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
                    for request in requests:
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
                    for request in requests:
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
                for request in requests:
                    for time_name in times:
                        if time_name in request and self.marked_start_time is None:
                            if self.start_time is None or request[time_name] < self.start_time:
                                self.start_time = request[time_name]
                # Go through and adjust all of the times to be relative in ms
                if self.start_time is not None:
                    for request in requests:
                        for time_name in times:
                            if time_name in request:
                                request[time_name] = \
                                        float(request[time_name] - self.start_time) / 1000.0
                        for key in ['chunks', 'chunks_in', 'chunks_out']:
                            if key in request:
                                for chunk in request[key]:
                                    if 'ts' in chunk:
                                        chunk['ts'] = float(chunk['ts'] - self.start_time) / 1000.0
                else:
                    requests = []
        if not len(requests):
            requests = None
        self.netlog_requests = requests
        return requests

    def ProcessNetlogConnectJobEvent(self, trace_event):
        """Connect jobs link sockets to DNS lookups/group names"""
        if 'connect_job' not in self.netlog:
            self.netlog['connect_job'] = {}
        request_id = trace_event['id']
        if request_id not in self.netlog['connect_job']:
            self.netlog['connect_job'][request_id] = {'created': trace_event['ts']}
        params = trace_event['args']['params'] if 'params' in trace_event['args'] else {}
        entry = self.netlog['connect_job'][request_id]
        name = trace_event['name']
        if name == 'TRANSPORT_CONNECT_JOB_CONNECT' and trace_event['ph'] == 'b':
            entry['connect_start'] = trace_event['ts']
        if name == 'TRANSPORT_CONNECT_JOB_CONNECT' and trace_event['ph'] == 'e':
            entry['connect_end'] = trace_event['ts']
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

    def ProcessNetlogStreamJobEvent(self, trace_event):
        """Strem jobs leank requests to sockets"""
        if 'stream_job' not in self.netlog:
            self.netlog['stream_job'] = {}
        request_id = trace_event['id']
        if request_id not in self.netlog['stream_job']:
            self.netlog['stream_job'][request_id] = {'created': trace_event['ts']}
        params = trace_event['args']['params'] if 'params' in trace_event['args'] else {}
        entry = self.netlog['stream_job'][request_id]
        name = trace_event['name']
        if 'group_name' in params:
            entry['group'] = params['group_name']
        if 'group_id' in params:
            entry['group'] = params['group_id']
        if name == 'HTTP_STREAM_REQUEST_STARTED_JOB':
            entry['start'] = trace_event['ts']
        if name == 'TCP_CLIENT_SOCKET_POOL_REQUESTED_SOCKET':
            entry['socket_start'] = trace_event['ts']
        if 'source_dependency' in params and 'id' in params['source_dependency']:
            if name == 'SOCKET_POOL_BOUND_TO_SOCKET':
                socket_id = params['source_dependency']['id']
                entry['socket_end'] = trace_event['ts']
                entry['socket'] = socket_id
                if 'url_request' in entry and entry['urlrequest'] in self.netlog['urlrequest']:
                    self.netlog['urlrequest'][entry['urlrequest']]['socket'] = socket_id
                    if 'group' in entry:
                        self.netlog['urlrequest'][entry['urlrequest']]['group'] = entry['group']
            if name == 'HTTP_STREAM_JOB_BOUND_TO_REQUEST':
                url_request_id = params['source_dependency']['id']
                entry['url_request'] = url_request_id
                if 'socket_end' not in entry:
                    entry['socket_end'] = trace_event['ts']
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
                    entry['socket_end'] = trace_event['ts']
                if h2_session_id in self.netlog['h2_session'] and 'socket' in self.netlog['h2_session'][h2_session_id]:
                    entry['socket'] = self.netlog['h2_session'][h2_session_id]['socket']
                if 'url_request' in entry and entry['urlrequest'] in self.netlog['urlrequest']:
                    self.netlog['urlrequest'][entry['urlrequest']]['h2_session'] = h2_session_id

    def ProcessNetlogHttp2SessionEvent(self, trace_event):
        """Raw H2 session information (linked to sockets and requests)"""
        if 'h2_session' not in self.netlog:
            self.netlog['h2_session'] = {}
        session_id = trace_event['id']
        if session_id not in self.netlog['h2_session']:
            self.netlog['h2_session'][session_id] = {'stream': {}}
        params = trace_event['args']['params'] if 'params' in trace_event['args'] else {}
        entry = self.netlog['h2_session'][session_id]
        name = trace_event['name']
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
                stream['end'] = trace_event['ts']
                if 'first_byte' not in stream:
                    stream['first_byte'] = trace_event['ts']
                stream['bytes_in'] += params['size']
                stream['chunks'].append({'ts': trace_event['ts'], 'bytes': params['size']})
            if name == 'HTTP2_SESSION_SEND_HEADERS':
                if 'start' not in stream:
                    stream['start'] = trace_event['ts']
                if 'headers' in params:
                    stream['request_headers'] = params['headers']
            if name == 'HTTP2_SESSION_RECV_HEADERS':
                if 'first_byte' not in stream:
                    stream['first_byte'] = trace_event['ts']
                stream['end'] = trace_event['ts']
                if 'headers' in params:
                    stream['response_headers'] = params['headers']
            if name == 'HTTP2_STREAM_ADOPTED_PUSH_STREAM' and 'url' in params and \
                    'url_request' in self.netlog:
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
                                                      'created': trace_event['ts']}
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
            request['start'] = trace_event['ts']
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

    def ProcessNetlogQuicSessionEvent(self, trace_event):
        """Raw QUIC session information (linked to sockets and requests)"""
        if 'quic_session' not in self.netlog:
            self.netlog['quic_session'] = {}
        session_id = trace_event['id']
        if session_id not in self.netlog['quic_session']:
            self.netlog['quic_session'][session_id] = {'stream': {}}
        params = trace_event['args']['params'] if 'params' in trace_event['args'] else {}
        entry = self.netlog['quic_session'][session_id]
        name = trace_event['name']
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
            entry['connect_start'] = trace_event['ts']
        if name == 'QUIC_SESSION_VERSION_NEGOTIATED' and 'connect_end' not in entry:
            entry['connect_end'] = trace_event['ts']
            if 'version' in params:
                entry['version'] = params['version']
        if name == 'CERT_VERIFIER_REQUEST' and 'connect_end' in entry:
            if 'tls_start' not in entry:
                entry['tls_start'] = entry['connect_end']
            if 'tls_end' not in entry:
                entry['tls_end'] = trace_event['ts']
        if 'quic_stream_id' in params:
            stream_id = params['quic_stream_id']
            if stream_id not in entry['stream']:
                entry['stream'][stream_id] = {'bytes_in': 0, 'chunks': []}
            stream = entry['stream'][stream_id]
            if name == 'QUIC_CHROMIUM_CLIENT_STREAM_SEND_REQUEST_HEADERS':
                if 'start' not in stream:
                    stream['start'] = trace_event['ts']
                if 'headers' in params:
                    stream['request_headers'] = params['headers']
            if name == 'QUIC_CHROMIUM_CLIENT_STREAM_READ_RESPONSE_HEADERS':
                if 'first_byte' not in stream:
                    stream['first_byte'] = trace_event['ts']
                stream['end'] = trace_event['ts']
                if 'headers' in params:
                    stream['response_headers'] = params['headers']

    def ProcessNetlogDnsEvent(self, trace_event):
        if 'dns' not in self.netlog:
            self.netlog['dns'] = {}
        request_id = trace_event['id']
        if request_id not in self.netlog['dns']:
            self.netlog['dns'][request_id] = {}
        params = trace_event['args']['params'] if 'params' in trace_event['args'] else {}
        entry = self.netlog['dns'][request_id]
        name = trace_event['name']
        if 'source_dependency' in params and 'id' in params['source_dependency']:
            parent_id = params['source_dependency']['id']
            if 'connect_job' in self.netlog and parent_id in self.netlog['connect_job']:
                self.netlog['connect_job'][parent_id]['dns'] = request_id
        if name == 'HOST_RESOLVER_IMPL_REQUEST' and 'ph' in trace_event:
            if trace_event['ph'] == 'b':
                if 'start' not in entry or trace_event['ts'] < entry['start']:
                    entry['start'] = trace_event['ts']
            if trace_event['ph'] == 'e':
                if 'end' not in entry or trace_event['ts'] > entry['end']:
                    entry['end'] = trace_event['ts']
        if 'start' not in entry and name == 'HOST_RESOLVER_IMPL_ATTEMPT_STARTED':
            entry['start'] = trace_event['ts']
        if name == 'HOST_RESOLVER_IMPL_ATTEMPT_FINISHED':
            entry['end'] = trace_event['ts']
        if name == 'HOST_RESOLVER_IMPL_CACHE_HIT':
            if 'end' not in entry or trace_event['ts'] > entry['end']:
                entry['end'] = trace_event['ts']
        if 'host' not in entry and 'host' in params:
            entry['host'] = params['host']
        if 'address_list' in params:
            entry['address_list'] = params['address_list']

    def ProcessNetlogSocketEvent(self, trace_event):
        if 'socket' not in self.netlog:
            self.netlog['socket'] = {}
        request_id = trace_event['id']
        if request_id not in self.netlog['socket']:
            self.netlog['socket'][request_id] = {'bytes_out': 0, 'bytes_in': 0,
                                                 'chunks_out': [], 'chunks_in': []}
        params = trace_event['args']['params'] if 'params' in trace_event['args'] else {}
        entry = self.netlog['socket'][request_id]
        name = trace_event['name']
        if 'address' in params:
            entry['address'] = params['address']
        if 'source_address' in params:
            entry['source_address'] = params['source_address']
        if 'connect_start' not in entry and name == 'TCP_CONNECT_ATTEMPT' and \
                trace_event['ph'] == 'b':
            entry['connect_start'] = trace_event['ts']
        if name == 'TCP_CONNECT_ATTEMPT' and trace_event['ph'] == 'e':
            entry['connect_end'] = trace_event['ts']
        if name == 'SSL_CONNECT':
            if 'connect_end' not in entry:
                entry['connect_end'] = trace_event['ts']
            if 'ssl_start' not in entry and trace_event['ph'] == 'b':
                entry['ssl_start'] = trace_event['ts']
            if trace_event['ph'] == 'e':
                entry['ssl_end'] = trace_event['ts']
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
                entry['connect_end'] = trace_event['ts']
            entry['bytes_out'] += params['byte_count']
            entry['chunks_out'].append({'ts': trace_event['ts'], 'bytes': params['byte_count']})
        if name == 'SOCKET_BYTES_RECEIVED' and 'byte_count' in params:
            entry['bytes_in'] += params['byte_count']
            entry['chunks_in'].append({'ts': trace_event['ts'], 'bytes': params['byte_count']})
        if name == 'SSL_CERTIFICATES_RECEIVED' and 'certificates' in params:
            if 'certificates' not in entry:
                entry['certificates'] = []
            entry['certificates'].extend(params['certificates'])

    def ProcessNetlogUdpSocketEvent(self, trace_event):
        if 'socket' not in self.netlog:
            self.netlog['socket'] = {}
        request_id = trace_event['id']
        if request_id not in self.netlog['socket']:
            self.netlog['socket'][request_id] = {'bytes_out': 0, 'bytes_in': 0,
                                                 'chunks_out': [], 'chunks_in': []}
        params = trace_event['args']['params'] if 'params' in trace_event['args'] else {}
        entry = self.netlog['socket'][request_id]
        name = trace_event['name']
        if name == 'UDP_CONNECT' and 'address' in params:
            entry['address'] = params['address']
        if name == 'UDP_LOCAL_ADDRESS' and 'address' in params:
            entry['source_address'] = params['address']
        if 'connect_start' not in entry and name == 'UDP_CONNECT' and \
                trace_event['ph'] == 'b':
            entry['connect_start'] = trace_event['ts']
        if name == 'UDP_CONNECT' and trace_event['ph'] == 'e':
            entry['connect_end'] = trace_event['ts']
        if name == 'UDP_BYTES_SENT' and 'byte_count' in params:
            entry['bytes_out'] += params['byte_count']
            entry['chunks_out'].append({'ts': trace_event['ts'], 'bytes': params['byte_count']})
        if name == 'UDP_BYTES_RECEIVED' and 'byte_count' in params:
            entry['bytes_in'] += params['byte_count']
            entry['chunks_in'].append({'ts': trace_event['ts'], 'bytes': params['byte_count']})

    def ProcessNetlogUrlRequestEvent(self, trace_event):
        if 'url_request' not in self.netlog:
            self.netlog['url_request'] = {}
        request_id = trace_event['id']
        if request_id not in self.netlog['url_request']:
            self.netlog['url_request'][request_id] = {'bytes_in': 0,
                                                      'chunks': [],
                                                      'created': trace_event['ts']}
        params = trace_event['args']['params'] if 'params' in trace_event['args'] else {}
        entry = self.netlog['url_request'][request_id]
        name = trace_event['name']
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
            entry['start'] = trace_event['ts']
        if 'headers' in params and name == 'HTTP_TRANSACTION_SEND_REQUEST_HEADERS':
            entry['request_headers'] = params['headers']
            if 'line' in params:
                entry['line'] = params['line']
            if 'start' not in entry:
                entry['start'] = trace_event['ts']
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
                entry['start'] = trace_event['ts']
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
                entry['start'] = trace_event['ts']
        if 'headers' in params and name == 'HTTP_TRANSACTION_READ_RESPONSE_HEADERS':
            entry['response_headers'] = params['headers']
            if 'first_byte' not in entry:
                entry['first_byte'] = trace_event['ts']
            entry['end'] = trace_event['ts']
        if 'headers' in params and name == 'HTTP_TRANSACTION_READ_EARLY_HINTS_RESPONSE_HEADERS':
            entry['early_hint_headers'] = params['headers']
            entry['end'] = trace_event['time']
        if 'byte_count' in params and name == 'URL_REQUEST_JOB_BYTES_READ':
            entry['has_raw_bytes'] = True
            entry['end'] = trace_event['ts']
            entry['bytes_in'] += params['byte_count']
            entry['chunks'].append({'ts': trace_event['ts'], 'bytes': params['byte_count']})
        if 'byte_count' in params and name == 'URL_REQUEST_JOB_FILTERED_BYTES_READ':
            entry['end'] = trace_event['ts']
            if 'uncompressed_bytes_in' not in entry:
                entry['uncompressed_bytes_in'] = 0
            entry['uncompressed_bytes_in'] += params['byte_count']
            if 'has_raw_bytes' not in entry or not entry['has_raw_bytes']:
                entry['bytes_in'] += params['byte_count']
                entry['chunks'].append({'ts': trace_event['ts'], 'bytes': params['byte_count']})
        if 'stream_id' in params:
            entry['stream_id'] = params['stream_id']
        if name == 'URL_REQUEST_REDIRECTED':
            new_id = self.netlog['next_request_id']
            self.netlog['next_request_id'] += 1
            self.netlog['url_request'][new_id] = entry
            del self.netlog['url_request'][request_id]
    
    def ProcessNetlogDiskCacheEvent(self, trace_event):
        """Disk cache events"""
        if 'args' in trace_event and 'params' in trace_event['args'] and 'key' in trace_event['args']['params']:
            url = trace_event['args']['params']['key']
            space_index = url.rfind(' ')
            if space_index >= 0:
                url = url[space_index + 1:]
            if 'urls' not in self.netlog:
                self.netlog['urls'] = {}
            if url not in self.netlog['urls']:
                self.netlog['urls'][url] = {'start': trace_event['ts']}


    #######################################################################
    #   V8 call stats
    #######################################################################
    def ProcessV8Event(self, trace_event):
        try:
            if self.start_time is not None and self.cpu['main_thread'] is not None and trace_event['ts'] >= self.start_time and \
                    "name" in trace_event:
                thread = '{0}:{1}'.format(
                    trace_event['pid'], trace_event['tid'])
                if trace_event["ph"] == "B":
                    if thread not in self.v8stack:
                        self.v8stack[thread] = []
                    self.v8stack[thread].append(trace_event)
                else:
                    duration = 0.0
                    if trace_event["ph"] == "E" and thread in self.v8stack:
                        start_event = self.v8stack[thread].pop()
                        if start_event['name'] == trace_event['name'] and 'ts' in start_event and start_event['ts'] <= trace_event['ts']:
                            duration = trace_event['ts'] - start_event['ts']
                    elif trace_event['ph'] == 'X' and 'dur' in trace_event:
                        duration = trace_event['dur']
                    if self.v8stats is None:
                        self.v8stats = {'threads': {}}
                    if thread not in self.v8stats['threads']:
                        self.v8stats['threads'][thread] = {}
                    name = trace_event["name"]
                    if name not in self.v8stats['threads'][thread]:
                        self.v8stats['threads'][thread][name] = {"dur": 0.0, "count": 0}
                    self.v8stats['threads'][thread][name]['dur'] += float(duration) / 1000.0
                    self.v8stats['threads'][thread][name]['count'] += 1
                    if 'args' in trace_event and 'runtime-call-stats' in trace_event["args"]:
                        for stat in trace_event["args"]["runtime-call-stats"]:
                            if len(trace_event["args"]["runtime-call-stats"][stat]) == 2:
                                if 'breakdown' not in self.v8stats['threads'][thread][name]:
                                    self.v8stats['threads'][thread][name]['breakdown'] = {}
                                if stat not in self.v8stats['threads'][thread][name]['breakdown']:
                                    self.v8stats['threads'][thread][name]['breakdown'][stat] = {"count": 0, "dur": 0.0}
                                self.v8stats['threads'][thread][name]['breakdown'][stat]["count"] += int(trace_event["args"]["runtime-call-stats"][stat][0])
                                self.v8stats['threads'][thread][name]['breakdown'][stat]["dur"] += float(trace_event["args"]["runtime-call-stats"][stat][1]) / 1000.0
        except BaseException:
            logging.exception('Error processing V8 event')


##########################################################################
#   Main Entry Point
##########################################################################
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Chrome trace parser.',
                                     prog='trace-parser')
    parser.add_argument('-v', '--verbose', action='count',
                        help="Increase verbosity (specify multiple times for more). -vvvv for full debug output.")
    parser.add_argument('-t', '--trace', help="Input trace file.")
    parser.add_argument('-l', '--timeline',
                        help="Input timeline file (iOS or really old Chrome).")
    parser.add_argument('-c', '--cpu', help="Output CPU time slices file.")
    parser.add_argument(
        '-j', '--js', help="Output Javascript per-script parse/evaluate/execute timings.")
    parser.add_argument('-u', '--user', help="Output user timing file.")
    parser.add_argument('-f', '--features',
                        help="Output blink feature usage file.")
    parser.add_argument('-i', '--interactive',
                        help="Output list of interactive times.")
    parser.add_argument('-x', '--longtasks', help="Output list of long main thread task times.")
    parser.add_argument('-n', '--netlog', help="Output netlog details file.")
    parser.add_argument('-r', '--requests', help="Output timeline requests file.")
    parser.add_argument('-s', '--stats', help="Output v8 Call stats file.")
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

    if not options.trace and not options.timeline:
        parser.error("Input trace or timeline file is not specified.")

    start = time.time()
    trace = Trace()
    if options.trace:
        trace.Process(options.trace)
    elif options.timeline:
        trace.ProcessTimeline(options.timeline)

    if options.user:
        trace.WriteUserTiming(options.user)

    if options.cpu:
        trace.WriteCPUSlices(options.cpu)

    if options.js:
        trace.WriteScriptTimings(options.js)

    if options.features:
        trace.WriteFeatureUsage(options.features)

    if options.interactive:
        trace.WriteInteractive(options.interactive)
    
    if options.longtasks:
        trace.WriteLongTasks(options.longtasks)

    if options.netlog:
        trace.WriteNetlog(options.netlog)
    
    if options.requests:
        trace.WriteTimelineRequests(options.requests)

    if options.stats:
        trace.WriteV8Stats(options.stats)

    end = time.time()
    elapsed = end - start
    logging.debug("Elapsed Time: {0:0.4f}".format(elapsed))


##########################################################################
#   Blink feature names from https://source.chromium.org/chromium/chromium/src/+/main:third_party/blink/public/mojom/use_counter/metrics/web_feature.mojom
##########################################################################
BLINK_FEATURES = {
    "0": "PageDestruction",
    "3": "PrefixedIndexedDB",
    "4": "WorkerStart",
    "5": "SharedWorkerStart",
    "9": "UnprefixedIndexedDB",
    "10": "OpenWebDatabase",
    "13": "UnprefixedRequestAnimationFrame",
    "14": "PrefixedRequestAnimationFrame",
    "15": "ContentSecurityPolicy",
    "16": "ContentSecurityPolicyReportOnly",
    "18": "PrefixedTransitionEndEvent",
    "19": "UnprefixedTransitionEndEvent",
    "20": "PrefixedAndUnprefixedTransitionEndEvent",
    "21": "AutoFocusAttribute",
    "23": "DataListElement",
    "24": "FormAttribute",
    "25": "IncrementalAttribute",
    "26": "InputTypeColor",
    "27": "InputTypeDate",
    "29": "InputTypeDateTimeFallback",
    "30": "InputTypeDateTimeLocal",
    "31": "InputTypeEmail",
    "32": "InputTypeMonth",
    "33": "InputTypeNumber",
    "34": "InputTypeRange",
    "35": "InputTypeSearch",
    "36": "InputTypeTel",
    "37": "InputTypeTime",
    "38": "InputTypeURL",
    "39": "InputTypeWeek",
    "40": "InputTypeWeekFallback",
    "41": "ListAttribute",
    "42": "MaxAttribute",
    "43": "MinAttribute",
    "44": "PatternAttribute",
    "45": "PlaceholderAttribute",
    "47": "PrefixedDirectoryAttribute",
    "49": "RequiredAttribute",
    "51": "StepAttribute",
    "52": "PageVisits",
    "53": "HTMLMarqueeElement",
    "55": "Reflection",
    "57": "PrefixedStorageInfo",
    "58": "XFrameOptions",
    "59": "XFrameOptionsSameOrigin",
    "60": "XFrameOptionsSameOriginWithBadAncestorChain",
    "61": "DeprecatedFlexboxWebContent",
    "62": "DeprecatedFlexboxChrome",
    "63": "DeprecatedFlexboxChromeExtension",
    "65": "UnprefixedPerformanceTimeline",
    "67": "UnprefixedUserTiming",
    "69": "WindowEvent",
    "70": "ContentSecurityPolicyWithBaseElement",
    "74": "DocumentClear",
    "77": "XMLDocument",
    "78": "XSLProcessingInstruction",
    "79": "XSLTProcessor",
    "80": "SVGSwitchElement",
    "83": "DocumentAll",
    "84": "FormElement",
    "85": "DemotedFormElement",
    "90": "SVGAnimationElement",
    "96": "LineClamp",
    "97": "SubFrameBeforeUnloadRegistered",
    "98": "SubFrameBeforeUnloadFired",
    "102": "ConsoleMarkTimeline",
    "111": "DocumentCreateAttribute",
    "112": "DocumentCreateAttributeNS",
    "113": "DocumentCreateCDATASection",
    "115": "DocumentXMLEncoding",
    "116": "DocumentXMLStandalone",
    "117": "DocumentXMLVersion",
    "123": "NavigatorProductSub",
    "124": "NavigatorVendor",
    "125": "NavigatorVendorSub",
    "128": "PrefixedAnimationEndEvent",
    "129": "UnprefixedAnimationEndEvent",
    "130": "PrefixedAndUnprefixedAnimationEndEvent",
    "131": "PrefixedAnimationStartEvent",
    "132": "UnprefixedAnimationStartEvent",
    "133": "PrefixedAndUnprefixedAnimationStartEvent",
    "134": "PrefixedAnimationIterationEvent",
    "135": "UnprefixedAnimationIterationEvent",
    "136": "PrefixedAndUnprefixedAnimationIterationEvent",
    "137": "EventReturnValue",
    "138": "SVGSVGElement",
    "143": "DOMSubtreeModifiedEvent",
    "144": "DOMNodeInsertedEvent",
    "145": "DOMNodeRemovedEvent",
    "146": "DOMNodeRemovedFromDocumentEvent",
    "147": "DOMNodeInsertedIntoDocumentEvent",
    "148": "DOMCharacterDataModifiedEvent",
    "150": "DocumentAllLegacyCall",
    "152": "HTMLEmbedElementLegacyCall",
    "153": "HTMLObjectElementLegacyCall",
    "155": "GetMatchedCSSRules",
    "160": "AttributeOwnerElement",
    "162": "AttributeSpecified",
    "164": "PrefixedAudioDecodedByteCount",
    "165": "PrefixedVideoDecodedByteCount",
    "166": "PrefixedVideoSupportsFullscreen",
    "167": "PrefixedVideoDisplayingFullscreen",
    "168": "PrefixedVideoEnterFullscreen",
    "169": "PrefixedVideoExitFullscreen",
    "170": "PrefixedVideoEnterFullScreen",
    "171": "PrefixedVideoExitFullScreen",
    "172": "PrefixedVideoDecodedFrameCount",
    "173": "PrefixedVideoDroppedFrameCount",
    "176": "PrefixedElementRequestFullscreen",
    "177": "PrefixedElementRequestFullScreen",
    "178": "BarPropLocationbar",
    "179": "BarPropMenubar",
    "180": "BarPropPersonalbar",
    "181": "BarPropScrollbars",
    "182": "BarPropStatusbar",
    "183": "BarPropToolbar",
    "184": "InputTypeEmailMultiple",
    "185": "InputTypeEmailMaxLength",
    "186": "InputTypeEmailMultipleMaxLength",
    "190": "InputTypeText",
    "191": "InputTypeTextMaxLength",
    "192": "InputTypePassword",
    "193": "InputTypePasswordMaxLength",
    "196": "PrefixedPageVisibility",
    "198": "CSSStyleSheetInsertRuleOptionalArg",
    "200": "DocumentBeforeUnloadRegistered",
    "201": "DocumentBeforeUnloadFired",
    "202": "DocumentUnloadRegistered",
    "203": "DocumentUnloadFired",
    "204": "SVGLocatableNearestViewportElement",
    "205": "SVGLocatableFarthestViewportElement",
    "209": "SVGPointMatrixTransform",
    "211": "DOMFocusInOutEvent",
    "212": "FileGetLastModifiedDate",
    "213": "HTMLElementInnerText",
    "214": "HTMLElementOuterText",
    "215": "ReplaceDocumentViaJavaScriptURL",
    "217": "ElementPrefixedMatchesSelector",
    "219": "CSSStyleSheetRules",
    "220": "CSSStyleSheetAddRule",
    "221": "CSSStyleSheetRemoveRule",
    "222": "InitMessageEvent",
    "233": "PrefixedDevicePixelRatioMediaFeature",
    "234": "PrefixedMaxDevicePixelRatioMediaFeature",
    "235": "PrefixedMinDevicePixelRatioMediaFeature",
    "237": "PrefixedTransform3dMediaFeature",
    "240": "PrefixedStorageQuota",
    "243": "ResetReferrerPolicy",
    "244": "CaseInsensitiveAttrSelectorMatch",
    "246": "FormNameAccessForImageElement",
    "247": "FormNameAccessForPastNamesMap",
    "248": "FormAssociationByParser",
    "250": "SVGSVGElementInDocument",
    "251": "SVGDocumentRootElement",
    "257": "WorkerSubjectToCSP",
    "258": "WorkerAllowedByChildBlockedByScript",
    "260": "DeprecatedWebKitGradient",
    "261": "DeprecatedWebKitLinearGradient",
    "262": "DeprecatedWebKitRepeatingLinearGradient",
    "263": "DeprecatedWebKitRadialGradient",
    "264": "DeprecatedWebKitRepeatingRadialGradient",
    "267": "PrefixedImageSmoothingEnabled",
    "268": "UnprefixedImageSmoothingEnabled",
    "274": "TextAutosizing",
    "276": "HTMLAnchorElementPingAttribute",
    "279": "SVGClassName",
    "281": "HTMLMediaElementSeekToFragmentStart",
    "282": "HTMLMediaElementPauseAtFragmentEnd",
    "283": "PrefixedWindowURL",
    "285": "WindowOrientation",
    "286": "DOMStringListContains",
    "287": "DocumentCaptureEvents",
    "288": "DocumentReleaseEvents",
    "289": "WindowCaptureEvents",
    "290": "WindowReleaseEvents",
    "295": "DocumentXPathCreateExpression",
    "296": "DocumentXPathCreateNSResolver",
    "297": "DocumentXPathEvaluate",
    "298": "AttrGetValue",
    "299": "AttrSetValue",
    "300": "AnimationConstructorKeyframeListEffectObjectTiming",
    "302": "AnimationConstructorKeyframeListEffectNoTiming",
    "303": "AttrSetValueWithElement",
    "304": "PrefixedCancelAnimationFrame",
    "305": "PrefixedCancelRequestAnimationFrame",
    "306": "NamedNodeMapGetNamedItem",
    "307": "NamedNodeMapSetNamedItem",
    "308": "NamedNodeMapRemoveNamedItem",
    "309": "NamedNodeMapItem",
    "310": "NamedNodeMapGetNamedItemNS",
    "311": "NamedNodeMapSetNamedItemNS",
    "312": "NamedNodeMapRemoveNamedItemNS",
    "318": "PrefixedDocumentIsFullscreen",
    "320": "PrefixedDocumentCurrentFullScreenElement",
    "321": "PrefixedDocumentCancelFullScreen",
    "322": "PrefixedDocumentFullscreenEnabled",
    "323": "PrefixedDocumentFullscreenElement",
    "324": "PrefixedDocumentExitFullscreen",
    "325": "SVGForeignObjectElement",
    "327": "SelectionSetPosition",
    "328": "AnimationFinishEvent",
    "329": "SVGSVGElementInXMLDocument",
    "341": "PrefixedPerformanceClearResourceTimings",
    "342": "PrefixedPerformanceSetResourceTimingBufferSize",
    "343": "EventSrcElement",
    "344": "EventCancelBubble",
    "345": "EventPath",
    "347": "NodeIteratorDetach",
    "348": "AttrNodeValue",
    "349": "AttrTextContent",
    "350": "EventGetReturnValueTrue",
    "351": "EventGetReturnValueFalse",
    "352": "EventSetReturnValueTrue",
    "353": "EventSetReturnValueFalse",
    "356": "WindowOffscreenBuffering",
    "357": "WindowDefaultStatus",
    "358": "WindowDefaultstatus",
    "361": "PrefixedTransitionEventConstructor",
    "362": "PrefixedMutationObserverConstructor",
    "363": "PrefixedIDBCursorConstructor",
    "364": "PrefixedIDBDatabaseConstructor",
    "365": "PrefixedIDBFactoryConstructor",
    "366": "PrefixedIDBIndexConstructor",
    "367": "PrefixedIDBKeyRangeConstructor",
    "368": "PrefixedIDBObjectStoreConstructor",
    "369": "PrefixedIDBRequestConstructor",
    "370": "PrefixedIDBTransactionConstructor",
    "371": "NotificationPermission",
    "372": "RangeDetach",
    "386": "PrefixedFileRelativePath",
    "387": "DocumentCaretRangeFromPoint",
    "389": "ElementScrollIntoViewIfNeeded",
    "393": "RangeExpand",
    "396": "HTMLImageElementX",
    "397": "HTMLImageElementY",
    "400": "SelectionBaseNode",
    "401": "SelectionBaseOffset",
    "402": "SelectionExtentNode",
    "403": "SelectionExtentOffset",
    "404": "SelectionType",
    "405": "SelectionModify",
    "406": "SelectionSetBaseAndExtent",
    "407": "SelectionEmpty",
    "409": "VTTCue",
    "410": "VTTCueRender",
    "411": "VTTCueRenderVertical",
    "412": "VTTCueRenderSnapToLinesFalse",
    "413": "VTTCueRenderLineNotAuto",
    "414": "VTTCueRenderPositionNot50",
    "415": "VTTCueRenderSizeNot100",
    "416": "VTTCueRenderAlignNotMiddle",
    "417": "ElementRequestPointerLock",
    "418": "VTTCueRenderRtl",
    "419": "PostMessageFromSecureToInsecure",
    "420": "PostMessageFromInsecureToSecure",
    "421": "DocumentExitPointerLock",
    "422": "DocumentPointerLockElement",
    "424": "PrefixedCursorZoomIn",
    "425": "PrefixedCursorZoomOut",
    "429": "TextEncoderConstructor",
    "430": "TextEncoderEncode",
    "431": "TextDecoderConstructor",
    "432": "TextDecoderDecode",
    "433": "FocusInOutEvent",
    "434": "MouseEventMovementX",
    "435": "MouseEventMovementY",
    "440": "DocumentFonts",
    "441": "MixedContentFormsSubmitted",
    "442": "FormsSubmitted",
    "443": "TextInputEventOnInput",
    "444": "TextInputEventOnTextArea",
    "445": "TextInputEventOnContentEditable",
    "446": "TextInputEventOnNotNode",
    "447": "WebkitBeforeTextInsertedOnInput",
    "448": "WebkitBeforeTextInsertedOnTextArea",
    "449": "WebkitBeforeTextInsertedOnContentEditable",
    "450": "WebkitBeforeTextInsertedOnNotNode",
    "451": "WebkitEditableContentChangedOnInput",
    "452": "WebkitEditableContentChangedOnTextArea",
    "453": "WebkitEditableContentChangedOnContentEditable",
    "454": "WebkitEditableContentChangedOnNotNode",
    "455": "HTMLImports",
    "456": "ElementCreateShadowRoot",
    "457": "DocumentRegisterElement",
    "458": "EditingAppleInterchangeNewline",
    "459": "EditingAppleConvertedSpace",
    "460": "EditingApplePasteAsQuotation",
    "461": "EditingAppleStyleSpanClass",
    "462": "EditingAppleTabSpanClass",
    "463": "HTMLImportsAsyncAttribute",
    "465": "XMLHttpRequestSynchronous",
    "466": "CSSSelectorPseudoUnresolved",
    "467": "CSSSelectorPseudoShadow",
    "468": "CSSSelectorPseudoContent",
    "469": "CSSSelectorPseudoHost",
    "470": "CSSSelectorPseudoHostContext",
    "471": "CSSDeepCombinator",
    "473": "UseAsm",
    "475": "DOMWindowOpen",
    "476": "DOMWindowOpenFeatures",
    "478": "MediaStreamTrackGetSources",
    "479": "AspectRatioFlexItem",
    "480": "DetailsElement",
    "481": "DialogElement",
    "482": "MapElement",
    "483": "MeterElement",
    "484": "ProgressElement",
    "490": "PrefixedHTMLElementDropzone",
    "491": "WheelEventWheelDeltaX",
    "492": "WheelEventWheelDeltaY",
    "493": "WheelEventWheelDelta",
    "494": "SendBeacon",
    "495": "SendBeaconQuotaExceeded",
    "501": "SVGSMILElementInDocument",
    "502": "MouseEventOffsetX",
    "503": "MouseEventOffsetY",
    "504": "MouseEventX",
    "505": "MouseEventY",
    "506": "MouseEventFromElement",
    "507": "MouseEventToElement",
    "508": "RequestFileSystem",
    "509": "RequestFileSystemWorker",
    "510": "RequestFileSystemSyncWorker",
    "519": "SVGStyleElementTitle",
    "520": "PictureSourceSrc",
    "521": "Picture",
    "522": "Sizes",
    "523": "SrcsetXDescriptor",
    "524": "SrcsetWDescriptor",
    "525": "SelectionContainsNode",
    "529": "XMLExternalResourceLoad",
    "530": "MixedContentPrivateHostnameInPublicHostname",
    "531": "LegacyProtocolEmbeddedAsSubresource",
    "532": "RequestedSubresourceWithEmbeddedCredentials",
    "533": "NotificationCreated",
    "534": "NotificationClosed",
    "535": "NotificationPermissionRequested",
    "538": "ConsoleTimeline",
    "539": "ConsoleTimelineEnd",
    "540": "SRIElementWithMatchingIntegrityAttribute",
    "541": "SRIElementWithNonMatchingIntegrityAttribute",
    "542": "SRIElementWithUnparsableIntegrityAttribute",
    "545": "V8Animation_StartTime_AttributeGetter",
    "546": "V8Animation_StartTime_AttributeSetter",
    "547": "V8Animation_CurrentTime_AttributeGetter",
    "548": "V8Animation_CurrentTime_AttributeSetter",
    "549": "V8Animation_PlaybackRate_AttributeGetter",
    "550": "V8Animation_PlaybackRate_AttributeSetter",
    "551": "V8Animation_PlayState_AttributeGetter",
    "552": "V8Animation_Finish_Method",
    "553": "V8Animation_Play_Method",
    "554": "V8Animation_Pause_Method",
    "555": "V8Animation_Reverse_Method",
    "556": "BreakIterator",
    "557": "ScreenOrientationAngle",
    "558": "ScreenOrientationType",
    "559": "ScreenOrientationLock",
    "560": "ScreenOrientationUnlock",
    "561": "GeolocationSecureOrigin",
    "562": "GeolocationInsecureOrigin",
    "563": "NotificationSecureOrigin",
    "564": "NotificationInsecureOrigin",
    "565": "NotificationShowEvent",
    "569": "SVGTransformListConsolidate",
    "570": "SVGAnimatedTransformListBaseVal",
    "571": "QuotedAnimationName",
    "572": "QuotedKeyframesRule",
    "573": "SrcsetDroppedCandidate",
    "574": "WindowPostMessage",
    "575": "WindowPostMessageWithLegacyTargetOriginArgument",
    "576": "RenderRuby",
    "578": "ScriptElementWithInvalidTypeHasSrc",
    "581": "XMLHttpRequestSynchronousInNonWorkerOutsideBeforeUnload",
    "582": "CSSSelectorPseudoScrollbar",
    "583": "CSSSelectorPseudoScrollbarButton",
    "584": "CSSSelectorPseudoScrollbarThumb",
    "585": "CSSSelectorPseudoScrollbarTrack",
    "586": "CSSSelectorPseudoScrollbarTrackPiece",
    "587": "LangAttribute",
    "588": "LangAttributeOnHTML",
    "589": "LangAttributeOnBody",
    "590": "LangAttributeDoesNotMatchToUILocale",
    "591": "InputTypeSubmit",
    "592": "InputTypeSubmitWithValue",
    "593": "SetReferrerPolicy",
    "595": "MouseEventWhich",
    "598": "UIEventWhich",
    "599": "TextWholeText",
    "603": "NotificationCloseEvent",
    "606": "StyleMedia",
    "607": "StyleMediaType",
    "608": "StyleMediaMatchMedium",
    "609": "MixedContentPresent",
    "610": "MixedContentBlockable",
    "611": "MixedContentAudio",
    "612": "MixedContentDownload",
    "613": "MixedContentFavicon",
    "614": "MixedContentImage",
    "615": "MixedContentInternal",
    "616": "MixedContentPlugin",
    "617": "MixedContentPrefetch",
    "618": "MixedContentVideo",
    "620": "AudioListenerDopplerFactor",
    "621": "AudioListenerSpeedOfSound",
    "622": "AudioListenerSetVelocity",
    "628": "CSSSelectorPseudoFullScreenAncestor",
    "629": "CSSSelectorPseudoFullScreen",
    "630": "WebKitCSSMatrix",
    "631": "AudioContextCreateAnalyser",
    "632": "AudioContextCreateBiquadFilter",
    "633": "AudioContextCreateBufferSource",
    "634": "AudioContextCreateChannelMerger",
    "635": "AudioContextCreateChannelSplitter",
    "636": "AudioContextCreateConvolver",
    "637": "AudioContextCreateDelay",
    "638": "AudioContextCreateDynamicsCompressor",
    "639": "AudioContextCreateGain",
    "640": "AudioContextCreateMediaElementSource",
    "641": "AudioContextCreateMediaStreamDestination",
    "642": "AudioContextCreateMediaStreamSource",
    "643": "AudioContextCreateOscillator",
    "645": "AudioContextCreatePeriodicWave",
    "646": "AudioContextCreateScriptProcessor",
    "647": "AudioContextCreateStereoPanner",
    "648": "AudioContextCreateWaveShaper",
    "649": "AudioContextDecodeAudioData",
    "650": "AudioContextResume",
    "651": "AudioContextSuspend",
    "652": "AudioContext",
    "653": "OfflineAudioContext",
    "654": "PrefixedAudioContext",
    "655": "PrefixedOfflineAudioContext",
    "661": "MixedContentInNonHTTPSFrameThatRestrictsMixedContent",
    "662": "MixedContentInSecureFrameThatDoesNotRestrictMixedContent",
    "663": "MixedContentWebSocket",
    "664": "SyntheticKeyframesInCompositedCSSAnimation",
    "665": "MixedContentFormPresent",
    "666": "GetUserMediaInsecureOrigin",
    "667": "GetUserMediaSecureOrigin",
    "668": "DeviceMotionInsecureOrigin",
    "669": "DeviceMotionSecureOrigin",
    "670": "DeviceOrientationInsecureOrigin",
    "671": "DeviceOrientationSecureOrigin",
    "672": "SandboxViaIFrame",
    "673": "SandboxViaCSP",
    "674": "BlockedSniffingImageToScript",
    "675": "Fetch",
    "676": "FetchBodyStream",
    "677": "XMLHttpRequestAsynchronous",
    "679": "WhiteSpacePreFromXMLSpace",
    "680": "WhiteSpaceNowrapFromXMLSpace",
    "685": "SVGSVGElementForceRedraw",
    "686": "SVGSVGElementSuspendRedraw",
    "687": "SVGSVGElementUnsuspendRedraw",
    "688": "SVGSVGElementUnsuspendRedrawAll",
    "689": "AudioContextClose",
    "691": "CSSZoomNotEqualToOne",
    "694": "ClientRectListItem",
    "695": "WindowClientInformation",
    "696": "WindowFind",
    "697": "WindowScreenLeft",
    "698": "WindowScreenTop",
    "699": "V8Animation_Cancel_Method",
    "700": "V8Animation_Onfinish_AttributeGetter",
    "701": "V8Animation_Onfinish_AttributeSetter",
    "707": "V8Window_WebKitAnimationEvent_ConstructorGetter",
    "710": "CryptoGetRandomValues",
    "711": "SubtleCryptoEncrypt",
    "712": "SubtleCryptoDecrypt",
    "713": "SubtleCryptoSign",
    "714": "SubtleCryptoVerify",
    "715": "SubtleCryptoDigest",
    "716": "SubtleCryptoGenerateKey",
    "717": "SubtleCryptoImportKey",
    "718": "SubtleCryptoExportKey",
    "719": "SubtleCryptoDeriveBits",
    "720": "SubtleCryptoDeriveKey",
    "721": "SubtleCryptoWrapKey",
    "722": "SubtleCryptoUnwrapKey",
    "723": "CryptoAlgorithmAesCbc",
    "724": "CryptoAlgorithmHmac",
    "725": "CryptoAlgorithmRsaSsaPkcs1v1_5",
    "726": "CryptoAlgorithmSha1",
    "727": "CryptoAlgorithmSha256",
    "728": "CryptoAlgorithmSha384",
    "729": "CryptoAlgorithmSha512",
    "730": "CryptoAlgorithmAesGcm",
    "731": "CryptoAlgorithmRsaOaep",
    "732": "CryptoAlgorithmAesCtr",
    "733": "CryptoAlgorithmAesKw",
    "734": "CryptoAlgorithmRsaPss",
    "735": "CryptoAlgorithmEcdsa",
    "736": "CryptoAlgorithmEcdh",
    "737": "CryptoAlgorithmHkdf",
    "738": "CryptoAlgorithmPbkdf2",
    "739": "DocumentSetDomain",
    "740": "UpgradeInsecureRequestsEnabled",
    "741": "UpgradeInsecureRequestsUpgradedRequest",
    "742": "DocumentDesignMode",
    "743": "GlobalCacheStorage",
    "744": "NetInfo",
    "745": "BackgroundSync",
    "748": "LegacyConst",
    "750": "V8Permissions_Query_Method",
    "754": "V8HTMLInputElement_Autocapitalize_AttributeGetter",
    "755": "V8HTMLInputElement_Autocapitalize_AttributeSetter",
    "756": "V8HTMLTextAreaElement_Autocapitalize_AttributeGetter",
    "757": "V8HTMLTextAreaElement_Autocapitalize_AttributeSetter",
    "758": "SVGHrefBaseVal",
    "759": "SVGHrefAnimVal",
    "760": "V8CSSRuleList_Item_Method",
    "761": "V8MediaList_Item_Method",
    "762": "V8StyleSheetList_Item_Method",
    "763": "StyleSheetListAnonymousNamedGetter",
    "764": "AutocapitalizeAttribute",
    "765": "FullscreenSecureOrigin",
    "766": "FullscreenInsecureOrigin",
    "767": "DialogInSandboxedContext",
    "768": "SVGSMILAnimationInImageRegardlessOfCache",
    "770": "EncryptedMediaSecureOrigin",
    "771": "EncryptedMediaInsecureOrigin",
    "772": "PerformanceFrameTiming",
    "773": "V8Element_Animate_Method",
    "778": "V8SVGSVGElement_GetElementById_Method",
    "779": "ElementCreateShadowRootMultiple",
    "780": "V8MessageChannel_Constructor",
    "781": "V8MessagePort_PostMessage_Method",
    "782": "V8MessagePort_Start_Method",
    "783": "V8MessagePort_Close_Method",
    "784": "MessagePortsTransferred",
    "785": "CSSKeyframesRuleAnonymousIndexedGetter",
    "786": "V8Screen_AvailLeft_AttributeGetter",
    "787": "V8Screen_AvailTop_AttributeGetter",
    "791": "V8SVGFEConvolveMatrixElement_PreserveAlpha_AttributeGetter",
    "798": "V8SVGStyleElement_Disabled_AttributeGetter",
    "799": "V8SVGStyleElement_Disabled_AttributeSetter",
    "801": "InputTypeFileSecureOrigin",
    "802": "InputTypeFileInsecureOrigin",
    "804": "ElementAttachShadow",
    "806": "V8SecurityPolicyViolationEvent_DocumentURI_AttributeGetter",
    "807": "V8SecurityPolicyViolationEvent_BlockedURI_AttributeGetter",
    "808": "V8SecurityPolicyViolationEvent_StatusCode_AttributeGetter",
    "809": "HTMLLinkElementDisabled",
    "810": "V8HTMLLinkElement_Disabled_AttributeGetter",
    "811": "V8HTMLLinkElement_Disabled_AttributeSetter",
    "812": "V8HTMLStyleElement_Disabled_AttributeGetter",
    "813": "V8HTMLStyleElement_Disabled_AttributeSetter",
    "816": "V8DOMError_Constructor",
    "817": "V8DOMError_Name_AttributeGetter",
    "818": "V8DOMError_Message_AttributeGetter",
    "823": "V8Location_AncestorOrigins_AttributeGetter",
    "824": "V8IDBDatabase_ObjectStoreNames_AttributeGetter",
    "825": "V8IDBObjectStore_IndexNames_AttributeGetter",
    "826": "V8IDBTransaction_ObjectStoreNames_AttributeGetter",
    "830": "TextInputFired",
    "831": "V8TextEvent_Data_AttributeGetter",
    "832": "V8TextEvent_InitTextEvent_Method",
    "833": "V8SVGSVGElement_UseCurrentView_AttributeGetter",
    "834": "V8SVGSVGElement_CurrentView_AttributeGetter",
    "835": "ClientHintsDPR",
    "836": "ClientHintsResourceWidth",
    "837": "ClientHintsViewportWidth",
    "838": "SRIElementIntegrityAttributeButIneligible",
    "839": "FormDataAppendFile",
    "840": "FormDataAppendFileWithFilename",
    "841": "FormDataAppendBlob",
    "842": "FormDataAppendBlobWithFilename",
    "843": "FormDataAppendNull",
    "844": "HTMLDocumentCreateAttributeNameNotLowercase",
    "845": "NonHTMLElementSetAttributeNodeFromHTMLDocumentNameNotLowercase",
    "846": "DOMStringList_Item_AttributeGetter_IndexedDB",
    "847": "DOMStringList_Item_AttributeGetter_Location",
    "848": "DOMStringList_Contains_Method_IndexedDB",
    "849": "DOMStringList_Contains_Method_Location",
    "850": "NavigatorVibrate",
    "851": "NavigatorVibrateSubFrame",
    "853": "V8XPathEvaluator_Constructor",
    "854": "V8XPathEvaluator_CreateExpression_Method",
    "855": "V8XPathEvaluator_CreateNSResolver_Method",
    "856": "V8XPathEvaluator_Evaluate_Method",
    "857": "RequestMIDIAccess",
    "858": "V8MouseEvent_LayerX_AttributeGetter",
    "859": "V8MouseEvent_LayerY_AttributeGetter",
    "860": "InnerTextWithShadowTree",
    "861": "SelectionToStringWithShadowTree",
    "862": "WindowFindWithShadowTree",
    "863": "V8CompositionEvent_InitCompositionEvent_Method",
    "864": "V8CustomEvent_InitCustomEvent_Method",
    "865": "V8DeviceMotionEvent_InitDeviceMotionEvent_Method",
    "866": "V8DeviceOrientationEvent_InitDeviceOrientationEvent_Method",
    "867": "V8Event_InitEvent_Method",
    "868": "V8KeyboardEvent_InitKeyboardEvent_Method",
    "869": "V8MouseEvent_InitMouseEvent_Method",
    "870": "V8MutationEvent_InitMutationEvent_Method",
    "871": "V8StorageEvent_InitStorageEvent_Method",
    "872": "V8TouchEvent_InitTouchEvent_Method",
    "873": "V8UIEvent_InitUIEvent_Method",
    "874": "V8Document_CreateTouch_Method",
    "876": "RequestFileSystemNonWebbyOrigin",
    "879": "V8MemoryInfo_TotalJSHeapSize_AttributeGetter",
    "880": "V8MemoryInfo_UsedJSHeapSize_AttributeGetter",
    "881": "V8MemoryInfo_JSHeapSizeLimit_AttributeGetter",
    "882": "V8Performance_Timing_AttributeGetter",
    "883": "V8Performance_Navigation_AttributeGetter",
    "884": "V8Performance_Memory_AttributeGetter",
    "885": "V8SharedWorker_WorkerStart_AttributeGetter",
    "886": "HTMLKeygenElement",
    "892": "HTMLMediaElementPreloadNone",
    "893": "HTMLMediaElementPreloadMetadata",
    "894": "HTMLMediaElementPreloadAuto",
    "895": "HTMLMediaElementPreloadDefault",
    "896": "MixedContentBlockableAllowed",
    "897": "PseudoBeforeAfterForInputElement",
    "898": "V8Permissions_Revoke_Method",
    "899": "LinkRelDnsPrefetch",
    "900": "LinkRelPreconnect",
    "901": "LinkRelPreload",
    "902": "LinkHeaderDnsPrefetch",
    "903": "LinkHeaderPreconnect",
    "904": "ClientHintsMetaAcceptCH",
    "905": "HTMLElementDeprecatedWidth",
    "906": "ClientHintsContentDPR",
    "907": "ElementAttachShadowOpen",
    "908": "ElementAttachShadowClosed",
    "909": "AudioParamSetValueAtTime",
    "910": "AudioParamLinearRampToValueAtTime",
    "911": "AudioParamExponentialRampToValueAtTime",
    "912": "AudioParamSetTargetAtTime",
    "913": "AudioParamSetValueCurveAtTime",
    "914": "AudioParamCancelScheduledValues",
    "915": "V8Permissions_Request_Method",
    "917": "LinkRelPrefetch",
    "918": "LinkRelPrerender",
    "919": "LinkRelNext",
    "920": "PrefixedPerformanceResourceTimingBufferFull",
    "921": "CSSValuePrefixedMinContent",
    "922": "CSSValuePrefixedMaxContent",
    "923": "CSSValuePrefixedFitContent",
    "924": "CSSValuePrefixedFillAvailable",
    "926": "PresentationDefaultRequest",
    "927": "PresentationAvailabilityChangeEventListener",
    "928": "PresentationRequestConstructor",
    "929": "PresentationRequestStart",
    "930": "PresentationRequestReconnect",
    "931": "PresentationRequestGetAvailability",
    "932": "PresentationRequestConnectionAvailableEventListener",
    "933": "PresentationConnectionTerminate",
    "934": "PresentationConnectionSend",
    "936": "PresentationConnectionMessageEventListener",
    "937": "CSSAnimationsStackedNeutralKeyframe",
    "938": "ReadingCheckedInClickHandler",
    "939": "FlexboxIntrinsicSizeAlgorithmIsDifferent",
    "940": "HTMLImportsHasStyleSheets",
    "944": "ClipPathOfPositionedElement",
    "945": "ClipCssOfPositionedElement",
    "946": "NetInfoType",
    "947": "NetInfoDownlinkMax",
    "948": "NetInfoOnChange",
    "949": "NetInfoOnTypeChange",
    "950": "V8Window_Alert_Method",
    "951": "V8Window_Confirm_Method",
    "952": "V8Window_Prompt_Method",
    "953": "V8Window_Print_Method",
    "954": "V8Window_RequestIdleCallback_Method",
    "955": "FlexboxPercentagePaddingVertical",
    "956": "FlexboxPercentageMarginVertical",
    "957": "BackspaceNavigatedBack",
    "958": "BackspaceNavigatedBackAfterFormInteraction",
    "959": "CSPSourceWildcardWouldMatchExactHost",
    "960": "CredentialManagerGet",
    "961": "CredentialManagerGetWithUI",
    "962": "CredentialManagerGetWithoutUI",
    "963": "CredentialManagerStore",
    "964": "CredentialManagerRequireUserMediation",
    "966": "BlockableMixedContentInSubframeBlocked",
    "967": "AddEventListenerThirdArgumentIsObject",
    "968": "RemoveEventListenerThirdArgumentIsObject",
    "969": "CSSAtRuleCharset",
    "970": "CSSAtRuleFontFace",
    "971": "CSSAtRuleImport",
    "972": "CSSAtRuleKeyframes",
    "973": "CSSAtRuleMedia",
    "974": "CSSAtRuleNamespace",
    "975": "CSSAtRulePage",
    "976": "CSSAtRuleSupports",
    "977": "CSSAtRuleViewport",
    "978": "CSSAtRuleWebkitKeyframes",
    "979": "V8HTMLFieldSetElement_Elements_AttributeGetter",
    "980": "HTMLMediaElementPreloadForcedNone",
    "981": "ExternalAddSearchProvider",
    "982": "ExternalIsSearchProviderInstalled",
    "983": "V8Permissions_RequestAll_Method",
    "987": "DeviceOrientationAbsoluteInsecureOrigin",
    "988": "DeviceOrientationAbsoluteSecureOrigin",
    "989": "FontFaceConstructor",
    "990": "ServiceWorkerControlledPage",
    "993": "MeterElementWithMeterAppearance",
    "994": "MeterElementWithNoneAppearance",
    "997": "SelectionAnchorNode",
    "998": "SelectionAnchorOffset",
    "999": "SelectionFocusNode",
    "1000": "SelectionFocusOffset",
    "1001": "SelectionIsCollapsed",
    "1002": "SelectionRangeCount",
    "1003": "SelectionGetRangeAt",
    "1004": "SelectionAddRange",
    "1005": "SelectionRemoveAllRanges",
    "1006": "SelectionCollapse",
    "1007": "SelectionCollapseToStart",
    "1008": "SelectionCollapseToEnd",
    "1009": "SelectionExtend",
    "1010": "SelectionSelectAllChildren",
    "1011": "SelectionDeleteDromDocument",
    "1012": "SelectionDOMString",
    "1013": "InputTypeRangeVerticalAppearance",
    "1014": "CSSFilterReference",
    "1015": "CSSFilterGrayscale",
    "1016": "CSSFilterSepia",
    "1017": "CSSFilterSaturate",
    "1018": "CSSFilterHueRotate",
    "1019": "CSSFilterInvert",
    "1020": "CSSFilterOpacity",
    "1021": "CSSFilterBrightness",
    "1022": "CSSFilterContrast",
    "1023": "CSSFilterBlur",
    "1024": "CSSFilterDropShadow",
    "1025": "BackgroundSyncRegister",
    "1027": "ExecCommandOnInputOrTextarea",
    "1028": "V8History_ScrollRestoration_AttributeGetter",
    "1029": "V8History_ScrollRestoration_AttributeSetter",
    "1030": "SVG1DOMFilter",
    "1031": "OfflineAudioContextStartRendering",
    "1032": "OfflineAudioContextSuspend",
    "1033": "OfflineAudioContextResume",
    "1034": "AttrCloneNode",
    "1035": "SVG1DOMPaintServer",
    "1036": "SVGSVGElementFragmentSVGView",
    "1037": "SVGSVGElementFragmentSVGViewElement",
    "1038": "PresentationConnectionClose",
    "1039": "SVG1DOMShape",
    "1040": "SVG1DOMText",
    "1041": "RTCPeerConnectionConstructorConstraints",
    "1042": "RTCPeerConnectionConstructorCompliant",
    "1044": "RTCPeerConnectionCreateOfferLegacyFailureCallback",
    "1045": "RTCPeerConnectionCreateOfferLegacyConstraints",
    "1046": "RTCPeerConnectionCreateOfferLegacyOfferOptions",
    "1047": "RTCPeerConnectionCreateOfferLegacyCompliant",
    "1049": "RTCPeerConnectionCreateAnswerLegacyFailureCallback",
    "1050": "RTCPeerConnectionCreateAnswerLegacyConstraints",
    "1051": "RTCPeerConnectionCreateAnswerLegacyCompliant",
    "1052": "RTCPeerConnectionSetLocalDescriptionLegacyNoSuccessCallback",
    "1053": "RTCPeerConnectionSetLocalDescriptionLegacyNoFailureCallback",
    "1054": "RTCPeerConnectionSetLocalDescriptionLegacyCompliant",
    "1055": "RTCPeerConnectionSetRemoteDescriptionLegacyNoSuccessCallback",
    "1056": "RTCPeerConnectionSetRemoteDescriptionLegacyNoFailureCallback",
    "1057": "RTCPeerConnectionSetRemoteDescriptionLegacyCompliant",
    "1058": "RTCPeerConnectionGetStatsLegacyNonCompliant",
    "1059": "NodeFilterIsFunction",
    "1060": "NodeFilterIsObject",
    "1062": "CSSSelectorInternalPseudoListBox",
    "1063": "CSSSelectorInternalMediaControlsCastButton",
    "1064": "CSSSelectorInternalMediaControlsOverlayCastButton",
    "1065": "CSSSelectorInternalPseudoSpatialNavigationFocus",
    "1066": "SameOriginTextScript",
    "1067": "SameOriginApplicationScript",
    "1068": "SameOriginOtherScript",
    "1069": "CrossOriginTextScript",
    "1070": "CrossOriginApplicationScript",
    "1071": "CrossOriginOtherScript",
    "1072": "SVG1DOMSVGTests",
    "1073": "V8SVGViewElement_ViewTarget_AttributeGetter",
    "1074": "DisableRemotePlaybackAttribute",
    "1075": "V8SloppyMode",
    "1076": "V8StrictMode",
    "1077": "V8StrongMode",
    "1078": "AudioNodeConnectToAudioNode",
    "1079": "AudioNodeConnectToAudioParam",
    "1080": "AudioNodeDisconnectFromAudioNode",
    "1081": "AudioNodeDisconnectFromAudioParam",
    "1082": "V8CSSFontFaceRule_Style_AttributeGetter",
    "1083": "SelectionCollapseNull",
    "1084": "SelectionSetBaseAndExtentNull",
    "1085": "V8SVGSVGElement_CreateSVGNumber_Method",
    "1086": "V8SVGSVGElement_CreateSVGLength_Method",
    "1087": "V8SVGSVGElement_CreateSVGAngle_Method",
    "1088": "V8SVGSVGElement_CreateSVGPoint_Method",
    "1089": "V8SVGSVGElement_CreateSVGMatrix_Method",
    "1090": "V8SVGSVGElement_CreateSVGRect_Method",
    "1091": "V8SVGSVGElement_CreateSVGTransform_Method",
    "1092": "V8SVGSVGElement_CreateSVGTransformFromMatrix_Method",
    "1093": "FormNameAccessForNonDescendantImageElement",
    "1095": "V8SVGSVGElement_Viewport_AttributeGetter",
    "1096": "V8RegExpPrototypeStickyGetter",
    "1097": "V8RegExpPrototypeToString",
    "1098": "V8InputDeviceCapabilities_FiresTouchEvents_AttributeGetter",
    "1099": "DataElement",
    "1100": "TimeElement",
    "1101": "SVG1DOMUriReference",
    "1102": "SVG1DOMZoomAndPan",
    "1103": "V8SVGGraphicsElement_Transform_AttributeGetter",
    "1104": "MenuItemElement",
    "1105": "MenuItemCloseTag",
    "1106": "SVG1DOMMarkerElement",
    "1107": "SVG1DOMUseElement",
    "1108": "SVG1DOMMaskElement",
    "1109": "V8SVGAElement_Target_AttributeGetter",
    "1110": "V8SVGClipPathElement_ClipPathUnits_AttributeGetter",
    "1111": "SVG1DOMFitToViewBox",
    "1112": "SVG1DOMCursorElement",
    "1113": "V8SVGPathElement_PathLength_AttributeGetter",
    "1114": "SVG1DOMSVGElement",
    "1115": "SVG1DOMImageElement",
    "1116": "SVG1DOMForeignObjectElement",
    "1117": "AudioContextCreateIIRFilter",
    "1118": "CSSSelectorPseudoSlotted",
    "1119": "MediaDevicesEnumerateDevices",
    "1120": "NonSecureSharedWorkerAccessedFromSecureContext",
    "1121": "SecureSharedWorkerAccessedFromNonSecureContext",
    "1123": "EventComposedPath",
    "1124": "LinkHeaderPreload",
    "1125": "MouseWheelEvent",
    "1126": "WheelEvent",
    "1127": "MouseWheelAndWheelEvent",
    "1128": "BodyScrollsInAdditionToViewport",
    "1129": "DocumentDesignModeEnabeld",
    "1130": "ContentEditableTrue",
    "1131": "ContentEditableTrueOnHTML",
    "1132": "ContentEditablePlainTextOnly",
    "1133": "V8RegExpPrototypeUnicodeGetter",
    "1134": "V8IntlV8Parse",
    "1135": "V8IntlPattern",
    "1136": "V8IntlResolved",
    "1137": "V8PromiseChain",
    "1138": "V8PromiseAccept",
    "1139": "V8PromiseDefer",
    "1140": "EventComposed",
    "1141": "GeolocationInsecureOriginIframe",
    "1142": "GeolocationSecureOriginIframe",
    "1143": "RequestMIDIAccessIframe",
    "1144": "GetUserMediaInsecureOriginIframe",
    "1145": "GetUserMediaSecureOriginIframe",
    "1146": "ElementRequestPointerLockIframe",
    "1147": "NotificationAPIInsecureOriginIframe",
    "1148": "NotificationAPISecureOriginIframe",
    "1149": "WebSocket",
    "1150": "MediaStreamConstraintsNameValue",
    "1151": "MediaStreamConstraintsFromDictionary",
    "1152": "MediaStreamConstraintsConformant",
    "1153": "CSSSelectorIndirectAdjacent",
    "1156": "CreateImageBitmap",
    "1157": "PresentationConnectionConnectEventListener",
    "1158": "PresentationConnectionCloseEventListener",
    "1159": "PresentationConnectionTerminateEventListener",
    "1160": "DocumentCreateEventFontFaceSetLoadEvent",
    "1161": "DocumentCreateEventMediaQueryListEvent",
    "1162": "DocumentCreateEventAnimationEvent",
    "1164": "DocumentCreateEventApplicationCacheErrorEvent",
    "1166": "DocumentCreateEventBeforeUnloadEvent",
    "1167": "DocumentCreateEventClipboardEvent",
    "1168": "DocumentCreateEventCompositionEvent",
    "1169": "DocumentCreateEventDragEvent",
    "1170": "DocumentCreateEventErrorEvent",
    "1171": "DocumentCreateEventFocusEvent",
    "1172": "DocumentCreateEventHashChangeEvent",
    "1173": "DocumentCreateEventMutationEvent",
    "1174": "DocumentCreateEventPageTransitionEvent",
    "1176": "DocumentCreateEventPopStateEvent",
    "1177": "DocumentCreateEventProgressEvent",
    "1178": "DocumentCreateEventPromiseRejectionEvent",
    "1180": "DocumentCreateEventResourceProgressEvent",
    "1181": "DocumentCreateEventSecurityPolicyViolationEvent",
    "1182": "DocumentCreateEventTextEvent",
    "1183": "DocumentCreateEventTransitionEvent",
    "1184": "DocumentCreateEventWheelEvent",
    "1186": "DocumentCreateEventTrackEvent",
    "1187": "DocumentCreateEventWebKitAnimationEvent",
    "1188": "DocumentCreateEventMutationEvents",
    "1189": "DocumentCreateEventOrientationEvent",
    "1190": "DocumentCreateEventSVGEvents",
    "1191": "DocumentCreateEventWebKitTransitionEvent",
    "1192": "DocumentCreateEventBeforeInstallPromptEvent",
    "1193": "DocumentCreateEventSyncEvent",
    "1195": "DocumentCreateEventDeviceMotionEvent",
    "1196": "DocumentCreateEventDeviceOrientationEvent",
    "1197": "DocumentCreateEventMediaEncryptedEvent",
    "1198": "DocumentCreateEventMediaKeyMessageEvent",
    "1199": "DocumentCreateEventGamepadEvent",
    "1201": "DocumentCreateEventIDBVersionChangeEvent",
    "1202": "DocumentCreateEventBlobEvent",
    "1203": "DocumentCreateEventMediaStreamEvent",
    "1204": "DocumentCreateEventMediaStreamTrackEvent",
    "1205": "DocumentCreateEventRTCDTMFToneChangeEvent",
    "1206": "DocumentCreateEventRTCDataChannelEvent",
    "1207": "DocumentCreateEventRTCIceCandidateEvent",
    "1209": "DocumentCreateEventNotificationEvent",
    "1210": "DocumentCreateEventPresentationConnectionAvailableEvent",
    "1211": "DocumentCreateEventPresentationConnectionCloseEvent",
    "1212": "DocumentCreateEventPushEvent",
    "1213": "DocumentCreateEventExtendableEvent",
    "1214": "DocumentCreateEventExtendableMessageEvent",
    "1215": "DocumentCreateEventFetchEvent",
    "1217": "DocumentCreateEventServiceWorkerMessageEvent",
    "1218": "DocumentCreateEventSpeechRecognitionError",
    "1219": "DocumentCreateEventSpeechRecognitionEvent",
    "1220": "DocumentCreateEventSpeechSynthesisEvent",
    "1221": "DocumentCreateEventStorageEvent",
    "1222": "DocumentCreateEventAudioProcessingEvent",
    "1223": "DocumentCreateEventOfflineAudioCompletionEvent",
    "1224": "DocumentCreateEventWebGLContextEvent",
    "1225": "DocumentCreateEventMIDIConnectionEvent",
    "1226": "DocumentCreateEventMIDIMessageEvent",
    "1227": "DocumentCreateEventCloseEvent",
    "1228": "DocumentCreateEventKeyboardEvents",
    "1229": "HTMLMediaElement",
    "1230": "HTMLMediaElementInDocument",
    "1231": "HTMLMediaElementControlsAttribute",
    "1233": "V8Animation_Oncancel_AttributeGetter",
    "1234": "V8Animation_Oncancel_AttributeSetter",
    "1235": "V8HTMLCommentInExternalScript",
    "1236": "V8HTMLComment",
    "1237": "V8SloppyModeBlockScopedFunctionRedefinition",
    "1238": "V8ForInInitializer",
    "1239": "V8Animation_Id_AttributeGetter",
    "1240": "V8Animation_Id_AttributeSetter",
    "1243": "WebAnimationHyphenatedProperty",
    "1244": "FormControlsCollectionReturnsRadioNodeListForFieldSet",
    "1245": "ApplicationCacheManifestSelectInsecureOrigin",
    "1246": "ApplicationCacheManifestSelectSecureOrigin",
    "1247": "ApplicationCacheAPIInsecureOrigin",
    "1248": "ApplicationCacheAPISecureOrigin",
    "1249": "CSSAtRuleApply",
    "1250": "CSSSelectorPseudoAny",
    "1251": "PannerNodeSetVelocity",
    "1252": "DocumentAllItemNoArguments",
    "1253": "DocumentAllItemNamed",
    "1254": "DocumentAllItemIndexed",
    "1255": "DocumentAllItemIndexedWithNonNumber",
    "1256": "DocumentAllLegacyCallNoArguments",
    "1257": "DocumentAllLegacyCallNamed",
    "1258": "DocumentAllLegacyCallIndexed",
    "1259": "DocumentAllLegacyCallIndexedWithNonNumber",
    "1260": "DocumentAllLegacyCallTwoArguments",
    "1263": "HTMLLabelElementControlForNonFormAssociatedElement",
    "1265": "HTMLMediaElementLoadNetworkEmptyNotPaused",
    "1267": "V8Window_WebkitSpeechGrammar_ConstructorGetter",
    "1268": "V8Window_WebkitSpeechGrammarList_ConstructorGetter",
    "1269": "V8Window_WebkitSpeechRecognition_ConstructorGetter",
    "1270": "V8Window_WebkitSpeechRecognitionError_ConstructorGetter",
    "1271": "V8Window_WebkitSpeechRecognitionEvent_ConstructorGetter",
    "1272": "V8Window_SpeechSynthesis_AttributeGetter",
    "1273": "V8IDBFactory_WebkitGetDatabaseNames_Method",
    "1274": "ImageDocument",
    "1275": "ScriptPassesCSPDynamic",
    "1277": "CSPWithStrictDynamic",
    "1278": "ScrollAnchored",
    "1279": "AddEventListenerFourArguments",
    "1280": "RemoveEventListenerFourArguments",
    "1281": "InvalidReportUriDirectiveInMetaCSP",
    "1282": "InvalidSandboxDirectiveInMetaCSP",
    "1283": "InvalidFrameAncestorsDirectiveInMetaCSP",
    "1287": "SVGCalcModeDiscrete",
    "1288": "SVGCalcModeLinear",
    "1289": "SVGCalcModePaced",
    "1290": "SVGCalcModeSpline",
    "1291": "FormSubmissionStarted",
    "1292": "FormValidationStarted",
    "1293": "FormValidationAbortedSubmission",
    "1294": "FormValidationShowedMessage",
    "1295": "WebAnimationsEasingAsFunctionLinear",
    "1296": "WebAnimationsEasingAsFunctionOther",
    "1297": "V8Document_Images_AttributeGetter",
    "1298": "V8Document_Embeds_AttributeGetter",
    "1299": "V8Document_Plugins_AttributeGetter",
    "1300": "V8Document_Links_AttributeGetter",
    "1301": "V8Document_Forms_AttributeGetter",
    "1302": "V8Document_Scripts_AttributeGetter",
    "1303": "V8Document_Anchors_AttributeGetter",
    "1304": "V8Document_Applets_AttributeGetter",
    "1305": "XMLHttpRequestCrossOriginWithCredentials",
    "1306": "MediaStreamTrackRemote",
    "1307": "V8Node_IsConnected_AttributeGetter",
    "1308": "ShadowRootDelegatesFocus",
    "1309": "MixedShadowRootV0AndV1",
    "1310": "ImageDocumentInFrame",
    "1311": "MediaDocument",
    "1312": "MediaDocumentInFrame",
    "1313": "PluginDocument",
    "1314": "PluginDocumentInFrame",
    "1315": "SinkDocument",
    "1316": "SinkDocumentInFrame",
    "1317": "TextDocument",
    "1318": "TextDocumentInFrame",
    "1319": "ViewSourceDocument",
    "1320": "FileAPINativeLineEndings",
    "1321": "PointerEventAttributeCount",
    "1322": "CompositedReplication",
    "1323": "EncryptedMediaAllSelectedContentTypesHaveCodecs",
    "1324": "EncryptedMediaAllSelectedContentTypesMissingCodecs",
    "1325": "V8DataTransferItem_WebkitGetAsEntry_Method",
    "1326": "V8HTMLInputElement_WebkitEntries_AttributeGetter",
    "1327": "Entry_Filesystem_AttributeGetter_IsolatedFileSystem",
    "1328": "Entry_GetMetadata_Method_IsolatedFileSystem",
    "1329": "Entry_MoveTo_Method_IsolatedFileSystem",
    "1330": "Entry_CopyTo_Method_IsolatedFileSystem",
    "1331": "Entry_Remove_Method_IsolatedFileSystem",
    "1332": "Entry_GetParent_Method_IsolatedFileSystem",
    "1333": "Entry_ToURL_Method_IsolatedFileSystem",
    "1334": "During_Microtask_Alert",
    "1335": "During_Microtask_Confirm",
    "1336": "During_Microtask_Print",
    "1337": "During_Microtask_Prompt",
    "1338": "During_Microtask_SyncXHR",
    "1342": "CredentialManagerGetReturnedCredential",
    "1343": "GeolocationInsecureOriginDeprecatedNotRemoved",
    "1344": "GeolocationInsecureOriginIframeDeprecatedNotRemoved",
    "1345": "ProgressElementWithNoneAppearance",
    "1346": "ProgressElementWithProgressBarAppearance",
    "1347": "PointerEventAddListenerCount",
    "1348": "EventCancelBubbleAffected",
    "1349": "EventCancelBubbleWasChangedToTrue",
    "1350": "EventCancelBubbleWasChangedToFalse",
    "1351": "CSSValueAppearanceNone",
    "1352": "CSSValueAppearanceNotNone",
    "1353": "CSSValueAppearanceOthers",
    "1354": "CSSValueAppearanceButton",
    "1355": "CSSValueAppearanceCaret",
    "1356": "CSSValueAppearanceCheckbox",
    "1357": "CSSValueAppearanceMenulist",
    "1358": "CSSValueAppearanceMenulistButton",
    "1359": "CSSValueAppearanceListbox",
    "1360": "CSSValueAppearanceRadio",
    "1361": "CSSValueAppearanceSearchField",
    "1362": "CSSValueAppearanceTextField",
    "1363": "AudioContextCreatePannerAutomated",
    "1364": "PannerNodeSetPosition",
    "1365": "PannerNodeSetOrientation",
    "1366": "AudioListenerSetPosition",
    "1367": "AudioListenerSetOrientation",
    "1368": "IntersectionObserver_Constructor",
    "1369": "DurableStoragePersist",
    "1370": "DurableStoragePersisted",
    "1371": "DurableStorageEstimate",
    "1372": "UntrustedEventDefaultHandled",
    "1375": "CSSDeepCombinatorAndShadow",
    "1376": "OpacityWithPreserve3DQuirk",
    "1377": "CSSSelectorPseudoReadOnly",
    "1378": "CSSSelectorPseudoReadWrite",
    "1379": "UnloadHandler_Navigation",
    "1380": "TouchStartUserGestureUtilized",
    "1381": "TouchMoveUserGestureUtilized",
    "1382": "TouchEndDuringScrollUserGestureUtilized",
    "1383": "CSSSelectorPseudoDefined",
    "1384": "RTCPeerConnectionAddIceCandidatePromise",
    "1385": "RTCPeerConnectionAddIceCandidateLegacy",
    "1386": "RTCIceCandidateDefaultSdpMLineIndex",
    "1389": "MediaStreamConstraintsOldAndNew",
    "1390": "V8ArrayProtectorDirtied",
    "1391": "V8ArraySpeciesModified",
    "1392": "V8ArrayPrototypeConstructorModified",
    "1393": "V8ArrayInstanceProtoModified",
    "1394": "V8ArrayInstanceConstructorModified",
    "1395": "V8LegacyFunctionDeclaration",
    "1396": "V8RegExpPrototypeSourceGetter",
    "1397": "V8RegExpPrototypeOldFlagGetter",
    "1398": "V8DecimalWithLeadingZeroInStrictMode",
    "1399": "FormSubmissionNotInDocumentTree",
    "1400": "GetUserMediaPrefixed",
    "1401": "GetUserMediaLegacy",
    "1402": "GetUserMediaPromise",
    "1403": "CSSFilterFunctionNoArguments",
    "1404": "V8LegacyDateParser",
    "1405": "OpenSearchInsecureOriginInsecureTarget",
    "1406": "OpenSearchInsecureOriginSecureTarget",
    "1407": "OpenSearchSecureOriginInsecureTarget",
    "1408": "OpenSearchSecureOriginSecureTarget",
    "1409": "RegisterProtocolHandlerSecureOrigin",
    "1410": "RegisterProtocolHandlerInsecureOrigin",
    "1411": "CrossOriginWindowAlert",
    "1412": "CrossOriginWindowConfirm",
    "1413": "CrossOriginWindowPrompt",
    "1414": "CrossOriginWindowPrint",
    "1415": "MediaStreamOnActive",
    "1416": "MediaStreamOnInactive",
    "1417": "AddEventListenerPassiveTrue",
    "1418": "AddEventListenerPassiveFalse",
    "1419": "CSPReferrerDirective",
    "1420": "DocumentOpen",
    "1421": "ElementRequestPointerLockInShadow",
    "1422": "ShadowRootPointerLockElement",
    "1423": "DocumentPointerLockElementInV0Shadow",
    "1424": "TextAreaMaxLength",
    "1425": "TextAreaMinLength",
    "1426": "TopNavigationFromSubFrame",
    "1427": "PrefixedElementRequestFullscreenInShadow",
    "1428": "MediaSourceAbortRemove",
    "1429": "MediaSourceDurationTruncatingBuffered",
    "1430": "AudioContextCrossOriginIframe",
    "1431": "PointerEventSetCapture",
    "1432": "PointerEventDispatch",
    "1433": "MIDIMessageEventReceivedTime",
    "1434": "SummaryElementWithDisplayBlockAuthorRule",
    "1435": "V8MediaStream_Active_AttributeGetter",
    "1436": "BeforeInstallPromptEvent",
    "1437": "BeforeInstallPromptEventUserChoice",
    "1438": "BeforeInstallPromptEventPreventDefault",
    "1439": "BeforeInstallPromptEventPrompt",
    "1440": "ExecCommandAltersHTMLStructure",
    "1441": "SecureContextCheckPassed",
    "1442": "SecureContextCheckFailed",
    "1443": "SecureContextCheckForSandboxedOriginPassed",
    "1444": "SecureContextCheckForSandboxedOriginFailed",
    "1445": "V8DefineGetterOrSetterWouldThrow",
    "1446": "V8FunctionConstructorReturnedUndefined",
    "1447": "V8BroadcastChannel_Constructor",
    "1448": "V8BroadcastChannel_PostMessage_Method",
    "1449": "V8BroadcastChannel_Close_Method",
    "1450": "TouchStartFired",
    "1451": "MouseDownFired",
    "1452": "PointerDownFired",
    "1453": "PointerDownFiredForTouch",
    "1454": "PointerEventDispatchPointerDown",
    "1455": "SVGSMILBeginOrEndEventValue",
    "1456": "SVGSMILBeginOrEndSyncbaseValue",
    "1457": "SVGSMILElementInsertedAfterLoad",
    "1458": "V8VisualViewport_ScrollLeft_AttributeGetter",
    "1459": "V8VisualViewport_ScrollTop_AttributeGetter",
    "1460": "V8VisualViewport_PageX_AttributeGetter",
    "1461": "V8VisualViewport_PageY_AttributeGetter",
    "1462": "V8VisualViewport_ClientWidth_AttributeGetter",
    "1463": "V8VisualViewport_ClientHeight_AttributeGetter",
    "1464": "V8VisualViewport_Scale_AttributeGetter",
    "1465": "VisualViewportScrollFired",
    "1466": "VisualViewportResizeFired",
    "1467": "NodeGetRootNode",
    "1468": "SlotChangeEventAddListener",
    "1469": "CSSValueAppearanceButtonRendered",
    "1470": "CSSValueAppearanceButtonForAnchor",
    "1471": "CSSValueAppearanceButtonForButton",
    "1472": "CSSValueAppearanceButtonForOtherButtons",
    "1473": "CSSValueAppearanceTextFieldRendered",
    "1474": "CSSValueAppearanceTextFieldForSearch",
    "1475": "CSSValueAppearanceTextFieldForTextField",
    "1476": "RTCPeerConnectionGetStats",
    "1477": "SVGSMILAnimationAppliedEffect",
    "1478": "PerformanceResourceTimingSizes",
    "1479": "EventSourceDocument",
    "1480": "EventSourceWorker",
    "1481": "SingleOriginInTimingAllowOrigin",
    "1482": "MultipleOriginsInTimingAllowOrigin",
    "1483": "StarInTimingAllowOrigin",
    "1484": "SVGSMILAdditiveAnimation",
    "1485": "SendBeaconWithNonSimpleContentType",
    "1486": "ChromeLoadTimesRequestTime",
    "1487": "ChromeLoadTimesStartLoadTime",
    "1488": "ChromeLoadTimesCommitLoadTime",
    "1489": "ChromeLoadTimesFinishDocumentLoadTime",
    "1490": "ChromeLoadTimesFinishLoadTime",
    "1491": "ChromeLoadTimesFirstPaintTime",
    "1492": "ChromeLoadTimesFirstPaintAfterLoadTime",
    "1493": "ChromeLoadTimesNavigationType",
    "1494": "ChromeLoadTimesWasFetchedViaSpdy",
    "1495": "ChromeLoadTimesWasNpnNegotiated",
    "1496": "ChromeLoadTimesNpnNegotiatedProtocol",
    "1497": "ChromeLoadTimesWasAlternateProtocolAvailable",
    "1498": "ChromeLoadTimesConnectionInfo",
    "1499": "ChromeLoadTimesUnknown",
    "1500": "SVGViewElement",
    "1501": "WebShareShare",
    "1502": "AuxclickAddListenerCount",
    "1503": "HTMLCanvasElement",
    "1504": "SVGSMILAnimationElementTiming",
    "1505": "SVGSMILBeginEndAnimationElement",
    "1506": "SVGSMILPausing",
    "1507": "SVGSMILCurrentTime",
    "1508": "HTMLBodyElementOnSelectionChangeAttribute",
    "1509": "ForeignFetchInterception",
    "1510": "MapNameMatchingStrict",
    "1511": "MapNameMatchingASCIICaseless",
    "1512": "MapNameMatchingUnicodeLower",
    "1513": "RadioNameMatchingStrict",
    "1514": "RadioNameMatchingASCIICaseless",
    "1515": "RadioNameMatchingCaseFolding",
    "1517": "InputSelectionGettersThrow",
    "1519": "UsbGetDevices",
    "1520": "UsbRequestDevice",
    "1521": "UsbDeviceOpen",
    "1522": "UsbDeviceClose",
    "1523": "UsbDeviceSelectConfiguration",
    "1524": "UsbDeviceClaimInterface",
    "1525": "UsbDeviceReleaseInterface",
    "1526": "UsbDeviceSelectAlternateInterface",
    "1527": "UsbDeviceControlTransferIn",
    "1528": "UsbDeviceControlTransferOut",
    "1529": "UsbDeviceClearHalt",
    "1530": "UsbDeviceTransferIn",
    "1531": "UsbDeviceTransferOut",
    "1532": "UsbDeviceIsochronousTransferIn",
    "1533": "UsbDeviceIsochronousTransferOut",
    "1534": "UsbDeviceReset",
    "1535": "PointerEnterLeaveFired",
    "1536": "PointerOverOutFired",
    "1539": "DraggableAttribute",
    "1540": "CleanScriptElementWithNonce",
    "1541": "PotentiallyInjectedScriptElementWithNonce",
    "1542": "PendingStylesheetAddedAfterBodyStarted",
    "1543": "UntrustedMouseDownEventDispatchedToSelect",
    "1544": "BlockedSniffingAudioToScript",
    "1545": "BlockedSniffingVideoToScript",
    "1546": "BlockedSniffingCSVToScript",
    "1547": "MetaSetCookie",
    "1548": "MetaRefresh",
    "1549": "MetaSetCookieWhenCSPBlocksInlineScript",
    "1550": "MetaRefreshWhenCSPBlocksInlineScript",
    "1551": "MiddleClickAutoscrollStart",
    "1552": "ClipCssOfFixedPositionElement",
    "1553": "RTCPeerConnectionCreateOfferOptionsOfferToReceive",
    "1554": "DragAndDropScrollStart",
    "1555": "PresentationConnectionListConnectionAvailableEventListener",
    "1556": "WebAudioAutoplayCrossOriginIframe",
    "1557": "ScriptInvalidTypeOrLanguage",
    "1558": "VRGetDisplays",
    "1559": "VRPresent",
    "1560": "VRDeprecatedGetPose",
    "1561": "WebAudioAnalyserNode",
    "1562": "WebAudioAudioBuffer",
    "1563": "WebAudioAudioBufferSourceNode",
    "1564": "WebAudioBiquadFilterNode",
    "1565": "WebAudioChannelMergerNode",
    "1566": "WebAudioChannelSplitterNode",
    "1567": "WebAudioConvolverNode",
    "1568": "WebAudioDelayNode",
    "1569": "WebAudioDynamicsCompressorNode",
    "1570": "WebAudioGainNode",
    "1571": "WebAudioIIRFilterNode",
    "1572": "WebAudioMediaElementAudioSourceNode",
    "1573": "WebAudioOscillatorNode",
    "1574": "WebAudioPannerNode",
    "1575": "WebAudioPeriodicWave",
    "1576": "WebAudioStereoPannerNode",
    "1577": "WebAudioWaveShaperNode",
    "1578": "CSSZoomReset",
    "1579": "CSSZoomDocument",
    "1580": "PaymentAddressCareOf",
    "1581": "XSSAuditorBlockedScript",
    "1582": "XSSAuditorBlockedEntirePage",
    "1583": "XSSAuditorDisabled",
    "1584": "XSSAuditorEnabledFilter",
    "1585": "XSSAuditorEnabledBlock",
    "1586": "XSSAuditorInvalid",
    "1587": "SVGCursorElement",
    "1588": "SVGCursorElementHasClient",
    "1589": "TextInputEventOnInput",
    "1590": "TextInputEventOnTextArea",
    "1591": "TextInputEventOnContentEditable",
    "1592": "TextInputEventOnNotNode",
    "1593": "WebkitBeforeTextInsertedOnInput",
    "1594": "WebkitBeforeTextInsertedOnTextArea",
    "1595": "WebkitBeforeTextInsertedOnContentEditable",
    "1596": "WebkitBeforeTextInsertedOnNotNode",
    "1597": "WebkitEditableContentChangedOnInput",
    "1598": "WebkitEditableContentChangedOnTextArea",
    "1599": "WebkitEditableContentChangedOnContentEditable",
    "1600": "WebkitEditableContentChangedOnNotNode",
    "1601": "V8NavigatorUserMediaError_ConstraintName_AttributeGetter",
    "1602": "V8HTMLMediaElement_SrcObject_AttributeGetter",
    "1603": "V8HTMLMediaElement_SrcObject_AttributeSetter",
    "1604": "CreateObjectURLBlob",
    "1605": "CreateObjectURLMediaSource",
    "1606": "CreateObjectURLMediaStream",
    "1607": "DocumentCreateTouchWindowNull",
    "1608": "DocumentCreateTouchWindowWrongType",
    "1609": "DocumentCreateTouchTargetNull",
    "1610": "DocumentCreateTouchTargetWrongType",
    "1611": "DocumentCreateTouchLessThanSevenArguments",
    "1612": "DocumentCreateTouchMoreThanSevenArguments",
    "1613": "EncryptedMediaCapabilityProvided",
    "1614": "EncryptedMediaCapabilityNotProvided",
    "1615": "LongTaskObserver",
    "1616": "CSSMotionInEffect",
    "1617": "CSSOffsetInEffect",
    "1618": "VRGetDisplaysInsecureOrigin",
    "1619": "VRRequestPresent",
    "1620": "VRRequestPresentInsecureOrigin",
    "1621": "VRDeprecatedFieldOfView",
    "1622": "VideoInCanvas",
    "1623": "HiddenAutoplayedVideoInCanvas",
    "1624": "OffscreenCanvas",
    "1625": "GamepadPose",
    "1626": "GamepadHand",
    "1627": "GamepadDisplayId",
    "1628": "GamepadButtonTouched",
    "1629": "GamepadPoseHasOrientation",
    "1630": "GamepadPoseHasPosition",
    "1631": "GamepadPosePosition",
    "1632": "GamepadPoseLinearVelocity",
    "1633": "GamepadPoseLinearAcceleration",
    "1634": "GamepadPoseOrientation",
    "1635": "GamepadPoseAngularVelocity",
    "1636": "GamepadPoseAngularAcceleration",
    "1638": "V8RTCDataChannel_MaxRetransmitTime_AttributeGetter",
    "1639": "V8RTCDataChannel_MaxRetransmits_AttributeGetter",
    "1640": "V8RTCDataChannel_Reliable_AttributeGetter",
    "1641": "V8RTCPeerConnection_AddStream_Method",
    "1642": "V8RTCPeerConnection_CreateDTMFSender_Method",
    "1643": "V8RTCPeerConnection_GetLocalStreams_Method",
    "1644": "V8RTCPeerConnection_GetRemoteStreams_Method",
    "1645": "V8RTCPeerConnection_GetStreamById_Method",
    "1646": "V8RTCPeerConnection_RemoveStream_Method",
    "1647": "V8RTCPeerConnection_UpdateIce_Method",
    "1648": "RTCPeerConnectionCreateDataChannelMaxRetransmitTime",
    "1649": "RTCPeerConnectionCreateDataChannelMaxRetransmits",
    "1650": "AudioContextCreateConstantSource",
    "1651": "WebAudioConstantSourceNode",
    "1652": "LoopbackEmbeddedInSecureContext",
    "1653": "LoopbackEmbeddedInNonSecureContext",
    "1654": "BlinkMacSystemFont",
    "1655": "RTCConfigurationIceTransportsNone",
    "1656": "RTCIceServerURL",
    "1657": "RTCIceServerURLs",
    "1658": "OffscreenCanvasTransferToImageBitmap2D",
    "1659": "OffscreenCanvasTransferToImageBitmapWebGL",
    "1660": "OffscreenCanvasCommit2D",
    "1661": "OffscreenCanvasCommitWebGL",
    "1662": "RTCConfigurationIceTransportPolicy",
    "1663": "RTCConfigurationIceTransportPolicyNone",
    "1664": "RTCConfigurationIceTransports",
    "1665": "DocumentFullscreenElementInV0Shadow",
    "1666": "ScriptWithCSPBypassingSchemeParserInserted",
    "1667": "ScriptWithCSPBypassingSchemeNotParserInserted",
    "1668": "DocumentCreateElement2ndArgStringHandling",
    "1669": "V8MediaRecorder_Start_Method",
    "1670": "WebBluetoothRequestDevice",
    "1671": "UnitlessPerspectiveInPerspectiveProperty",
    "1672": "UnitlessPerspectiveInTransformProperty",
    "1673": "V8RTCSessionDescription_Type_AttributeGetter",
    "1674": "V8RTCSessionDescription_Type_AttributeSetter",
    "1675": "V8RTCSessionDescription_Sdp_AttributeGetter",
    "1676": "V8RTCSessionDescription_Sdp_AttributeSetter",
    "1677": "RTCSessionDescriptionInitNoType",
    "1678": "RTCSessionDescriptionInitNoSdp",
    "1679": "HTMLMediaElementPreloadForcedMetadata",
    "1680": "GenericSensorStart",
    "1681": "GenericSensorStop",
    "1682": "TouchEventPreventedNoTouchAction",
    "1683": "TouchEventPreventedForcedDocumentPassiveNoTouchAction",
    "1684": "V8Event_StopPropagation_Method",
    "1685": "V8Event_StopImmediatePropagation_Method",
    "1686": "ImageCaptureConstructor",
    "1687": "V8Document_RootScroller_AttributeGetter",
    "1688": "V8Document_RootScroller_AttributeSetter",
    "1689": "CustomElementRegistryDefine",
    "1690": "LinkHeaderServiceWorker",
    "1691": "CSSShadowPiercingDescendantCombinator",
    "1692": "CSSFlexibleBox",
    "1693": "CSSGridLayout",
    "1694": "V8BarcodeDetector_Detect_Method",
    "1695": "V8FaceDetector_Detect_Method",
    "1696": "FullscreenAllowedByOrientationChange",
    "1697": "ServiceWorkerRespondToNavigationRequestWithRedirectedResponse",
    "1698": "V8AudioContext_Constructor",
    "1699": "V8OfflineAudioContext_Constructor",
    "1700": "AppInstalledEventAddListener",
    "1701": "AudioContextGetOutputTimestamp",
    "1702": "V8MediaStreamAudioDestinationNode_Constructor",
    "1703": "V8AnalyserNode_Constructor",
    "1704": "V8AudioBuffer_Constructor",
    "1705": "V8AudioBufferSourceNode_Constructor",
    "1706": "V8AudioProcessingEvent_Constructor",
    "1707": "V8BiquadFilterNode_Constructor",
    "1708": "V8ChannelMergerNode_Constructor",
    "1709": "V8ChannelSplitterNode_Constructor",
    "1710": "V8ConstantSourceNode_Constructor",
    "1711": "V8ConvolverNode_Constructor",
    "1712": "V8DelayNode_Constructor",
    "1713": "V8DynamicsCompressorNode_Constructor",
    "1714": "V8GainNode_Constructor",
    "1715": "V8IIRFilterNode_Constructor",
    "1716": "V8MediaElementAudioSourceNode_Constructor",
    "1717": "V8MediaStreamAudioSourceNode_Constructor",
    "1718": "V8OfflineAudioCompletionEvent_Constructor",
    "1719": "V8OscillatorNode_Constructor",
    "1720": "V8PannerNode_Constructor",
    "1721": "V8PeriodicWave_Constructor",
    "1722": "V8StereoPannerNode_Constructor",
    "1723": "V8WaveShaperNode_Constructor",
    "1724": "V8Headers_GetAll_Method",
    "1725": "NavigatorVibrateEngagementNone",
    "1726": "NavigatorVibrateEngagementMinimal",
    "1727": "NavigatorVibrateEngagementLow",
    "1728": "NavigatorVibrateEngagementMedium",
    "1729": "NavigatorVibrateEngagementHigh",
    "1730": "NavigatorVibrateEngagementMax",
    "1731": "AlertEngagementNone",
    "1732": "AlertEngagementMinimal",
    "1733": "AlertEngagementLow",
    "1734": "AlertEngagementMedium",
    "1735": "AlertEngagementHigh",
    "1736": "AlertEngagementMax",
    "1737": "ConfirmEngagementNone",
    "1738": "ConfirmEngagementMinimal",
    "1739": "ConfirmEngagementLow",
    "1740": "ConfirmEngagementMedium",
    "1741": "ConfirmEngagementHigh",
    "1742": "ConfirmEngagementMax",
    "1743": "PromptEngagementNone",
    "1744": "PromptEngagementMinimal",
    "1745": "PromptEngagementLow",
    "1746": "PromptEngagementMedium",
    "1747": "PromptEngagementHigh",
    "1748": "PromptEngagementMax",
    "1749": "TopNavInSandbox",
    "1750": "TopNavInSandboxWithoutGesture",
    "1751": "TopNavInSandboxWithPerm",
    "1752": "TopNavInSandboxWithPermButNoGesture",
    "1753": "ReferrerPolicyHeader",
    "1754": "HTMLAnchorElementReferrerPolicyAttribute",
    "1755": "HTMLIFrameElementReferrerPolicyAttribute",
    "1756": "HTMLImageElementReferrerPolicyAttribute",
    "1757": "HTMLLinkElementReferrerPolicyAttribute",
    "1758": "BaseElement",
    "1759": "BaseWithCrossOriginHref",
    "1760": "BaseWithDataHref",
    "1761": "BaseWithNewlinesInTarget",
    "1762": "BaseWithOpenBracketInTarget",
    "1763": "BaseWouldBeBlockedByDefaultSrc",
    "1764": "V8AssigmentExpressionLHSIsCallInSloppy",
    "1765": "V8AssigmentExpressionLHSIsCallInStrict",
    "1766": "V8PromiseConstructorReturnedUndefined",
    "1767": "FormSubmittedWithUnclosedFormControl",
    "1768": "DocumentCompleteURLHTTPContainingNewline",
    "1770": "DocumentCompleteURLHTTPContainingNewlineAndLessThan",
    "1771": "DocumentCompleteURLNonHTTPContainingNewline",
    "1772": "CSSSelectorInternalMediaControlsTextTrackList",
    "1773": "CSSSelectorInternalMediaControlsTextTrackListItem",
    "1774": "CSSSelectorInternalMediaControlsTextTrackListItemInput",
    "1775": "CSSSelectorInternalMediaControlsTextTrackListKindCaptions",
    "1776": "CSSSelectorInternalMediaControlsTextTrackListKindSubtitles",
    "1777": "ScrollbarUseVerticalScrollbarButton",
    "1778": "ScrollbarUseVerticalScrollbarThumb",
    "1779": "ScrollbarUseVerticalScrollbarTrack",
    "1780": "ScrollbarUseHorizontalScrollbarButton",
    "1781": "ScrollbarUseHorizontalScrollbarThumb",
    "1782": "ScrollbarUseHorizontalScrollbarTrack",
    "1783": "HTMLTableCellElementColspan",
    "1784": "HTMLTableCellElementColspanGreaterThan1000",
    "1785": "HTMLTableCellElementColspanGreaterThan8190",
    "1786": "SelectionAddRangeIntersect",
    "1787": "PostMessageFromInsecureToSecureToplevel",
    "1788": "V8MediaSession_Metadata_AttributeGetter",
    "1789": "V8MediaSession_Metadata_AttributeSetter",
    "1790": "V8MediaSession_PlaybackState_AttributeGetter",
    "1791": "V8MediaSession_PlaybackState_AttributeSetter",
    "1792": "V8MediaSession_SetActionHandler_Method",
    "1793": "WebNFCPush",
    "1794": "WebNFCCancelPush",
    "1795": "WebNFCWatch",
    "1796": "WebNFCCancelWatch",
    "1797": "AudioParamCancelAndHoldAtTime",
    "1798": "CSSValueUserModifyReadOnly",
    "1799": "CSSValueUserModifyReadWrite",
    "1800": "CSSValueUserModifyReadWritePlaintextOnly",
    "1801": "V8TextDetector_Detect_Method",
    "1802": "CSSValueOnDemand",
    "1803": "ServiceWorkerNavigationPreload",
    "1804": "FullscreenRequestWithPendingElement",
    "1805": "HTMLIFrameElementAllowfullscreenAttributeSetAfterContentLoad",
    "1806": "PointerEventSetCaptureOutsideDispatch",
    "1807": "NotificationPermissionRequestedInsecureOrigin",
    "1808": "V8DeprecatedStorageInfo_QueryUsageAndQuota_Method",
    "1809": "V8DeprecatedStorageInfo_RequestQuota_Method",
    "1810": "V8DeprecatedStorageQuota_QueryUsageAndQuota_Method",
    "1811": "V8DeprecatedStorageQuota_RequestQuota_Method",
    "1812": "V8FileReaderSync_Constructor",
    "1813": "UncancellableTouchEventPreventDefaulted",
    "1814": "UncancellableTouchEventDueToMainThreadResponsivenessPreventDefaulted",
    "1815": "V8HTMLVideoElement_Poster_AttributeGetter",
    "1816": "V8HTMLVideoElement_Poster_AttributeSetter",
    "1817": "NotificationPermissionRequestedIframe",
    "1818": "FileReaderSyncInServiceWorker",
    "1819": "PresentationReceiverInsecureOrigin",
    "1820": "PresentationReceiverSecureOrigin",
    "1821": "PresentationRequestInsecureOrigin",
    "1822": "PresentationRequestSecureOrigin",
    "1823": "RtcpMuxPolicyNegotiate",
    "1824": "DOMClobberedVariableAccessed",
    "1825": "HTMLDocumentCreateProcessingInstruction",
    "1826": "FetchResponseConstructionWithStream",
    "1827": "LocationOrigin",
    "1828": "DocumentOrigin",
    "1829": "SubtleCryptoOnlyStrictSecureContextCheckFailed",
    "1830": "Canvas2DFilter",
    "1831": "Canvas2DImageSmoothingQuality",
    "1832": "CanvasToBlob",
    "1833": "CanvasToDataURL",
    "1834": "OffscreenCanvasConvertToBlob",
    "1835": "SVGInCanvas2D",
    "1836": "SVGInWebGL",
    "1837": "SelectionFuncionsChangeFocus",
    "1838": "HTMLObjectElementGetter",
    "1839": "HTMLObjectElementSetter",
    "1840": "HTMLEmbedElementGetter",
    "1841": "HTMLEmbedElementSetter",
    "1842": "TransformUsesBoxSizeOnSVG",
    "1843": "ScrollByKeyboardArrowKeys",
    "1844": "ScrollByKeyboardPageUpDownKeys",
    "1845": "ScrollByKeyboardHomeEndKeys",
    "1846": "ScrollByKeyboardSpacebarKey",
    "1847": "ScrollByTouch",
    "1848": "ScrollByWheel",
    "1849": "ScheduledActionIgnored",
    "1850": "GetCanvas2DContextAttributes",
    "1851": "V8HTMLInputElement_Capture_AttributeGetter",
    "1852": "V8HTMLInputElement_Capture_AttributeSetter",
    "1853": "HTMLMediaElementControlsListAttribute",
    "1854": "HTMLMediaElementControlsListNoDownload",
    "1855": "HTMLMediaElementControlsListNoFullscreen",
    "1856": "HTMLMediaElementControlsListNoRemotePlayback",
    "1857": "PointerEventClickRetargetCausedByCapture",
    "1861": "VRDisplayDisplayName",
    "1862": "VREyeParametersOffset",
    "1863": "VRPoseLinearVelocity",
    "1864": "VRPoseLinearAcceleration",
    "1865": "VRPoseAngularVelocity",
    "1866": "VRPoseAngularAcceleration",
    "1867": "CSSOverflowPaged",
    "1868": "ChildSrcAllowedWorkerThatScriptSrcBlocked",
    "1869": "HTMLTableElementPresentationAttributeBackground",
    "1870": "V8Navigator_GetInstalledRelatedApps_Method",
    "1871": "NamedAccessOnWindow_ChildBrowsingContext",
    "1872": "NamedAccessOnWindow_ChildBrowsingContext_CrossOriginNameMismatch",
    "1873": "V0CustomElementsRegisterHTMLCustomTag",
    "1874": "V0CustomElementsRegisterHTMLTypeExtension",
    "1875": "V0CustomElementsRegisterSVGElement",
    "1876": "V0CustomElementsRegisterEmbedderElement",
    "1877": "V0CustomElementsCreateCustomTagElement",
    "1878": "V0CustomElementsCreateTypeExtensionElement",
    "1879": "V0CustomElementsConstruct",
    "1880": "V8IDBObserver_Observe_Method",
    "1881": "V8IDBObserver_Unobserve_Method",
    "1882": "WebBluetoothRemoteCharacteristicGetDescriptor",
    "1883": "WebBluetoothRemoteCharacteristicGetDescriptors",
    "1884": "WebBluetoothRemoteCharacteristicReadValue",
    "1885": "WebBluetoothRemoteCharacteristicWriteValue",
    "1886": "WebBluetoothRemoteCharacteristicStartNotifications",
    "1887": "WebBluetoothRemoteCharacteristicStopNotifications",
    "1888": "WebBluetoothRemoteDescriptorReadValue",
    "1889": "WebBluetoothRemoteDescriptorWriteValue",
    "1890": "WebBluetoothRemoteServerConnect",
    "1891": "WebBluetoothRemoteServerDisconnect",
    "1892": "WebBluetoothRemoteServerGetPrimaryService",
    "1893": "WebBluetoothRemoteServerGetPrimaryServices",
    "1894": "WebBluetoothRemoteServiceGetCharacteristic",
    "1895": "WebBluetoothRemoteServiceGetCharacteristics",
    "1896": "HTMLContentElement",
    "1897": "HTMLShadowElement",
    "1898": "HTMLSlotElement",
    "1899": "AccelerometerConstructor",
    "1900": "AbsoluteOrientationSensorConstructor",
    "1901": "AmbientLightSensorConstructor",
    "1902": "GenericSensorOnActivate",
    "1903": "GenericSensorOnChange",
    "1904": "GenericSensorOnError",
    "1905": "GenericSensorActivated",
    "1906": "GyroscopeConstructor",
    "1907": "MagnetometerConstructor",
    "1908": "OrientationSensorPopulateMatrix",
    "1909": "WindowOpenWithInvalidURL",
    "1910": "CrossOriginMainFrameNulledNameAccessed",
    "1911": "MenuItemElementIconAttribute",
    "1912": "WebkitCSSMatrixSetMatrixValue",
    "1913": "WebkitCSSMatrixConstructFromString",
    "1914": "CanRequestURLHTTPContainingNewline",
    "1915": "CanRequestURLNonHTTPContainingNewline",
    "1916": "GetGamepads",
    "1917": "V8SVGPathElement_GetPathSegAtLength_Method",
    "1918": "MediaStreamConstraintsAudio",
    "1919": "MediaStreamConstraintsAudioUnconstrained",
    "1920": "MediaStreamConstraintsVideo",
    "1921": "MediaStreamConstraintsVideoUnconstrained",
    "1922": "MediaStreamConstraintsWidth",
    "1923": "MediaStreamConstraintsHeight",
    "1924": "MediaStreamConstraintsAspectRatio",
    "1925": "MediaStreamConstraintsFrameRate",
    "1926": "MediaStreamConstraintsFacingMode",
    "1927": "MediaStreamConstraintsVolume",
    "1928": "MediaStreamConstraintsSampleRate",
    "1929": "MediaStreamConstraintsSampleSize",
    "1930": "MediaStreamConstraintsEchoCancellation",
    "1931": "MediaStreamConstraintsLatency",
    "1932": "MediaStreamConstraintsChannelCount",
    "1933": "MediaStreamConstraintsDeviceIdAudio",
    "1934": "MediaStreamConstraintsDeviceIdVideo",
    "1935": "MediaStreamConstraintsDisableLocalEcho",
    "1936": "MediaStreamConstraintsGroupIdAudio",
    "1937": "MediaStreamConstraintsGroupIdVideo",
    "1938": "MediaStreamConstraintsVideoKind",
    "1939": "MediaStreamConstraintsDepthNear",
    "1940": "MediaStreamConstraintsDepthFar",
    "1941": "MediaStreamConstraintsFocalLengthX",
    "1942": "MediaStreamConstraintsFocalLengthY",
    "1943": "MediaStreamConstraintsMediaStreamSourceAudio",
    "1944": "MediaStreamConstraintsMediaStreamSourceVideo",
    "1945": "MediaStreamConstraintsRenderToAssociatedSink",
    "1946": "MediaStreamConstraintsHotwordEnabled",
    "1947": "MediaStreamConstraintsGoogEchoCancellation",
    "1948": "MediaStreamConstraintsGoogExperimentalEchoCancellation",
    "1949": "MediaStreamConstraintsGoogAutoGainControl",
    "1950": "MediaStreamConstraintsGoogExperimentalAutoGainControl",
    "1951": "MediaStreamConstraintsGoogNoiseSuppression",
    "1952": "MediaStreamConstraintsGoogHighpassFilter",
    "1953": "MediaStreamConstraintsGoogTypingNoiseDetection",
    "1954": "MediaStreamConstraintsGoogExperimentalNoiseSuppression",
    "1955": "MediaStreamConstraintsGoogBeamforming",
    "1956": "MediaStreamConstraintsGoogArrayGeometry",
    "1957": "MediaStreamConstraintsGoogAudioMirroring",
    "1958": "MediaStreamConstraintsGoogDAEchoCancellation",
    "1959": "MediaStreamConstraintsGoogNoiseReduction",
    "1960": "MediaStreamConstraintsGoogPowerLineFrequency",
    "1961": "ViewportFixedPositionUnderFilter",
    "1962": "RequestMIDIAccessWithSysExOption",
    "1963": "RequestMIDIAccessIframeWithSysExOption",
    "1964": "GamepadAxes",
    "1965": "GamepadButtons",
    "1966": "VibrateWithoutUserGesture",
    "1967": "DispatchMouseEventOnDisabledFormControl",
    "1968": "ElementNameDOMInvalidHTMLParserValid",
    "1969": "ElementNameDOMValidHTMLParserInvalid",
    "1970": "GATTServerDisconnectedEvent",
    "1971": "AnchorClickDispatchForNonConnectedNode",
    "1972": "HTMLParseErrorNestedForm",
    "1973": "FontShapingNotDefGlyphObserved",
    "1974": "PostMessageOutgoingWouldBeBlockedByConnectSrc",
    "1975": "PostMessageIncomingWouldBeBlockedByConnectSrc",
    "1976": "PaymentRequestNetworkNameInSupportedMethods",
    "1977": "CrossOriginPropertyAccess",
    "1978": "CrossOriginPropertyAccessFromOpener",
    "1979": "CredentialManagerCreate",
    "1980": "WebDatabaseCreateDropFTS3Table",
    "1981": "FieldEditInSecureContext",
    "1982": "FieldEditInNonSecureContext",
    "1983": "CredentialManagerCredentialRequestOptionsUnmediated",
    "1984": "CredentialManagerGetMediationRequired",
    "1985": "CredentialManagerIdName",
    "1986": "CredentialManagerPasswordName",
    "1987": "CredentialManagerAdditionalData",
    "1988": "CredentialManagerCustomFetch",
    "1989": "NetInfoRtt",
    "1990": "NetInfoDownlink",
    "1991": "ShapeDetection_BarcodeDetectorConstructor",
    "1992": "ShapeDetection_FaceDetectorConstructor",
    "1993": "ShapeDetection_TextDetectorConstructor",
    "1994": "CredentialManagerCredentialRequestOptionsOnlyUnmediated",
    "1995": "InertAttribute",
    "1996": "PluginInstanceAccessFromIsolatedWorld",
    "1997": "PluginInstanceAccessFromMainWorld",
    "1998": "RequestFullscreenForDialogElement",
    "1999": "RequestFullscreenForDialogElementInTopLayer",
    "2000": "ShowModalForElementInFullscreenStack",
    "2001": "ThreeValuedPositionBackground",
    "2002": "ThreeValuedPositionBasicShape",
    "2003": "ThreeValuedPositionGradient",
    "2004": "ThreeValuedPositionObjectPosition",
    "2005": "ThreeValuedPositionPerspectiveOrigin",
    "2007": "UnitlessZeroAngleFilter",
    "2008": "UnitlessZeroAngleGradient",
    "2010": "UnitlessZeroAngleTransform",
    "2011": "HTMLOListElementStartGetterReversedWithoutStartAttribute",
    "2012": "CredentialManagerPreventSilentAccess",
    "2013": "NetInfoEffectiveType",
    "2014": "V8SpeechRecognition_Start_Method",
    "2015": "TableRowDirectionDifferentFromTable",
    "2016": "TableSectionDirectionDifferentFromTable",
    "2017": "ClientHintsDeviceRAM",
    "2018": "CSSRegisterProperty",
    "2019": "RelativeOrientationSensorConstructor",
    "2020": "SmoothScrollJSInterventionActivated",
    "2021": "BudgetAPIGetCost",
    "2022": "BudgetAPIGetBudget",
    "2023": "CrossOriginMainFrameNulledNonEmptyNameAccessed",
    "2024": "DeprecatedTimingFunctionStepMiddle",
    "2025": "DocumentDomainSetWithNonDefaultPort",
    "2026": "DocumentDomainSetWithDefaultPort",
    "2027": "FeaturePolicyHeader",
    "2028": "FeaturePolicyAllowAttribute",
    "2029": "MIDIPortOpen",
    "2030": "MIDIOutputSend",
    "2031": "MIDIMessageEvent",
    "2032": "FetchEventIsReload",
    "2033": "ServiceWorkerClientFrameType",
    "2034": "QuirksModeDocument",
    "2035": "LimitedQuirksModeDocument",
    "2036": "EncryptedMediaCrossOriginIframe",
    "2037": "CSSSelectorWebkitMediaControls",
    "2038": "CSSSelectorWebkitMediaControlsOverlayEnclosure",
    "2039": "CSSSelectorWebkitMediaControlsOverlayPlayButton",
    "2040": "CSSSelectorWebkitMediaControlsEnclosure",
    "2041": "CSSSelectorWebkitMediaControlsPanel",
    "2042": "CSSSelectorWebkitMediaControlsPlayButton",
    "2043": "CSSSelectorWebkitMediaControlsCurrentTimeDisplay",
    "2044": "CSSSelectorWebkitMediaControlsTimeRemainingDisplay",
    "2045": "CSSSelectorWebkitMediaControlsTimeline",
    "2046": "CSSSelectorWebkitMediaControlsTimelineContainer",
    "2047": "CSSSelectorWebkitMediaControlsMuteButton",
    "2048": "CSSSelectorWebkitMediaControlsVolumeSlider",
    "2049": "CSSSelectorWebkitMediaControlsFullscreenButton",
    "2050": "CSSSelectorWebkitMediaControlsToggleClosedCaptionsButton",
    "2051": "LinearAccelerationSensorConstructor",
    "2052": "ReportUriMultipleEndpoints",
    "2053": "ReportUriSingleEndpoint",
    "2054": "V8ConstructorNonUndefinedPrimitiveReturn",
    "2055": "EncryptedMediaDisallowedByFeaturePolicyInCrossOriginIframe",
    "2056": "GeolocationDisallowedByFeaturePolicyInCrossOriginIframe",
    "2057": "GetUserMediaMicDisallowedByFeaturePolicyInCrossOriginIframe",
    "2058": "GetUserMediaCameraDisallowedByFeaturePolicyInCrossOriginIframe",
    "2059": "RequestMIDIAccessDisallowedByFeaturePolicyInCrossOriginIframe",
    "2060": "MediaSourceKeyframeTimeGreaterThanDependant",
    "2061": "MediaSourceMuxedSequenceMode",
    "2062": "PrepareModuleScript",
    "2063": "PresentationRequestStartSecureOrigin",
    "2064": "PresentationRequestStartInsecureOrigin",
    "2065": "PersistentClientHintHeader",
    "2066": "StyleSheetListNonNullAnonymousNamedGetter",
    "2067": "OffMainThreadFetch",
    "2069": "ARIAActiveDescendantAttribute",
    "2070": "ARIAAtomicAttribute",
    "2071": "ARIAAutocompleteAttribute",
    "2072": "ARIABusyAttribute",
    "2073": "ARIACheckedAttribute",
    "2074": "ARIAColCountAttribute",
    "2075": "ARIAColIndexAttribute",
    "2076": "ARIAColSpanAttribute",
    "2077": "ARIAControlsAttribute",
    "2078": "ARIACurrentAttribute",
    "2079": "ARIADescribedByAttribute",
    "2080": "ARIADetailsAttribute",
    "2081": "ARIADisabledAttribute",
    "2082": "ARIADropEffectAttribute",
    "2083": "ARIAErrorMessageAttribute",
    "2084": "ARIAExpandedAttribute",
    "2085": "ARIAFlowToAttribute",
    "2086": "ARIAGrabbedAttribute",
    "2087": "ARIAHasPopupAttribute",
    "2088": "ARIAHelpAttribute",
    "2089": "ARIAHiddenAttribute",
    "2090": "ARIAInvalidAttribute",
    "2091": "ARIAKeyShortcutsAttribute",
    "2092": "ARIALabelAttribute",
    "2093": "ARIALabeledByAttribute",
    "2094": "ARIALabelledByAttribute",
    "2095": "ARIALevelAttribute",
    "2096": "ARIALiveAttribute",
    "2097": "ARIAModalAttribute",
    "2098": "ARIAMultilineAttribute",
    "2099": "ARIAMultiselectableAttribute",
    "2100": "ARIAOrientationAttribute",
    "2101": "ARIAOwnsAttribute",
    "2102": "ARIAPlaceholderAttribute",
    "2103": "ARIAPosInSetAttribute",
    "2104": "ARIAPressedAttribute",
    "2105": "ARIAReadOnlyAttribute",
    "2106": "ARIARelevantAttribute",
    "2107": "ARIARequiredAttribute",
    "2108": "ARIARoleDescriptionAttribute",
    "2109": "ARIARowCountAttribute",
    "2110": "ARIARowIndexAttribute",
    "2111": "ARIARowSpanAttribute",
    "2112": "ARIASelectedAttribute",
    "2113": "ARIASetSizeAttribute",
    "2114": "ARIASortAttribute",
    "2115": "ARIAValueMaxAttribute",
    "2116": "ARIAValueMinAttribute",
    "2117": "ARIAValueNowAttribute",
    "2118": "ARIAValueTextAttribute",
    "2119": "V8LabeledExpressionStatement",
    "2120": "PaymentRequestSupportedMethodsArray",
    "2121": "NavigatorDeviceMemory",
    "2122": "FixedWidthTableDistributionChanged",
    "2123": "WebkitBoxLayout",
    "2124": "WebkitBoxLayoutHorizontal",
    "2125": "WebkitBoxLayoutVertical",
    "2126": "WebkitBoxAlignNotInitial",
    "2127": "WebkitBoxDirectionNotInitial",
    "2128": "WebkitBoxLinesNotInitial",
    "2129": "WebkitBoxPackNotInitial",
    "2130": "WebkitBoxChildFlexNotInitial",
    "2131": "WebkitBoxChildFlexGroupNotInitial",
    "2132": "WebkitBoxChildOrdinalGroupNotInitial",
    "2133": "WebkitBoxNotDefaultOrder",
    "2134": "WebkitBoxNoChildren",
    "2135": "WebkitBoxOneChild",
    "2136": "WebkitBoxOneChildIsLayoutBlockFlowInline",
    "2137": "WebkitBoxManyChildren",
    "2138": "WebkitBoxLineClamp",
    "2139": "WebkitBoxLineClampPercentage",
    "2140": "WebkitBoxLineClampNoChildren",
    "2141": "WebkitBoxLineClampOneChild",
    "2142": "WebkitBoxLineClampOneChildIsLayoutBlockFlowInline",
    "2143": "WebkitBoxLineClampManyChildren",
    "2144": "WebkitBoxLineClampDoesSomething",
    "2145": "FeaturePolicyAllowAttributeDeprecatedSyntax",
    "2146": "SuppressHistoryEntryWithoutUserGesture",
    "2147": "ImageInputTypeFormDataWithNonEmptyValue",
    "2157": "PerformanceServerTiming",
    "2158": "FileReaderResultBeforeCompletion",
    "2159": "SyncXhrInPageDismissal",
    "2160": "AsyncXhrInPageDismissal",
    "2162": "AnimationSetPlaybackRateCompensatorySeek",
    "2163": "DeepCombinatorInStaticProfile",
    "2164": "PseudoShadowInStaticProfile",
    "2165": "SchemeBypassesCSP",
    "2166": "InnerSchemeBypassesCSP",
    "2167": "SameOriginApplicationOctetStream",
    "2168": "SameOriginApplicationXml",
    "2169": "SameOriginTextHtml",
    "2170": "SameOriginTextPlain",
    "2171": "SameOriginTextXml",
    "2172": "CrossOriginApplicationOctetStream",
    "2173": "CrossOriginApplicationXml",
    "2174": "CrossOriginTextHtml",
    "2175": "CrossOriginTextPlain",
    "2176": "CrossOriginTextXml",
    "2177": "SameOriginWorkerApplicationOctetStream",
    "2178": "SameOriginWorkerApplicationXml",
    "2179": "SameOriginWorkerTextHtml",
    "2180": "SameOriginWorkerTextPlain",
    "2181": "SameOriginWorkerTextXml",
    "2182": "CrossOriginWorkerApplicationOctetStream",
    "2183": "CrossOriginWorkerApplicationXml",
    "2184": "CrossOriginWorkerTextHtml",
    "2185": "CrossOriginWorkerTextPlain",
    "2186": "CrossOriginWorkerTextXml",
    "2188": "PerformanceObserverForWindow",
    "2189": "PerformanceObserverForWorker",
    "2190": "PaintTimingObserved",
    "2191": "PaintTimingRequested",
    "2192": "HTMLMediaElementMediaPlaybackRateOutOfRange",
    "2193": "CSSFilterFunctionNegativeBrightness",
    "2194": "CookieSet",
    "2195": "CookieGet",
    "2196": "GeolocationDisabledByFeaturePolicy",
    "2197": "EncryptedMediaDisabledByFeaturePolicy",
    "2198": "BatteryStatusGetBattery",
    "2199": "BatteryStatusInsecureOrigin",
    "2200": "BatteryStatusCrossOrigin",
    "2201": "BatteryStatusSameOriginABA",
    "2203": "HasIDClassTagAttribute",
    "2204": "HasBeforeOrAfterPseudoElement",
    "2205": "ShapeOutsideMaybeAffectedInlineSize",
    "2206": "ShapeOutsideMaybeAffectedInlinePosition",
    "2207": "GamepadVibrationActuator",
    "2208": "MicrophoneDisabledByFeaturePolicyEstimate",
    "2209": "CameraDisabledByFeaturePolicyEstimate",
    "2210": "MidiDisabledByFeaturePolicy",
    "2211": "DocumentGetPreferredStylesheetSet",
    "2212": "DocumentGetSelectedStylesheetSet",
    "2213": "DocumentSetSelectedStylesheetSet",
    "2214": "GeolocationGetCurrentPosition",
    "2215": "GeolocationWatchPosition",
    "2216": "DataUriHasOctothorpe",
    "2217": "NetInfoSaveData",
    "2218": "V8Element_GetClientRects_Method",
    "2219": "V8Element_GetBoundingClientRect_Method",
    "2220": "V8Range_GetClientRects_Method",
    "2221": "V8Range_GetBoundingClientRect_Method",
    "2222": "V8ErrorCaptureStackTrace",
    "2223": "V8ErrorPrepareStackTrace",
    "2224": "V8ErrorStackTraceLimit",
    "2225": "PaintWorklet",
    "2226": "DocumentPageHideRegistered",
    "2227": "DocumentPageHideFired",
    "2228": "DocumentPageShowRegistered",
    "2229": "DocumentPageShowFired",
    "2230": "ReplaceCharsetInXHR",
    "2231": "RespondToSameOriginRequestWithCrossOriginResponse",
    "2232": "LinkRelModulePreload",
    "2233": "PerformanceMeasurePassedInObject",
    "2234": "PerformanceMeasurePassedInNavigationTiming",
    "2235": "HTMLFrameSetElementNonNullAnonymousNamedGetter",
    "2236": "CSPWithUnsafeEval",
    "2237": "WebAssemblyInstantiation",
    "2238": "V8IndexAccessor",
    "2239": "V8MediaCapabilities_DecodingInfo_Method",
    "2240": "V8MediaCapabilities_EncodingInfo_Method",
    "2241": "V8MediaCapabilitiesInfo_Supported_AttributeGetter",
    "2242": "V8MediaCapabilitiesInfo_Smooth_AttributeGetter",
    "2243": "V8MediaCapabilitiesInfo_PowerEfficient_AttributeGetter",
    "2244": "WindowEventInV0ShadowTree",
    "2245": "HTMLAnchorElementDownloadInSandboxWithUserGesture",
    "2246": "HTMLAnchorElementDownloadInSandboxWithoutUserGesture",
    "2247": "WindowOpenRealmMismatch",
    "2248": "GridRowTrackPercentIndefiniteHeight",
    "2249": "VRGetDisplaysSupportsPresent",
    "2250": "DuplicatedAttribute",
    "2251": "DuplicatedAttributeForExecutedScript",
    "2252": "V8RTCPeerConnection_GetSenders_Method",
    "2253": "V8RTCPeerConnection_GetReceivers_Method",
    "2254": "V8RTCPeerConnection_AddTrack_Method",
    "2255": "V8RTCPeerConnection_RemoveTrack_Method",
    "2256": "LocalCSSFile",
    "2257": "LocalCSSFileExtensionRejected",
    "2258": "UserMediaDisableHardwareNoiseSuppression",
    "2259": "CertificateTransparencyRequiredErrorOnResourceLoad",
    "2260": "CSSSelectorPseudoWebkitAnyLink",
    "2261": "AudioWorkletAddModule",
    "2262": "AudioWorkletGlobalScopeRegisterProcessor",
    "2263": "AudioWorkletNodeConstructor",
    "2264": "HTMLMediaElementEmptyLoadWithFutureData",
    "2265": "CSSValueDisplayContents",
    "2266": "CSSSelectorPseudoAnyLink",
    "2267": "FileAccessedCache",
    "2268": "FileAccessedCookies",
    "2269": "FileAccessedDatabase",
    "2270": "FileAccessedFileSystem",
    "2271": "FileAccessedLocalStorage",
    "2272": "FileAccessedLocks",
    "2273": "FileAccessedServiceWorker",
    "2274": "FileAccessedSessionStorage",
    "2275": "FileAccessedSharedWorker",
    "2276": "V8MediaKeys_GetStatusForPolicy_Method",
    "2277": "V8DeoptimizerDisableSpeculation",
    "2278": "CSSSelectorCue",
    "2279": "CSSSelectorWebkitCalendarPickerIndicator",
    "2280": "CSSSelectorWebkitClearButton",
    "2281": "CSSSelectorWebkitColorSwatch",
    "2282": "CSSSelectorWebkitColorSwatchWrapper",
    "2283": "CSSSelectorWebkitDateAndTimeValue",
    "2284": "CSSSelectorWebkitDatetimeEdit",
    "2285": "CSSSelectorWebkitDatetimeEditAmpmField",
    "2286": "CSSSelectorWebkitDatetimeEditDayField",
    "2287": "CSSSelectorWebkitDatetimeEditFieldsWrapper",
    "2288": "CSSSelectorWebkitDatetimeEditHourField",
    "2289": "CSSSelectorWebkitDatetimeEditMillisecondField",
    "2290": "CSSSelectorWebkitDatetimeEditMinuteField",
    "2291": "CSSSelectorWebkitDatetimeEditMonthField",
    "2292": "CSSSelectorWebkitDatetimeEditSecondField",
    "2293": "CSSSelectorWebkitDatetimeEditText",
    "2294": "CSSSelectorWebkitDatetimeEditWeekField",
    "2295": "CSSSelectorWebkitDatetimeEditYearField",
    "2296": "CSSSelectorWebkitDetailsMarker",
    "2297": "CSSSelectorWebkitFileUploadButton",
    "2298": "CSSSelectorWebkitInnerSpinButton",
    "2299": "CSSSelectorWebkitInputPlaceholder",
    "2300": "CSSSelectorWebkitMediaSliderContainer",
    "2301": "CSSSelectorWebkitMediaSliderThumb",
    "2302": "CSSSelectorWebkitMediaTextTrackContainer",
    "2303": "CSSSelectorWebkitMediaTextTrackDisplay",
    "2304": "CSSSelectorWebkitMediaTextTrackRegion",
    "2305": "CSSSelectorWebkitMediaTextTrackRegionContainer",
    "2306": "CSSSelectorWebkitMeterBar",
    "2307": "CSSSelectorWebkitMeterEvenLessGoodValue",
    "2308": "CSSSelectorWebkitMeterInnerElement",
    "2309": "CSSSelectorWebkitMeterOptimumValue",
    "2310": "CSSSelectorWebkitMeterSuboptimumValue",
    "2311": "CSSSelectorWebkitProgressBar",
    "2312": "CSSSelectorWebkitProgressInnerElement",
    "2313": "CSSSelectorWebkitProgressValue",
    "2314": "CSSSelectorWebkitSearchCancelButton",
    "2315": "CSSSelectorWebkitSliderContainer",
    "2316": "CSSSelectorWebkitSliderRunnableTrack",
    "2317": "CSSSelectorWebkitSliderThumb",
    "2318": "CSSSelectorWebkitTextfieldDecorationContainer",
    "2319": "CSSSelectorWebkitUnknownPseudo",
    "2320": "FilterAsContainingBlockMayChangeOutput",
    "2321": "DispatchMouseUpDownEventOnDisabledFormControl",
    "2322": "CSSSelectorPseudoMatches",
    "2323": "V8RTCRtpSender_ReplaceTrack_Method",
    "2324": "InputTypeFileSecureOriginOpenChooser",
    "2325": "InputTypeFileInsecureOriginOpenChooser",
    "2326": "BasicShapeEllipseNoRadius",
    "2327": "BasicShapeEllipseOneRadius",
    "2328": "BasicShapeEllipseTwoRadius",
    "2329": "TemporalInputTypeChooserByTrustedClick",
    "2330": "TemporalInputTypeChooserByUntrustedClick",
    "2331": "TemporalInputTypeIgnoreUntrustedClick",
    "2332": "ColorInputTypeChooserByTrustedClick",
    "2333": "ColorInputTypeChooserByUntrustedClick",
    "2334": "CSSTypedOMStylePropertyMap",
    "2335": "ScrollToFragmentRequested",
    "2336": "ScrollToFragmentSucceedWithRaw",
    "2337": "ScrollToFragmentSucceedWithASCII",
    "2338": "ScrollToFragmentSucceedWithUTF8",
    "2339": "ScrollToFragmentSucceedWithIsomorphic",
    "2340": "ScrollToFragmentSucceedWithMixed",
    "2341": "ScrollToFragmentFailWithASCII",
    "2342": "ScrollToFragmentFailWithUTF8",
    "2343": "ScrollToFragmentFailWithIsomorphic",
    "2344": "ScrollToFragmentFailWithMixed",
    "2345": "ScrollToFragmentFailWithInvalidEncoding",
    "2346": "RTCPeerConnectionWithActiveCsp",
    "2347": "ImageDecodingAttribute",
    "2348": "ImageDecodeAPI",
    "2349": "V8HTMLElement_Autocapitalize_AttributeGetter",
    "2350": "V8HTMLElement_Autocapitalize_AttributeSetter",
    "2351": "CSSLegacyAlignment",
    "2352": "SRISignatureCheck",
    "2353": "SRISignatureSuccess",
    "2354": "CSSBasicShape",
    "2355": "CSSGradient",
    "2356": "CSSPaintFunction",
    "2357": "WebkitCrossFade",
    "2358": "DisablePictureInPictureAttribute",
    "2359": "CertificateTransparencyNonCompliantSubresourceInMainFrame",
    "2360": "CertificateTransparencyNonCompliantResourceInSubframe",
    "2361": "V8AbortController_Constructor",
    "2362": "ReplaceCharsetInXHRIgnoringCase",
    "2363": "HTMLIFrameElementGestureMedia",
    "2364": "WorkletAddModule",
    "2365": "AnimationWorkletRegisterAnimator",
    "2366": "WorkletAnimationConstructor",
    "2367": "ScrollTimelineConstructor",
    "2368": "V8Document_CreateTouchList_Method",
    "2369": "AsyncClipboardAPIRead",
    "2370": "AsyncClipboardAPIWrite",
    "2371": "AsyncClipboardAPIReadText",
    "2372": "AsyncClipboardAPIWriteText",
    "2373": "OpenerNavigationWithoutGesture",
    "2374": "GetComputedStyleWebkitAppearance",
    "2375": "V8LockManager_Request_Method",
    "2376": "V8LockManager_Query_Method",
    "2377": "UserMediaEnableExperimentalHardwareEchoCancellation",
    "2378": "V8RTCDTMFSender_Track_AttributeGetter",
    "2379": "V8RTCDTMFSender_Duration_AttributeGetter",
    "2380": "V8RTCDTMFSender_InterToneGap_AttributeGetter",
    "2381": "V8RTCRtpSender_Dtmf_AttributeGetter",
    "2382": "RTCConstraintEnableDtlsSrtpTrue",
    "2383": "RTCConstraintEnableDtlsSrtpFalse",
    "2384": "RtcPeerConnectionId",
    "2385": "V8PaintWorkletGlobalScope_RegisterPaint_Method",
    "2386": "V8PaintWorkletGlobalScope_DevicePixelRatio_AttributeGetter",
    "2387": "CSSSelectorPseudoFocus",
    "2388": "CSSSelectorPseudoFocusVisible",
    "2389": "DistrustedLegacySymantecSubresource",
    "2390": "VRDisplayGetFrameData",
    "2391": "XMLHttpRequestResponseXML",
    "2392": "MessagePortTransferClosedPort",
    "2393": "RTCLocalSdpModification",
    "2394": "KeyboardApiLock",
    "2395": "KeyboardApiUnlock",
    "2396": "PPAPIURLRequestStreamToFile",
    "2397": "PaymentHandler",
    "2398": "PaymentRequestShowWithoutGesture",
    "2399": "ReadableStreamConstructor",
    "2400": "WritableStreamConstructor",
    "2401": "TransformStreamConstructor",
    "2402": "NegativeBackgroundSize",
    "2403": "NegativeMaskSize",
    "2404": "ClientHintsRtt",
    "2405": "ClientHintsDownlink",
    "2406": "ClientHintsEct",
    "2407": "CrossOriginHTMLIFrameElementContentDocument",
    "2408": "CrossOriginHTMLIFrameElementGetSVGDocument",
    "2409": "CrossOriginHTMLEmbedElementGetSVGDocument",
    "2410": "CrossOriginHTMLFrameElementContentDocument",
    "2411": "CrossOriginHTMLObjectElementContentDocument",
    "2412": "CrossOriginHTMLObjectElementGetSVGDocument",
    "2413": "NavigatorXR",
    "2414": "XRRequestDevice",
    "2415": "XRRequestSession",
    "2416": "XRSupportsSession",
    "2417": "XRSessionGetInputSources",
    "2418": "CSSResizeAuto",
    "2419": "PrefixedCursorGrab",
    "2420": "PrefixedCursorGrabbing",
    "2421": "CredentialManagerCreatePublicKeyCredential",
    "2422": "CredentialManagerGetPublicKeyCredential",
    "2423": "CredentialManagerMakePublicKeyCredentialSuccess",
    "2424": "CredentialManagerGetPublicKeyCredentialSuccess",
    "2425": "ShapeOutsideContentBox",
    "2426": "ShapeOutsidePaddingBox",
    "2427": "ShapeOutsideBorderBox",
    "2428": "ShapeOutsideMarginBox",
    "2429": "PerformanceTimeline",
    "2430": "UserTiming",
    "2431": "CSSSelectorPseudoIS",
    "2432": "KeyboardApiGetLayoutMap",
    "2434": "PerformanceResourceTimingInitiatorType",
    "2436": "V8ArraySortNoElementsProtector",
    "2437": "V8ArrayPrototypeSortJSArrayModifiedPrototype",
    "2438": "V8Document_PictureInPictureEnabled_AttributeGetter",
    "2439": "V8Document_PictureInPictureElement_AttributeGetter",
    "2440": "V8Document_ExitPictureInPicture_Method",
    "2441": "V8ShadowRoot_PictureInPictureElement_AttributeGetter",
    "2442": "V8HTMLVideoElement_DisablePictureInPicture_AttributeGetter",
    "2443": "V8HTMLVideoElement_DisablePictureInPicture_AttributeSetter",
    "2444": "V8HTMLVideoElement_RequestPictureInPicture_Method",
    "2445": "EnterPictureInPictureEventListener",
    "2446": "LeavePictureInPictureEventListener",
    "2447": "V8PictureInPictureWindow_Height_AttributeGetter",
    "2448": "V8PictureInPictureWindow_Width_AttributeGetter",
    "2449": "PictureInPictureWindowResizeEventListener",
    "2450": "V8CookieStore_Delete_Method",
    "2451": "V8CookieStore_Get_Method",
    "2452": "V8CookieStore_GetAll_Method",
    "2453": "V8CookieStore_GetChangeSubscriptions_Method",
    "2454": "V8CookieStore_Has_Method",
    "2455": "V8CookieStore_Set_Method",
    "2456": "V8CookieStore_SubscribeToChanges_Method",
    "2457": "V8CookieChangeEvent_Changed_AttributeGetter",
    "2458": "V8CookieChangeEvent_Deleted_AttributeGetter",
    "2459": "V8ExtendableCookieChangeEvent_Changed_AttributeGetter",
    "2460": "V8ExtendableCookieChangeEvent_Deleted_AttributeGetter",
    "2461": "ShapeOutsideContentBoxDifferentFromMarginBox",
    "2462": "ShapeOutsidePaddingBoxDifferentFromMarginBox",
    "2463": "CSSContainLayoutPositionedDescendants",
    "2464": "HTMLFrameSetElementAnonymousNamedGetter",
    "2465": "CanvasConvertToBlob",
    "2466": "PolymerV1Detected",
    "2467": "PolymerV2Detected",
    "2468": "PerformanceEventTimingBuffer",
    "2469": "PerformanceEventTimingConstructor",
    "2470": "ReverseIterateDOMStorage",
    "2471": "TextToSpeech_Speak",
    "2472": "TextToSpeech_SpeakCrossOrigin",
    "2473": "TextToSpeech_SpeakDisallowedByAutoplay",
    "2474": "StaleWhileRevalidateEnabled",
    "2475": "MediaElementSourceOnOfflineContext",
    "2476": "MediaStreamDestinationOnOfflineContext",
    "2477": "MediaStreamSourceOnOfflineContext",
    "2478": "RTCDataChannelInitMaxRetransmitTime",
    "2479": "RTCPeerConnectionCreateDataChannelMaxPacketLifeTime",
    "2480": "V8SpeechGrammarList_AddFromUri_Method",
    "2481": "V8SpeechRecognitionEvent_Interpretation_AttributeGetter",
    "2482": "V8SpeechRecognitionEvent_Emma_AttributeGetter",
    "2483": "V8SpeechSynthesis_Speak_Method",
    "2484": "LegacySymantecCertMainFrameResource",
    "2485": "LegacySymantecCertInSubresource",
    "2486": "LegacySymantecCertInSubframeMainResource",
    "2487": "EventTimingExplicitlyRequested",
    "2488": "CSSEnvironmentVariable",
    "2489": "CSSEnvironmentVariable_SafeAreaInsetTop",
    "2490": "CSSEnvironmentVariable_SafeAreaInsetLeft",
    "2491": "CSSEnvironmentVariable_SafeAreaInsetBottom",
    "2492": "CSSEnvironmentVariable_SafeAreaInsetRight",
    "2493": "MediaControlsDisplayCutoutGesture",
    "2494": "DocumentOpenTwoArgs",
    "2495": "DocumentOpenTwoArgsWithReplace",
    "2496": "DocumentOpenThreeArgs",
    "2497": "V8FunctionTokenOffsetTooLongForToString",
    "2498": "ServiceWorkerImportScriptNotInstalled",
    "2499": "NestedDedicatedWorker",
    "2500": "ClientHintsMetaAcceptCHLifetime",
    "2501": "DOMNodeRemovedEventDelayed",
    "2502": "DOMNodeRemovedEventHandlerAccessDetachingNode",
    "2503": "DOMNodeRemovedEventListenedAtNonTarget",
    "2504": "DOMNodeRemovedFromDocumentEventDelayed",
    "2505": "DOMNodeRemovedFromDocumentEventHandlerAccessDetachingNode",
    "2506": "DOMNodeRemovedFromDocumentEventListenedAtNonTarget",
    "2507": "CSSFillAvailableLogicalWidth",
    "2508": "CSSFillAvailableLogicalHeight",
    "2509": "PopupOpenWhileFileChooserOpened",
    "2510": "CookieStoreAPI",
    "2511": "FeaturePolicyJSAPI",
    "2512": "V8RTCPeerConnection_GetTransceivers_Method",
    "2513": "V8RTCPeerConnection_AddTransceiver_Method",
    "2514": "V8RTCRtpTransceiver_Direction_AttributeGetter",
    "2515": "V8RTCRtpTransceiver_Direction_AttributeSetter",
    "2516": "HTMLLinkElementDisabledByParser",
    "2517": "RequestIsHistoryNavigation",
    "2518": "AddDocumentLevelPassiveTrueWheelEventListener",
    "2519": "AddDocumentLevelPassiveFalseWheelEventListener",
    "2520": "AddDocumentLevelPassiveDefaultWheelEventListener",
    "2521": "DocumentLevelPassiveDefaultEventListenerPreventedWheel",
    "2522": "ShapeDetectionAPI",
    "2523": "V8SourceBuffer_ChangeType_Method",
    "2524": "PPAPIWebSocket",
    "2525": "V8MediaStreamTrack_ContentHint_AttributeGetter",
    "2526": "V8MediaStreamTrack_ContentHint_AttributeSetter",
    "2527": "V8IDBFactory_Open_Method",
    "2528": "EvaluateScriptMovedBetweenDocuments",
    "2529": "ReportingObserver",
    "2530": "DeprecationReport",
    "2531": "InterventionReport",
    "2532": "V8WasmSharedMemory",
    "2533": "V8WasmThreadOpcodes",
    "2534": "CacheStorageAddAllSuccessWithDuplicate",
    "2535": "LegendDelegateFocusOrAccessKey",
    "2536": "FeaturePolicyReport",
    "2537": "V8Window_WebkitRTCPeerConnection_ConstructorGetter",
    "2538": "V8Window_WebkitMediaStream_ConstructorGetter",
    "2539": "TextEncoderStreamConstructor",
    "2540": "TextDecoderStreamConstructor",
    "2541": "SignedExchangeInnerResponse",
    "2542": "PaymentAddressLanguageCode",
    "2543": "DocumentDomainBlockedCrossOriginAccess",
    "2544": "DocumentDomainEnabledCrossOriginAccess",
    "2545": "SerialGetPorts",
    "2546": "SerialRequestPort",
    "2547": "SerialPortOpen",
    "2548": "SerialPortClose",
    "2549": "BackgroundFetchManagerFetch",
    "2550": "BackgroundFetchManagerGet",
    "2551": "BackgroundFetchManagerGetIds",
    "2552": "BackgroundFetchRegistrationAbort",
    "2553": "BackgroundFetchRegistrationMatch",
    "2554": "BackgroundFetchRegistrationMatchAll",
    "2555": "V8AtomicsNotify",
    "2556": "V8AtomicsWake",
    "2557": "FormDisabledAttributePresent",
    "2558": "FormDisabledAttributePresentAndSubmit",
    "2559": "CSSValueAppearanceCheckboxRendered",
    "2560": "CSSValueAppearanceCheckboxForOthersRendered",
    "2561": "CSSValueAppearanceRadioRendered",
    "2562": "CSSValueAppearanceRadioForOthersRendered",
    "2563": "CSSValueAppearanceInnerSpinButtonRendered",
    "2564": "CSSValueAppearanceInnerSpinButtonForOthersRendered",
    "2565": "CSSValueAppearanceMenuListRendered",
    "2566": "CSSValueAppearanceMenuListForOthersRendered",
    "2567": "CSSValueAppearanceProgressBarRendered",
    "2568": "CSSValueAppearanceSliderHorizontalRendered",
    "2569": "CSSValueAppearanceSliderHorizontalForOthersRendered",
    "2570": "CSSValueAppearanceSliderVerticalRendered",
    "2571": "CSSValueAppearanceSliderVerticalForOthersRendered",
    "2572": "CSSValueAppearanceSliderThumbHorizontalRendered",
    "2573": "CSSValueAppearanceSliderThumbHorizontalForOthersRendered",
    "2574": "CSSValueAppearanceSliderThumbVerticalRendered",
    "2575": "CSSValueAppearanceSliderThumbVerticalForOthersRendered",
    "2576": "CSSValueAppearanceSearchFieldRendered",
    "2577": "CSSValueAppearanceSearchFieldForOthersRendered",
    "2578": "CSSValueAppearanceSearchCancelRendered",
    "2579": "CSSValueAppearanceSearchCancelForOthersRendered",
    "2580": "CSSValueAppearanceTextAreaRendered",
    "2581": "CSSValueAppearanceTextAreaForOthersRendered",
    "2582": "CSSValueAppearanceMenuListButtonRendered",
    "2583": "CSSValueAppearanceMenuListButtonForOthersRendered",
    "2584": "CSSValueAppearancePushButtonRendered",
    "2585": "CSSValueAppearancePushButtonForOthersRendered",
    "2586": "CSSValueAppearanceSquareButtonRendered",
    "2587": "CSSValueAppearanceSquareButtonForOthersRendered",
    "2588": "GetComputedStyleForWebkitAppearance",
    "2589": "CursorImageLE32x32",
    "2590": "CursorImageGT32x32",
    "2591": "RTCPeerConnectionComplexPlanBSdpUsingDefaultSdpSemantics",
    "2592": "ResizeObserver_Constructor",
    "2593": "Collator",
    "2594": "NumberFormat",
    "2595": "DateTimeFormat",
    "2596": "PluralRules",
    "2597": "RelativeTimeFormat",
    "2598": "Locale",
    "2599": "ListFormat",
    "2600": "Segmenter",
    "2601": "StringLocaleCompare",
    "2602": "StringToLocaleUpperCase",
    "2603": "StringToLocaleLowerCase",
    "2604": "NumberToLocaleString",
    "2605": "DateToLocaleString",
    "2606": "DateToLocaleDateString",
    "2607": "DateToLocaleTimeString",
    "2608": "MalformedCSP",
    "2609": "V8AttemptOverrideReadOnlyOnPrototypeSloppy",
    "2610": "V8AttemptOverrideReadOnlyOnPrototypeStrict",
    "2611": "HTMLCanvasElementLowLatency",
    "2612": "V8OptimizedFunctionWithOneShotBytecode",
    "2613": "SVGGeometryPropertyHasNonZeroUnitlessValue",
    "2614": "CSSValueAppearanceNoImplementationSkipBorder",
    "2615": "InstantiateModuleScript",
    "2616": "DynamicImportModuleScript",
    "2617": "HistoryPushState",
    "2618": "HistoryReplaceState",
    "2619": "GetDisplayMedia",
    "2620": "CursorImageGT64x64",
    "2621": "AdClick",
    "2622": "UpdateWithoutShippingOptionOnShippingAddressChange",
    "2623": "UpdateWithoutShippingOptionOnShippingOptionChange",
    "2624": "CSSSelectorEmptyWhitespaceOnlyFail",
    "2625": "ActivatedImplicitRootScroller",
    "2626": "CSSUnknownNamespacePrefixInSelector",
    "2627": "PageLifeCycleFreeze",
    "2628": "DefaultInCustomIdent",
    "2629": "HTMLAnchorElementHrefTranslateAttribute",
    "2630": "WebKitUserModifyEffective",
    "2631": "PlainTextEditingEffective",
    "2632": "NavigationDownloadInSandboxWithUserGesture",
    "2633": "NavigationDownloadInSandboxWithoutUserGesture",
    "2634": "LegacyTLSVersionInMainFrameResource",
    "2635": "LegacyTLSVersionInSubresource",
    "2636": "LegacyTLSVersionInSubframeMainResource",
    "2637": "RTCMaxAudioBufferSize",
    "2638": "WebKitUserModifyReadWriteEffective",
    "2639": "WebKitUserModifyReadOnlyEffective",
    "2640": "WebKitUserModifyPlainTextEffective",
    "2641": "CSSAtRuleFontFeatureValues",
    "2642": "FlexboxSingleLineAlignContent",
    "2643": "SignedExchangeInnerResponseInMainFrame",
    "2644": "SignedExchangeInnerResponseInSubFrame",
    "2645": "CSSSelectorNotWithValidList",
    "2646": "CSSSelectorNotWithInvalidList",
    "2647": "CSSSelectorNotWithPartiallyValidList",
    "2648": "V8IDBFactory_Databases_Method",
    "2649": "OpenerNavigationDownloadCrossOriginNoGesture",
    "2650": "V8RegExpMatchIsTrueishOnNonJSRegExp",
    "2651": "V8RegExpMatchIsFalseishOnJSRegExp",
    "2652": "DownloadInAdFrameWithUserGesture",
    "2653": "DownloadInAdFrameWithoutUserGesture",
    "2654": "NavigatorAppVersion",
    "2655": "NavigatorDoNotTrack",
    "2656": "NavigatorHardwareConcurrency",
    "2657": "NavigatorLanguage",
    "2658": "NavigatorLanguages",
    "2659": "NavigatorMaxTouchPoints",
    "2660": "NavigatorMimeTypes",
    "2661": "NavigatorPlatform",
    "2662": "NavigatorPlugins",
    "2663": "NavigatorUserAgent",
    "2664": "WebBluetoothRequestScan",
    "2665": "V8SVGGeometryElement_IsPointInFill_Method",
    "2666": "V8SVGGeometryElement_IsPointInStroke_Method",
    "2667": "V8SVGGeometryElement_GetTotalLength_Method",
    "2668": "V8SVGGeometryElement_GetPointAtLength_Method",
    "2669": "OffscreenCanvasTransferToImageBitmap",
    "2670": "OffscreenCanvasIsPointInPath",
    "2671": "OffscreenCanvasIsPointInStroke",
    "2672": "OffscreenCanvasMeasureText",
    "2673": "OffscreenCanvasGetImageData",
    "2674": "V8SVGTextContentElement_GetComputedTextLength_Method",
    "2675": "V8SVGTextContentElement_GetEndPositionOfChar_Method",
    "2676": "V8SVGTextContentElement_GetExtentOfChar_Method",
    "2677": "V8SVGTextContentElement_GetStartPositionOfChar_Method",
    "2678": "V8SVGTextContentElement_GetSubStringLength_Method",
    "2679": "V8BatteryManager_ChargingTime_AttributeGetter",
    "2680": "V8BatteryManager_Charging_AttributeGetter",
    "2681": "V8BatteryManager_DischargingTime_AttributeGetter",
    "2682": "V8BatteryManager_Level_AttributeGetter",
    "2683": "V8PaintRenderingContext2D_IsPointInPath_Method",
    "2684": "V8PaintRenderingContext2D_IsPointInStroke_Method",
    "2685": "V8PaymentRequest_CanMakePayment_Method",
    "2686": "V8AnalyserNode_GetByteFrequencyData_Method",
    "2687": "V8AnalyserNode_GetByteTimeDomainData_Method",
    "2688": "V8AnalyserNode_GetFloatFrequencyData_Method",
    "2689": "V8AnalyserNode_GetFloatTimeDomainData_Method",
    "2690": "V8AudioBuffer_CopyFromChannel_Method",
    "2691": "V8AudioBuffer_GetChannelData_Method",
    "2692": "WebGLDebugRendererInfo",
    "2693": "V8WebGL2ComputeRenderingContext_GetExtension_Method",
    "2694": "V8WebGL2ComputeRenderingContext_GetSupportedExtensions_Method",
    "2695": "V8WebGL2RenderingContext_GetExtension_Method",
    "2696": "V8WebGL2RenderingContext_GetSupportedExtensions_Method",
    "2697": "V8WebGLRenderingContext_GetExtension_Method",
    "2698": "V8WebGLRenderingContext_GetSupportedExtensions_Method",
    "2699": "V8Screen_AvailHeight_AttributeGetter",
    "2700": "V8Screen_AvailWidth_AttributeGetter",
    "2701": "V8Screen_ColorDepth_AttributeGetter",
    "2702": "V8Screen_Height_AttributeGetter",
    "2703": "V8Screen_PixelDepth_AttributeGetter",
    "2704": "V8Screen_Width_AttributeGetter",
    "2705": "WindowInnerWidth",
    "2706": "WindowInnerHeight",
    "2707": "V8Window_MatchMedia_Method",
    "2708": "WindowScrollX",
    "2709": "WindowScrollY",
    "2710": "WindowPageXOffset",
    "2711": "WindowPageYOffset",
    "2712": "WindowScreenX",
    "2713": "WindowScreenY",
    "2714": "WindowOuterHeight",
    "2715": "WindowOuterWidth",
    "2716": "WindowDevicePixelRatio",
    "2717": "CanvasCaptureStream",
    "2718": "V8HTMLMediaElement_CanPlayType_Method",
    "2719": "HistoryLength",
    "2720": "FeaturePolicyReportOnlyHeader",
    "2721": "V8PaymentRequest_HasEnrolledInstrument_Method",
    "2722": "TrustedTypesEnabled",
    "2723": "TrustedTypesCreatePolicy",
    "2724": "TrustedTypesDefaultPolicyUsed",
    "2725": "TrustedTypesAssignmentError",
    "2726": "BadgeSet",
    "2727": "BadgeClear",
    "2728": "ElementTimingExplicitlyRequested",
    "2729": "V8HTMLMediaElement_CaptureStream_Method",
    "2730": "QuirkyLineBoxBackgroundSize",
    "2731": "DirectlyCompositedImage",
    "2732": "ForbiddenSyncXhrInPageDismissal",
    "2733": "V8HTMLVideoElement_AutoPictureInPicture_AttributeGetter",
    "2734": "V8HTMLVideoElement_AutoPictureInPicture_AttributeSetter",
    "2735": "AutoPictureInPictureAttribute",
    "2736": "RTCAudioJitterBufferRtxHandling",
    "2737": "WebShareCanShare",
    "2738": "PriorityHints",
    "2739": "TextAutosizedCrossSiteIframe",
    "2740": "V8RTCQuicTransport_Constructor",
    "2741": "V8RTCQuicTransport_Transport_AttributeGetter",
    "2742": "V8RTCQuicTransport_State_AttributeGetter",
    "2743": "V8RTCQuicTransport_GetKey_Method",
    "2744": "V8RTCQuicTransport_GetStats_Method",
    "2745": "V8RTCQuicTransport_Connect_Method",
    "2746": "V8RTCQuicTransport_Listen_Method",
    "2747": "V8RTCQuicTransport_Stop_Method",
    "2748": "V8RTCQuicTransport_CreateStream_Method",
    "2749": "V8RTCIceTransport_Constructor",
    "2750": "V8RTCIceTransport_Role_AttributeGetter",
    "2751": "V8RTCIceTransport_State_AttributeGetter",
    "2752": "V8RTCIceTransport_GatheringState_AttributeGetter",
    "2753": "V8RTCIceTransport_GetLocalCandidates_Method",
    "2754": "V8RTCIceTransport_GetRemoteCandidates_Method",
    "2755": "V8RTCIceTransport_GetSelectedCandidatePair_Method",
    "2756": "V8RTCIceTransport_GetLocalParameters_Method",
    "2757": "V8RTCIceTransport_GetRemoteParameters_Method",
    "2758": "V8RTCQuicStream_Transport_AttributeGetter",
    "2759": "V8RTCQuicStream_State_AttributeGetter",
    "2760": "V8RTCQuicStream_ReadBufferedAmount_AttributeGetter",
    "2761": "V8RTCQuicStream_MaxReadBufferedAmount_AttributeGetter",
    "2762": "V8RTCQuicStream_WriteBufferedAmount_AttributeGetter",
    "2763": "V8RTCQuicStream_MaxWriteBufferedAmount_AttributeGetter",
    "2764": "V8RTCQuicStream_ReadInto_Method",
    "2765": "V8RTCQuicStream_Write_Method",
    "2766": "V8RTCQuicStream_Reset_Method",
    "2767": "V8RTCQuicStream_WaitForWriteBufferedAmountBelow_Method",
    "2768": "V8RTCQuicStream_WaitForReadable_Method",
    "2769": "HTMLTemplateElement",
    "2770": "NoSysexWebMIDIWithoutPermission",
    "2771": "NoSysexWebMIDIOnInsecureOrigin",
    "2772": "ApplicationCacheInstalledButNoManifest",
    "2773": "PerMethodCanMakePaymentQuota",
    "2774": "CSSValueAppearanceButtonForNonButtonRendered",
    "2775": "CSSValueAppearanceButtonForOthersRendered",
    "2776": "CustomCursorIntersectsViewport",
    "2777": "ClientHintsLang",
    "2778": "LinkRelPreloadImageSrcset",
    "2779": "V8HTMLMediaElement_Remote_AttributeGetter",
    "2780": "V8RemotePlayback_WatchAvailability_Method",
    "2781": "V8RemotePlayback_Prompt_Method",
    "2782": "LayoutJankExplicitlyRequested",
    "2783": "MediaSessionSkipAd",
    "2784": "AdFrameSizeIntervention",
    "2785": "V8UserActivation_HasBeenActive_AttributeGetter",
    "2786": "V8UserActivation_IsActive_AttributeGetter",
    "2787": "TextEncoderEncodeInto",
    "2788": "InvalidBasicCardMethodData",
    "2789": "ClientHintsUA",
    "2790": "ClientHintsUAArch",
    "2791": "ClientHintsUAPlatform",
    "2792": "ClientHintsUAModel",
    "2793": "AnimationFrameCancelledWithinFrame",
    "2794": "SchedulingIsInputPending",
    "2795": "V8StringNormalize",
    "2796": "CSSValueAppearanceButtonBevel",
    "2797": "CSSValueAppearanceListitem",
    "2798": "CSSValueAppearanceMediaControlsBackground",
    "2799": "CSSValueAppearanceMediaControlsFullscreenBackground",
    "2800": "CSSValueAppearanceMediaCurrentTimeDisplay",
    "2801": "CSSValueAppearanceMediaEnterFullscreenButton",
    "2802": "CSSValueAppearanceMediaExitFullscreenButton",
    "2803": "CSSValueAppearanceMediaMuteButton",
    "2804": "CSSValueAppearanceMediaOverlayPlayButton",
    "2805": "CSSValueAppearanceMediaPlayButton",
    "2806": "CSSValueAppearanceMediaTimeRemainingDisplay",
    "2807": "CSSValueAppearanceMediaToggleClosedCaptionsButton",
    "2808": "CSSValueAppearanceMediaVolumeSliderContainer",
    "2809": "CSSValueAppearanceMenulistTextfield",
    "2810": "CSSValueAppearanceMenulistText",
    "2811": "CSSValueAppearanceProgressBarValue",
    "2812": "U2FCryptotokenRegister",
    "2813": "U2FCryptotokenSign",
    "2814": "CSSValueAppearanceInnerSpinButton",
    "2815": "CSSValueAppearanceMeter",
    "2816": "CSSValueAppearanceProgressBar",
    "2817": "CSSValueAppearanceProgressBarForOthersRendered",
    "2818": "CSSValueAppearancePushButton",
    "2819": "CSSValueAppearanceSquareButton",
    "2820": "CSSValueAppearanceSearchCancel",
    "2821": "CSSValueAppearanceTextarea",
    "2822": "CSSValueAppearanceTextFieldForOthersRendered",
    "2823": "CSSValueAppearanceTextFieldForTemporalRendered",
    "2824": "BuiltInModuleKvStorage",
    "2825": "BuiltInModuleVirtualScroller",
    "2826": "AdClickNavigation",
    "2827": "RTCStatsRelativePacketArrivalDelay",
    "2829": "CSSSelectorHostContextInSnapshotProfile",
    "2830": "CSSSelectorHostContextInLiveProfile",
    "2831": "ImportMap",
    "2832": "RefreshHeader",
    "2833": "SearchEventFired",
    "2834": "IdleDetectionStart",
    "2835": "TargetCurrent",
    "2836": "SandboxBackForwardStaysWithinSubtree",
    "2837": "SandboxBackForwardAffectsFramesOutsideSubtree",
    "2838": "DownloadPrePolicyCheck",
    "2839": "DownloadPostPolicyCheck",
    "2840": "DownloadInSandboxWithoutUserGesture",
    "2841": "ReadableStreamGetReader",
    "2842": "ReadableStreamPipeThrough",
    "2843": "ReadableStreamPipeTo",
    "2844": "CSSStyleSheetReplace",
    "2845": "CSSStyleSheetReplaceSync",
    "2846": "AdoptedStyleSheets",
    "2847": "HTMLImportsOnReverseOriginTrials",
    "2848": "ElementCreateShadowRootOnReverseOriginTrials",
    "2849": "DocumentRegisterElementOnReverseOriginTrials",
    "2850": "InputTypeRadio",
    "2851": "InputTypeCheckbox",
    "2852": "InputTypeImage",
    "2853": "InputTypeButton",
    "2854": "InputTypeHidden",
    "2855": "InputTypeReset",
    "2856": "SelectElementSingle",
    "2857": "SelectElementMultiple",
    "2858": "V8Animation_Effect_AttributeGetter",
    "2859": "V8Animation_Effect_AttributeSetter",
    "2860": "HidDeviceClose",
    "2861": "HidDeviceOpen",
    "2862": "HidDeviceReceiveFeatureReport",
    "2863": "HidDeviceSendFeatureReport",
    "2864": "HidDeviceSendReport",
    "2865": "HidGetDevices",
    "2866": "HidRequestDevice",
    "2867": "V8RTCQuicTransport_MaxDatagramLength_AttributeGetter",
    "2868": "V8RTCQuicTransport_ReadyToSendDatagram_Method",
    "2869": "V8RTCQuicTransport_SendDatagram_Method",
    "2870": "V8RTCQuicTransport_ReceiveDatagrams_Method",
    "2871": "CSSValueContainStyle",
    "2872": "WebShareSuccessfulContainingFiles",
    "2873": "WebShareSuccessfulWithoutFiles",
    "2874": "WebShareUnsuccessfulContainingFiles",
    "2875": "WebShareUnsuccessfulWithoutFiles",
    "2876": "VerticalScrollbarThumbScrollingWithMouse",
    "2877": "VerticalScrollbarThumbScrollingWithTouch",
    "2878": "HorizontalScrollbarThumbScrollingWithMouse",
    "2879": "HorizontalScrollbarThumbScrollingWithTouch",
    "2880": "SMSReceiverStart",
    "2881": "V8Animation_Pending_AttributeGetter",
    "2882": "FocusWithoutUserActivationNotSandboxedNotAdFrame",
    "2883": "FocusWithoutUserActivationNotSandboxedAdFrame",
    "2884": "FocusWithoutUserActivationSandboxedNotAdFrame",
    "2885": "FocusWithoutUserActivationSandboxedAdFrame",
    "2886": "V8RTCRtpReceiver_JitterBufferDelayHint_AttributeGetter",
    "2887": "V8RTCRtpReceiver_JitterBufferDelayHint_AttributeSetter",
    "2888": "MediaCapabilitiesDecodingInfoWithKeySystemConfig",
    "2889": "RevertInCustomIdent",
    "2890": "UnoptimizedImagePolicies",
    "2891": "VTTCueParser",
    "2892": "MediaElementTextTrackContainer",
    "2893": "MediaElementTextTrackList",
    "2894": "PaymentRequestInitialized",
    "2895": "PaymentRequestShow",
    "2896": "PaymentRequestShippingAddressChange",
    "2897": "PaymentRequestShippingOptionChange",
    "2898": "PaymentRequestPaymentMethodChange",
    "2899": "V8Animation_UpdatePlaybackRate_Method",
    "2900": "TwoValuedOverflow",
    "2901": "TextFragmentAnchor",
    "2902": "TextFragmentAnchorMatchFound",
    "2903": "NonPassiveTouchEventListener",
    "2904": "PassiveTouchEventListener",
    "2905": "CSSValueAppearanceSearchCancelForOthers2Rendered",
    "2906": "WebXrFramebufferScale",
    "2907": "WebXrIgnoreDepthValues",
    "2908": "WebXrSessionCreated",
    "2909": "V8XRReferenceSpace_GetOffsetReferenceSpace_Method",
    "2910": "V8XRInputSource_Gamepad_AttributeGetter",
    "2911": "V8XRSession_End_Method",
    "2912": "V8XRWebGLLayer_Constructor",
    "2913": "FetchKeepalive",
    "2914": "CSSTransitionCancelledByRemovingStyle",
    "2915": "V8RTCRtpSender_SetStreams_Method",
    "2916": "CookieNoSameSite",
    "2917": "CookieInsecureAndSameSiteNone",
    "2918": "UnsizedMediaPolicy",
    "2919": "ScrollByPrecisionTouchPad",
    "2920": "PinchZoom",
    "2921": "BuiltInModuleSwitchImported",
    "2922": "FeaturePolicyCommaSeparatedDeclarations",
    "2923": "FeaturePolicySemicolonSeparatedDeclarations",
    "2924": "V8CallSiteAPIGetFunctionSloppyCall",
    "2925": "V8CallSiteAPIGetThisSloppyCall",
    "2926": "BuiltInModuleToast",
    "2927": "LargestContentfulPaintExplicitlyRequested",
    "2928": "PageLifecycleTransitionsOptIn",
    "2929": "PageLifecycleTransitionsOptOut",
    "2930": "PeriodicBackgroundSync",
    "2931": "PeriodicBackgroundSyncRegister",
    "2932": "LazyLoadFrameLoadingAttributeEager",
    "2933": "LazyLoadFrameLoadingAttributeLazy",
    "2934": "LazyLoadImageLoadingAttributeEager",
    "2935": "LazyLoadImageLoadingAttributeLazy",
    "2936": "LazyLoadImageMissingDimensionsForLazy",
    "2937": "PeriodicBackgroundSyncGetTags",
    "2938": "PeriodicBackgroundSyncUnregister",
    "2939": "CreateObjectURLMediaSourceFromWorker",
    "2940": "CSSAtRuleProperty",
    "2941": "ServiceWorkerInterceptedRequestFromOriginDirtyStyleSheet",
    "2942": "WebkitMarginBeforeCollapseDiscard",
    "2943": "WebkitMarginBeforeCollapseSeparate",
    "2944": "WebkitMarginBeforeCollapseSeparateMaybeDoesSomething",
    "2945": "WebkitMarginAfterCollapseDiscard",
    "2946": "WebkitMarginAfterCollapseSeparate",
    "2947": "WebkitMarginAfterCollapseSeparateMaybeDoesSomething",
    "2949": "CredentialManagerGetWithUVM",
    "2951": "CredentialManagerGetSuccessWithUVM",
    "2952": "DiscardInputEventToMovingIframe",
    "2953": "SignedExchangeSubresourcePrefetch",
    "2954": "BasicCardType",
    "2955": "ExecutedJavaScriptURL",
    "2956": "LinkPrefetchLoadEvent",
    "2957": "LinkPrefetchErrorEvent",
    "2958": "FontSizeWebkitXxxLarge",
    "2959": "V8Database_ChangeVersion_Method",
    "2960": "V8Database_Transaction_Method",
    "2961": "V8Database_ReadTransaction_Method",
    "2962": "V8SQLTransaction_ExecuteSql_Method",
    "2963": "CSSValueAppearanceButtonForBootstrapLooseSelectorRendered",
    "2964": "CSSValueAppearanceButtonForOthers2Rendered",
    "2965": "CSSValueAppearanceButtonForSelectRendered",
    "2966": "CSSValueAppearanceListboxForOthersRendered",
    "2967": "CSSValueAppearanceMeterForOthersRendered",
    "2968": "SVGSMILDiscardElementParsed",
    "2969": "SVGSMILDiscardElementTriggered",
    "2971": "V8PointerEvent_GetPredictedEvents_Method",
    "2972": "ScrollSnapOnViewportBreaks",
    "2973": "ScrollPaddingOnViewportBreaks",
    "2974": "DownloadInAdFrame",
    "2975": "DownloadInSandbox",
    "2976": "DownloadWithoutUserGesture",
    "2977": "AutoplayDynamicDelegation",
    "2978": "ToggleEventHandlerDuringParsing",
    "2979": "FragmentDoubleHash",
    "2981": "OBSOLETE_CSSValueOverflowXOverlay",
    "2982": "OBSOLETE_CSSValueOverflowYOverlay",
    "2983": "ContentIndexAdd",
    "2984": "ContentIndexDelete",
    "2985": "ContentIndexGet",
    "2986": "V8SpeechGrammar_Constructor",
    "2987": "V8SpeechGrammarList_AddFromString_Method",
    "2988": "V8SpeechGrammarList_Constructor",
    "2989": "V8SpeechGrammarList_Item_Method",
    "2990": "V8SpeechRecognition_Constructor",
    "2991": "V8SpeechRecognition_Grammars_AttributeGetter",
    "2992": "V8SpeechRecognition_Grammars_AttributeSetter",
    "2993": "ContactsManagerSelect",
    "2994": "V8MediaSession_SetPositionState_Method",
    "2995": "CSSValueOverflowOverlay",
    "2996": "RequestedFileSystemTemporary",
    "2997": "RequestedFileSystemPersistent",
    "2998": "ElementWithLeftwardOrUpwardOverflowDirection_ScrollLeftOrTop",
    "2999": "ElementWithLeftwardOrUpwardOverflowDirection_ScrollLeftOrTopSetPositive",
    "3000": "XMLHttpRequestSynchronousInMainFrame",
    "3001": "XMLHttpRequestSynchronousInCrossOriginSubframe",
    "3002": "XMLHttpRequestSynchronousInSameOriginSubframe",
    "3003": "XMLHttpRequestSynchronousInWorker",
    "3004": "PerformanceObserverBufferedFlag",
    "3005": "WakeLockAcquireScreenLock",
    "3006": "WakeLockAcquireSystemLock",
    "3007": "ThirdPartyServiceWorker",
    "3008": "JSSelfProfiling",
    "3009": "HTMLFrameSetElement",
    "3010": "MediaCapabilitiesFramerateRatio",
    "3011": "MediaCapabilitiesFramerateNumber",
    "3012": "FetchRedirectError",
    "3013": "FetchRedirectManual",
    "3014": "FetchCacheReload",
    "3015": "V8Window_ChooseFileSystemEntries_Method",
    "3016": "V8FileSystemDirectoryHandle_GetSystemDirectory_Method",
    "3017": "NotificationShowTrigger",
    "3018": "WebSocketStreamConstructor",
    "3019": "DOMStorageRead",
    "3020": "DOMStorageWrite",
    "3021": "CacheStorageRead",
    "3022": "CacheStorageWrite",
    "3023": "IndexedDBRead",
    "3024": "IndexedDBWrite",
    "3025": "DeprecatedFileSystemRead",
    "3026": "DeprecatedFileSystemWrite",
    "3027": "PointerLockUnadjustedMovement",
    "3028": "CreateObjectBlob",
    "3029": "QuotaRead",
    "3030": "DelegateFocus",
    "3031": "DelegateFocusNotFirstInFlatTree",
    "3032": "ThirdPartySharedWorker",
    "3033": "ThirdPartyBroadcastChannel",
    "3034": "MediaSourceGroupEndTimestampDecreaseWithinMediaSegment",
    "3035": "TextFragmentAnchorTapToDismiss",
    "3036": "XRIsSessionSupported",
    "3037": "ScrollbarUseScrollbarButtonReversedDirection",
    "3038": "CSSSelectorPseudoScrollbarButtonReversedDirection",
    "3039": "FragmentHasTildeAmpersandTilde",
    "3040": "FragmentHasColonTildeColon",
    "3041": "FragmentHasTildeAtTilde",
    "3042": "FragmentHasAmpersandDelimiterQuestion",
    "3043": "InvalidFragmentDirective",
    "3044": "ContactsManagerGetProperties",
    "3045": "EvaluateScriptMovedBetweenElementDocuments",
    "3046": "PluginElementLoadedDocument",
    "3047": "PluginElementLoadedImage",
    "3048": "PluginElementLoadedExternal",
    "3049": "RenderSubtreeAttribute",
    "3050": "ARIAAnnotationRoles",
    "3051": "IntersectionObserverV2",
    "3052": "HeavyAdIntervention",
    "3053": "UserTimingL3",
    "3054": "GetGamepadsFromCrossOriginSubframe",
    "3055": "GetGamepadsFromInsecureContext",
    "3056": "OriginCleanImageBitmapSerialization",
    "3057": "NonOriginCleanImageBitmapSerialization",
    "3058": "OriginCleanImageBitmapTransfer",
    "3059": "NonOriginCleanImageBitmapTransfer",
    "3060": "CompressionStreamConstructor",
    "3061": "DecompressionStreamConstructor",
    "3062": "V8RTCRtpReceiver_PlayoutDelayHint_AttributeGetter",
    "3063": "V8RTCRtpReceiver_PlayoutDelayHint_AttributeSetter",
    "3064": "V8RegExpExecCalledOnSlowRegExp",
    "3065": "V8RegExpReplaceCalledOnSlowRegExp",
    "3066": "HasMarkerPseudoElement",
    "3067": "WindowMove",
    "3068": "WindowResize",
    "3069": "MovedOrResizedPopup",
    "3070": "MovedOrResizedPopup2sAfterCreation",
    "3071": "DOMWindowOpenPositioningFeatures",
    "3072": "MouseEventScreenX",
    "3073": "MouseEventScreenY",
    "3074": "CredentialManagerIsUserVerifyingPlatformAuthenticatorAvailable",
    "3075": "ObsoleteWebrtcTlsVersion",
    "3076": "UpgradeInsecureRequestsUpgradedRequestBlockable",
    "3077": "UpgradeInsecureRequestsUpgradedRequestOptionallyBlockable",
    "3078": "UpgradeInsecureRequestsUpgradedRequestWebsocket",
    "3079": "UpgradeInsecureRequestsUpgradedRequestForm",
    "3080": "UpgradeInsecureRequestsUpgradedRequestUnknown",
    "3081": "HasGlyphRelativeUnits",
    "3082": "CountQueuingStrategyConstructor",
    "3083": "ByteLengthQueuingStrategyConstructor",
    "3084": "ClassicDedicatedWorker",
    "3085": "ModuleDedicatedWorker",
    "3086": "FetchBodyStreamInServiceWorker",
    "3087": "FetchBodyStreamOutsideServiceWorker",
    "3088": "GetComputedStyleOutsideFlatTree",
    "3089": "ARIADescriptionAttribute",
    "3090": "StrictMimeTypeChecksWouldBlockWorker",
    "3091": "ResourceTimingTaintedOriginFlagFail",
    "3092": "RegisterProtocolHandlerSameOriginAsTop",
    "3093": "RegisterProtocolHandlerCrossOriginSubframe",
    "3094": "WebNfcNdefReaderScan",
    "3095": "WebNfcNdefWriterWrite",
    "3096": "HTMLPortalElement",
    "3097": "V8HTMLPortalElement_Activate_Method",
    "3098": "V8HTMLPortalElement_PostMessage_Method",
    "3099": "V8Window_PortalHost_AttributeGetter",
    "3100": "V8PortalHost_PostMessage_Method",
    "3101": "V8PortalActivateEvent_Data_AttributeGetter",
    "3102": "V8PortalActivateEvent_AdoptPredecessor_Method",
    "3103": "LinkRelPrefetchForSignedExchanges",
    "3104": "MessageEventSharedArrayBufferSameOrigin",
    "3105": "MessageEventSharedArrayBufferSameAgentCluster",
    "3106": "MessageEventSharedArrayBufferDifferentAgentCluster",
    "3107": "CacheStorageCodeCacheHint",
    "3108": "V8Metadata_ModificationTime_AttributeGetter",
    "3109": "V8RTCLegacyStatsReport_Timestamp_AttributeGetter",
    "3110": "InputElementValueAsDateGetter",
    "3111": "InputElementValueAsDateSetter",
    "3112": "HTMLMetaElementReferrerPolicy",
    "3113": "NonWebbyMixedContent",
    "3114": "V8SharedArrayBufferConstructed",
    "3115": "ScrollSnapCausesScrollOnInitialLayout",
    "3116": "ClientHintsUAMobile",
    "3117": "V8VideoPlaybackQuality_CorruptedVideoFrames_AttributeGetter",
    "3118": "LongTaskBufferFull",
    "3119": "HTMLMetaElementMonetization",
    "3120": "HTMLLinkElementMonetization",
    "3121": "InputTypeCheckboxRenderedNonSquare",
    "3122": "InputTypeRadioRenderedNonSquare",
    "3123": "WebkitBoxPackJustifyDoesSomething",
    "3124": "WebkitBoxPackCenterDoesSomething",
    "3125": "WebkitBoxPackEndDoesSomething",
    "3126": "V8KeyframeEffect_Constructor",
    "3127": "WebNfcAPI",
    "3128": "HostCandidateAttributeGetter",
    "3129": "CSPWithReasonableObjectRestrictions",
    "3130": "CSPWithReasonableBaseRestrictions",
    "3131": "CSPWithReasonableScriptRestrictions",
    "3132": "CSPWithReasonableRestrictions",
    "3133": "CSPROWithReasonableObjectRestrictions",
    "3134": "CSPROWithReasonableBaseRestrictions",
    "3135": "CSPROWithReasonableScriptRestrictions",
    "3136": "CSPROWithReasonableRestrictions",
    "3137": "CSPWithBetterThanReasonableRestrictions",
    "3138": "CSPROWithBetterThanReasonableRestrictions",
    "3139": "MeasureMemory",
    "3140": "V8Animation_ReplaceState_AttributeGetter",
    "3141": "V8Animation_Persist_Method",
    "3142": "TaskControllerConstructor",
    "3143": "TaskControllerSetPriority",
    "3144": "TaskSignalPriority",
    "3145": "SchedulerPostTask",
    "3146": "V8Animation_Onremove_AttributeGetter",
    "3147": "V8Animation_Onremove_AttributeSetter",
    "3148": "ClassicSharedWorker",
    "3149": "ModuleSharedWorker",
    "3150": "V8Animation_CommitStyles_Method",
    "3151": "SameOriginIframeWindowAlert",
    "3152": "SameOriginIframeWindowConfirm",
    "3153": "SameOriginIframeWindowPrompt",
    "3154": "SameOriginIframeWindowPrint",
    "3155": "LargeStickyAd",
    "3156": "OverlayInterstitialAd",
    "3157": "CSSComparisonFunctions",
    "3158": "FeaturePolicyProposalWouldChangeBehaviour",
    "3159": "RTCLocalSdpModificationSimulcast",
    "3160": "TrustedTypesEnabledEnforcing",
    "3161": "TrustedTypesEnabledReportOnly",
    "3162": "TrustedTypesAllowDuplicates",
    "3163": "V8ArrayPrototypeHasElements",
    "3164": "V8ObjectPrototypeHasElements",
    "3165": "DisallowDocumentAccess",
    "3166": "XRSessionRequestHitTestSource",
    "3167": "XRSessionRequestHitTestSourceForTransientInput",
    "3168": "XRDOMOverlay",
    "3169": "CssStyleSheetReplaceWithImport",
    "3170": "CryptoAlgorithmEd25519",
    "3171": "CryptoAlgorithmX25519",
    "3172": "DisplayNames",
    "3173": "NumberFormatStyleUnit",
    "3174": "DateTimeFormatRange",
    "3175": "DateTimeFormatDateTimeStyle",
    "3176": "BreakIteratorTypeWord",
    "3177": "BreakIteratorTypeLine",
    "3178": "V8FileSystemDirectoryHandle_Resolve_Method",
    "3179": "V8FileSystemHandle_IsSameEntry_Method",
    "3180": "V8RTCRtpSender_CreateEncodedAudioStreams_Method",
    "3181": "V8RTCRtpSender_CreateEncodedVideoStreams_Method",
    "3182": "V8RTCRtpReceiver_CreateEncodedAudioStreams_Method",
    "3183": "V8RTCRtpReceiver_CreateEncodedVideoStreams_Method",
    "3184": "QuicTransport",
    "3185": "QuicTransportStreamApis",
    "3186": "QuicTransportDatagramApis",
    "3187": "V8Document_GetAnimations_Method",
    "3188": "V8ShadowRoot_GetAnimations_Method",
    "3189": "ClientHintsUAFullVersion",
    "3190": "SchedulerCurrentTaskSignal",
    "3191": "ThirdPartyFileSystem",
    "3192": "ThirdPartyIndexedDb",
    "3193": "ThirdPartyCacheStorage",
    "3194": "ThirdPartyLocalStorage",
    "3195": "ThirdPartySessionStorage",
    "3196": "DeclarativeShadowRoot",
    "3197": "CrossOriginOpenerPolicySameOrigin",
    "3198": "CrossOriginOpenerPolicySameOriginAllowPopups",
    "3199": "CrossOriginEmbedderPolicyRequireCorp",
    "3200": "CoopAndCoepIsolated",
    "3201": "WrongBaselineOfButtonElement",
    "3202": "V8Document_HasTrustToken_Method",
    "3203": "ForceLoadAtTop",
    "3204": "LegacyLayoutByButton",
    "3205": "LegacyLayoutByDeprecatedFlexBox",
    "3206": "LegacyLayoutByDetailsMarker",
    "3207": "LegacyLayoutByEditing",
    "3208": "LegacyLayoutByFieldSet",
    "3209": "LegacyLayoutByFileUploadControl",
    "3210": "LegacyLayoutByFlexBox",
    "3211": "LegacyLayoutByFrameSet",
    "3212": "LegacyLayoutByGrid",
    "3213": "LegacyLayoutByMenuList",
    "3214": "LegacyLayoutByMultiCol",
    "3215": "LegacyLayoutByPrinting",
    "3216": "LegacyLayoutByRuby",
    "3217": "LegacyLayoutBySVG",
    "3218": "LegacyLayoutBySlider",
    "3219": "LegacyLayoutByTable",
    "3220": "LegacyLayoutByTextCombine",
    "3221": "LegacyLayoutByTextControl",
    "3222": "LegacyLayoutByVTTCue",
    "3223": "LegacyLayoutByWebkitBoxWithoutVerticalLineClamp",
    "3224": "LegacyLayoutByTableFlexGridBlockInNGFragmentationContext",
    "3225": "DocumentPolicyHeader",
    "3226": "DocumentPolicyReportOnlyHeader",
    "3227": "RequireDocumentPolicyHeader",
    "3228": "DocumentPolicyIframePolicyAttribute",
    "3229": "DocumentPolicyCausedPageUnload",
    "3230": "RequiredDocumentPolicy",
    "3231": "PerformanceObserverEntryTypesAndBuffered",
    "3232": "PerformanceObserverTypeError",
    "3233": "ImageCaptureWhiteBalanceMode",
    "3234": "ImageCaptureExposureMode",
    "3235": "ImageCaptureFocusMode",
    "3236": "ImageCapturePointsOfInterest",
    "3237": "ImageCaptureExposureCompensation",
    "3238": "ImageCaptureExposureTime",
    "3239": "ImageCaptureColorTemperature",
    "3240": "ImageCaptureIso",
    "3241": "ImageCaptureBrightness",
    "3242": "ImageCaptureContrast",
    "3243": "ImageCaptureSaturation",
    "3244": "ImageCaptureSharpness",
    "3245": "ImageCaptureFocusDistance",
    "3246": "ImageCapturePan",
    "3247": "ImageCaptureTilt",
    "3248": "ImageCaptureZoom",
    "3249": "ImageCaptureTorch",
    "3250": "XRFrameCreateAnchor",
    "3251": "XRHitTestResultCreateAnchor",
    "3252": "CSSKeywordRevert",
    "3253": "OverlayPopupAd",
    "3254": "EventTimingFirstInputExplicitlyRequested",
    "3255": "CustomScrollbarPercentThickness",
    "3256": "CustomScrollbarPartPercentLength",
    "3257": "V8InvalidatedArrayBufferDetachingProtector",
    "3258": "V8InvalidatedArrayConstructorProtector",
    "3259": "V8InvalidatedArrayIteratorLookupChainProtector",
    "3260": "V8InvalidatedArraySpeciesLookupChainProtector",
    "3261": "V8InvalidatedIsConcatSpreadableLookupChainProtector",
    "3262": "V8InvalidatedMapIteratorLookupChainProtector",
    "3263": "V8InvalidatedNoElementsProtector",
    "3264": "V8InvalidatedPromiseHookProtector",
    "3265": "V8InvalidatedPromiseResolveLookupChainProtector",
    "3266": "V8InvalidatedPromiseSpeciesLookupChainProtector",
    "3267": "V8InvalidatedPromiseThenLookupChainProtector",
    "3268": "V8InvalidatedRegExpSpeciesLookupChainProtector",
    "3269": "V8InvalidatedSetIteratorLookupChainProtector",
    "3270": "V8InvalidatedStringIteratorLookupChainProtector",
    "3271": "V8InvalidatedStringLengthOverflowLookupChainProtector",
    "3272": "V8InvalidatedTypedArraySpeciesLookupChainProtector",
    "3273": "ClientHintsUAPlatformVersion",
    "3274": "IFrameCSPAttribute",
    "3275": "NavigatorCookieEnabled",
    "3276": "TrustTokenFetch",
    "3277": "TrustTokenXhr",
    "3278": "TrustTokenIframe",
    "3279": "TrustedTypesPolicyCreated",
    "3280": "V8HTMLVideoElement_RequestVideoFrameCallback_Method",
    "3281": "V8HTMLVideoElement_CancelVideoFrameCallback_Method",
    "3282": "RubyElementWithDisplayBlock",
    "3283": "LocationFragmentDirectiveAccessed",
    "3284": "CanvasRenderingContext",
    "3285": "SchemefulSameSiteContextDowngrade",
    "3286": "OriginIsolationHeader",
    "3287": "V8WasmSimdOpcodes",
    "3288": "GridRowGapPercent",
    "3289": "GridRowGapPercentIndefinite",
    "3290": "FlexRowGapPercent",
    "3291": "FlexRowGapPercentIndefinite",
    "3292": "V8RTCRtpSender_CreateEncodedStreams_Method",
    "3293": "V8RTCRtpReceiver_CreateEncodedStreams_Method",
    "3294": "ForceEncodedAudioInsertableStreams",
    "3295": "ForceEncodedVideoInsertableStreams",
    "3296": "TransformStyleContainingBlockComputedUsedMismatch",
    "3297": "AdditionalGroupingPropertiesForCompat",
    "3298": "PopupDoesNotExceedOwnerWindowBounds",
    "3299": "PopupExceedsOwnerWindowBounds",
    "3300": "PopupExceedsOwnerWindowBoundsForIframe",
    "3301": "PopupGestureTapExceedsOwnerWindowBounds",
    "3302": "PopupMouseDownExceedsOwnerWindowBounds",
    "3303": "PopupMouseWheelExceedsOwnerWindowBounds",
    "3304": "V8VarRedeclaredCatchBinding",
    "3305": "WebBluetoothRemoteCharacteristicWriteValueWithResponse",
    "3306": "WebBluetoothRemoteCharacteristicWriteValueWithoutResponse",
    "3307": "FlexGapSpecified",
    "3308": "FlexGapPositive",
    "3309": "PluginInstanceAccessSuccessful",
    "3310": "StorageAccessAPI_HasStorageAccess_Method",
    "3311": "StorageAccessAPI_requestStorageAccess_Method",
    "3312": "WebBluetoothWatchAdvertisements",
    "3313": "RubyTextWithNonDefaultTextAlign",
    "3314": "HTMLMetaElementReferrerPolicyOutsideHead",
    "3315": "HTMLMetaElementReferrerPolicyMultipleTokens",
    "3316": "FetchAPINonGetOrHeadOpaqueResponse",
    "3317": "FetchAPINonGetOrHeadOpaqueResponseWithRedirect",
    "3318": "DynamicImportModuleScriptRelativeClassicSameOrigin",
    "3319": "DynamicImportModuleScriptRelativeClassicCrossOrigin",
    "3320": "V8WasmBulkMemory",
    "3321": "V8WasmRefTypes",
    "3322": "V8WasmMultiValue",
    "3323": "HiddenBackfaceWithPossible3D",
    "3324": "HiddenBackfaceWithPreserve3D",
    "3325": "CSSAtRuleScrollTimeline",
    "3326": "FetchUploadStreaming",
    "3327": "WebkitLineClampWithoutWebkitBox",
    "3328": "WebBluetoothGetDevices",
    "3329": "DialogWithNonZeroScrollOffset",
    "3330": "DialogHeightLargerThanViewport",
    "3331": "OverlayPopup",
    "3332": "ContentVisibilityAuto",
    "3333": "ContentVisibilityHidden",
    "3334": "ContentVisibilityHiddenMatchable",
    "3335": "InlineOverflowAutoWithInlineEndPadding",
    "3336": "InlineOverflowScrollWithInlineEndPadding",
    "3337": "CSSSelectorPseudoWebKitDetailsMarker",
    "3338": "SerialPortGetInfo",
    "3339": "FileSystemPickerMethod",
    "3340": "V8Window_ShowOpenFilePicker_Method",
    "3341": "V8Window_ShowSaveFilePicker_Method",
    "3342": "V8Window_ShowDirectoryPicker_Method",
    "3343": "V8Window_GetOriginPrivateDirectory_Method",
    "3344": "RTCConstraintEnableRtpDataChannelsTrue",
    "3345": "RTCConstraintEnableRtpDataChannelsFalse",
    "3346": "NativeFileSystemDragAndDrop",
    "3347": "RTCAdaptivePtime",
    "3348": "HTMLMetaElementReferrerPolicyMultipleTokensAffectingRequest",
    "3349": "NavigationTimingL2",
    "3350": "ResourceTiming",
    "3351": "V8PointerEvent_AzimuthAngle_AttributeGetter",
    "3352": "V8PointerEvent_AltitudeAngle_AttributeGetter",
    "3353": "CrossBrowsingContextGroupMainFrameNulledNonEmptyNameAccessed",
    "3354": "PositionSticky",
    "3355": "CommaSeparatorInAllowAttribute",
    "3359": "MainFrameCSPViaHTTP",
    "3360": "MainFrameCSPViaMeta",
    "3361": "MainFrameCSPViaOriginPolicy",
    "3362": "HtmlClipboardApiRead",
    "3363": "HtmlClipboardApiWrite",
    "3364": "CSSSystemColorComputeToSelf",
    "3365": "ConversionAPIAll",
    "3366": "ImpressionRegistration",
    "3367": "ConversionRegistration",
    "3368": "WebSharePolicyAllow",
    "3369": "WebSharePolicyDisallow",
    "3370": "FormAssociatedCustomElement",
    "3371": "WindowClosed",
    "3372": "WrongBaselineOfMultiLineButton",
    "3373": "WrongBaselineOfEmptyLineButton",
    "3374": "V8RTCRtpTransceiver_Stopped_AttributeGetter",
    "3375": "V8RTCRtpTransceiver_Stop_Method",
    "3376": "SecurePaymentConfirmation",
    "3377": "CSSInvalidVariableUnset",
    "3378": "ElementInternalsShadowRoot",
    "3379": "AnyPiiFieldDetected_PredictedTypeMatch",
    "3380": "EmailFieldDetected_PredictedTypeMatch",
    "3381": "PhoneFieldDetected_PredictedTypeMatch",
    "3382": "EmailFieldDetected_PatternMatch",
    "3383": "LastLetterSpacingAffectsRendering",
    "3384": "V8FontMetadata_GetTables_Method",
    "3385": "V8FontMetadata_Blob_Method",
    "3386": "V8FontManager_Query_Method",
    "3387": "AudioContextBaseLatency",
    "3388": "V8Window_GetScreens_Method",
    "3389": "V8Window_IsMultiScreen_Method",
    "3390": "V8Window_Onscreenschange_AttributeGetter",
    "3391": "V8Window_Onscreenschange_AttributeSetter",
    "3392": "DOMWindowOpenPositioningFeaturesCrossScreen",
    "3393": "DOMWindowSetWindowRectCrossScreen",
    "3394": "FullscreenCrossScreen",
    "3395": "BatterySavingsMeta",
    "3396": "DigitalGoodsGetDigitalGoodsService",
    "3397": "DigitalGoodsGetDetails",
    "3398": "DigitalGoodsAcknowledge",
    "3399": "MediaRecorder_MimeType",
    "3400": "MediaRecorder_VideoBitsPerSecond",
    "3401": "MediaRecorder_AudioBitsPerSecond",
    "3402": "OBSOLETE_BluetoothRemoteGATTCharacteristic_Uuid",
    "3403": "OBSOLETE_BluetoothRemoteGATTDescriptor_Uuid",
    "3404": "OBSOLETE_BluetoothRemoteGATTService_Uuid",
    "3405": "GPUAdapter_Name",
    "3406": "WindowScreenInternal",
    "3407": "WindowScreenPrimary",
    "3408": "ThirdPartyCookieRead",
    "3409": "ThirdPartyCookieWrite",
    "3410": "RTCLegacyRtpDataChannelNegotiated",
    "3411": "CrossSitePostMessage",
    "3412": "SchemelesslySameSitePostMessage",
    "3413": "SchemefulSameSitePostMessage",
    "3414": "UnspecifiedTargetOriginPostMessage",
    "3415": "SchemelesslySameSitePostMessageSecureToInsecure",
    "3416": "SchemelesslySameSitePostMessageInsecureToSecure",
    "3417": "OBSOLETE_BCPBroadcast",
    "3418": "OBSOLETE_BCPRead",
    "3419": "OBSOLETE_BCPWriteWithoutResponse",
    "3420": "OBSOLETE_BCPWrite",
    "3421": "OBSOLETE_BCPNotify",
    "3422": "OBSOLETE_BCPIndicate",
    "3423": "OBSOLETE_BCPAuthenticatedSignedWrites",
    "3424": "OBSOLETE_BCPReliableWrite",
    "3425": "OBSOLETE_BCPWritableAuxiliaries",
    "3426": "TextAlignSpecifiedToLegend",
    "3427": "V8Document_FragmentDirective_AttributeGetter",
    "3428": "V8StorageManager_GetDirectory_Method",
    "3429": "BeforematchHandlerRegistered",
    "3430": "BluetoothAdvertisingEventName",
    "3431": "BluetoothAdvertisingEventAppearance",
    "3432": "BluetoothAdvertisingEventTxPower",
    "3433": "CrossOriginOpenerPolicyReporting",
    "3434": "GamepadId",
    "3435": "ElementAttachInternals",
    "3436": "BluetoothDeviceName",
    "3437": "RTCIceCandidateAddress",
    "3438": "RTCIceCandidateCandidate",
    "3439": "RTCIceCandidatePort",
    "3440": "RTCIceCandidateRelatedAddress",
    "3441": "RTCIceCandidateRelatedPort",
    "3442": "SlotAssignNode",
    "3443": "PluginName",
    "3444": "PluginFilename",
    "3445": "PluginDescription",
    "3446": "SubresourceWebBundles",
    "3447": "RTCPeerConnectionSetRemoteDescriptionPromise",
    "3448": "RTCPeerConnectionSetLocalDescriptionPromise",
    "3449": "RTCPeerConnectionCreateOfferPromise",
    "3450": "RTCPeerConnectionCreateAnswerPromise",
    "3451": "RTCPeerConnectionSetRemoteDescription",
    "3452": "RTCPeerConnectionSetLocalDescription",
    "3453": "RTCPeerConnectionCreateOffer",
    "3454": "RTCPeerConnectionCreateAnswer",
    "3455": "V8AuthenticatorAttestationResponse_GetTransports_Method",
    "3456": "WebCodecsAudioDecoder",
    "3457": "WebCodecsVideoDecoder",
    "3458": "WebCodecsVideoEncoder",
    "3459": "WebCodecsVideoTrackReader",
    "3460": "WebCodecsImageDecoder",
    "3461": "BackForwardCacheExperimentHTTPHeader",
    "3462": "V8Navigator_OpenTCPSocket_Method",
    "3463": "V8Navigator_OpenUDPSocket_Method",
    "3464": "WebCodecs",
    "3465": "CredentialManagerCrossOriginPublicKeyGetRequest",
    "3466": "CSSContainStrictWithoutContentVisibility",
    "3467": "CSSContainAllWithoutContentVisibility",
    "3468": "TimerInstallFromBeforeUnload",
    "3469": "TimerInstallFromUnload",
    "3470": "OBSOLETE_ElementAttachInternalsBeforeConstructor",
    "3471": "SMILElementHasRepeatNEventListener",
    "3472": "WebTransport",
    "3477": "IdleDetectionPermissionRequested",
    "3478": "IdentifiabilityStudyReserved3478",
    "3479": "SpeechSynthesis_GetVoices_Method",
    "3480": "IdentifiabilityStudyReserved3480",
    "3481": "V8Navigator_JavaEnabled_Method",
    "3482": "IdentifiabilityStudyReserved3482",
    "3483": "IdentifiabilityStudyReserved3483",
    "3484": "IdentifiabilityStudyReserved3484",
    "3485": "IdentifiabilityStudyReserved3485",
    "3486": "IdentifiabilityStudyReserved3486",
    "3487": "IdentifiabilityStudyReserved3487",
    "3488": "IdentifiabilityStudyReserved3488",
    "3489": "IdentifiabilityStudyReserved3489",
    "3490": "IdentifiabilityStudyReserved3490",
    "3491": "IdentifiabilityStudyReserved3491",
    "3492": "IdentifiabilityStudyReserved3492",
    "3493": "IdentifiabilityStudyReserved3493",
    "3494": "IdentifiabilityStudyReserved3494",
    "3495": "IdentifiabilityStudyReserved3495",
    "3496": "IdentifiabilityStudyReserved3496",
    "3497": "IdentifiabilityStudyReserved3497",
    "3498": "IdentifiabilityStudyReserved3498",
    "3499": "V8BackgroundFetchRegistration_FailureReason_AttributeGetter",
    "3500": "V8Document_ElementFromPoint_Method",
    "3501": "V8Document_ElementsFromPoint_Method",
    "3502": "V8ShadowRoot_ElementFromPoint_Method",
    "3503": "V8ShadowRoot_ElementsFromPoint_Method",
    "3504": "WindowScreenTouchSupport",
    "3505": "IdentifiabilityStudyReserved3505",
    "3506": "IdentifiabilityStudyReserved3506",
    "3507": "V8PushManager_SupportedContentEncodings_AttributeGetter",
    "3508": "IdentifiabilityStudyReserved3508",
    "3509": "V8RTCRtpReceiver_GetCapabilities_Method",
    "3510": "V8RTCRtpSender_GetCapabilities_Method",
    "3511": "IdentifiabilityStudyReserved3511",
    "3512": "IdentifiabilityStudyReserved3512",
    "3513": "IdentifiabilityStudyReserved3513",
    "3514": "IdentifiabilityStudyReserved3514",
    "3515": "IdentifiabilityStudyReserved3515",
    "3516": "IdentifiabilityStudyReserved3516",
    "3517": "IdentifiabilityStudyReserved3517",
    "3518": "IdentifiabilityStudyReserved3518",
    "3519": "IdentifiabilityStudyReserved3519",
    "3520": "IdentifiabilityStudyReserved3520",
    "3521": "IdentifiabilityStudyReserved3521",
    "3522": "IdentifiabilityStudyReserved3522",
    "3523": "IdentifiabilityStudyReserved3523",
    "3524": "IdentifiabilityStudyReserved3524",
    "3525": "IdentifiabilityStudyReserved3525",
    "3526": "IdentifiabilityStudyReserved3526",
    "3527": "IdentifiabilityStudyReserved3527",
    "3528": "IdentifiabilityStudyReserved3528",
    "3529": "IdentifiabilityStudyReserved3529",
    "3530": "IdentifiabilityStudyReserved3530",
    "3531": "IdentifiabilityStudyReserved3531",
    "3532": "IdentifiabilityStudyReserved3532",
    "3533": "IdentifiabilityStudyReserved3533",
    "3534": "IdentifiabilityStudyReserved3534",
    "3535": "IdentifiabilityStudyReserved3535",
    "3536": "IdentifiabilityStudyReserved3536",
    "3537": "IdentifiabilityStudyReserved3537",
    "3538": "IdentifiabilityStudyReserved3538",
    "3539": "IdentifiabilityStudyReserved3539",
    "3540": "IdentifiabilityStudyReserved3540",
    "3541": "V8WheelEvent_DeltaMode_AttributeGetter",
    "3542": "V8Touch_Force_AttributeGetter",
    "3543": "WebGLRenderingContextMakeXRCompatible",
    "3544": "V8WebGLCompressedTextureASTC_GetSupportedProfiles_Method",
    "3545": "HTMLCanvasGetContext",
    "3546": "V8BeforeInstallPromptEvent_Platforms_AttributeGetter",
    "3547": "IdentifiabilityStudyReserved3547",
    "3548": "IdentifiabilityStudyReserved3548",
    "3549": "IdentifiabilityStudyReserved3549",
    "3550": "IdentifiabilityStudyReserved3550",
    "3551": "IdentifiabilityStudyReserved3551",
    "3552": "IdentifiabilityStudyReserved3552",
    "3553": "IdentifiabilityStudyReserved3553",
    "3554": "IdentifiabilityStudyReserved3554",
    "3555": "IdentifiabilityStudyReserved3555",
    "3556": "IdentifiabilityStudyReserved3556",
    "3557": "IdentifiabilityStudyReserved3557",
    "3558": "IdentifiabilityStudyReserved3558",
    "3559": "IdentifiabilityStudyReserved3559",
    "3560": "IdentifiabilityStudyReserved3560",
    "3561": "IdentifiabilityStudyReserved3561",
    "3562": "IdentifiabilityStudyReserved3562",
    "3563": "IdentifiabilityStudyReserved3563",
    "3564": "IdentifiabilityStudyReserved3564",
    "3565": "IdentifiabilityStudyReserved3565",
    "3566": "V8BaseAudioContext_SampleRate_AttributeGetter",
    "3567": "WindowScreenId",
    "3568": "WebGLRenderingContextGetParameter",
    "3569": "WebGLRenderingContextGetRenderbufferParameter",
    "3570": "WebGLRenderingContextGetShaderPrecisionFormat",
    "3571": "WebGL2RenderingContextGetInternalFormatParameter",
    "3572": "IdentifiabilityStudyReserved3572",
    "3573": "IdentifiabilityStudyReserved3573",
    "3574": "IdentifiabilityStudyReserved3574",
    "3575": "IdentifiabilityStudyReserved3575",
    "3576": "IdentifiabilityStudyReserved3576",
    "3577": "IdentifiabilityStudyReserved3577",
    "3578": "CascadedCSSZoomNotEqualToOne",
    "3579": "ForcedDarkMode",
    "3580": "PreferredColorSchemeDark",
    "3581": "PreferredColorSchemeDarkSetting",
    "3582": "IdentifiabilityStudyReserved3582",
    "3583": "IdentifiabilityStudyReserved3583",
    "3584": "IdentifiabilityStudyReserved3584",
    "3585": "IdentifiabilityStudyReserved3585",
    "3586": "IdentifiabilityStudyReserved3586",
    "3587": "IdentifiabilityStudyReserved3587",
    "3588": "IdentifiabilityStudyReserved3588",
    "3589": "IdentifiabilityStudyReserved3589",
    "3590": "IdentifiabilityStudyReserved3590",
    "3591": "IdentifiabilityStudyReserved3591",
    "3592": "IdentifiabilityStudyReserved3592",
    "3593": "IdentifiabilityStudyReserved3593",
    "3594": "IdentifiabilityStudyReserved3594",
    "3595": "IdentifiabilityStudyReserved3595",
    "3596": "IdentifiabilityStudyReserved3596",
    "3597": "IdentifiabilityStudyReserved3597",
    "3598": "IdentifiabilityStudyReserved3598",
    "3599": "IdentifiabilityStudyReserved3599",
    "3600": "IdentifiabilityStudyReserved3600",
    "3601": "IdentifiabilityStudyReserved3601",
    "3602": "IdentifiabilityStudyReserved3602",
    "3603": "IdentifiabilityStudyReserved3603",
    "3604": "IdentifiabilityStudyReserved3604",
    "3605": "IdentifiabilityStudyReserved3605",
    "3606": "IdentifiabilityStudyReserved3606",
    "3607": "IdentifiabilityStudyReserved3607",
    "3608": "IdentifiabilityStudyReserved3608",
    "3609": "IdentifiabilityStudyReserved3609",
    "3610": "BarcodeDetector_GetSupportedFormats",
    "3611": "IdentifiabilityStudyReserved3611",
    "3612": "IdentifiabilityStudyReserved3612",
    "3613": "IdentifiabilityStudyReserved3613",
    "3614": "IdentifiabilityStudyReserved3614",
    "3615": "IdentifiabilityStudyReserved3615",
    "3616": "IdentifiabilityStudyReserved3616",
    "3617": "IdentifiabilityStudyReserved3617",
    "3618": "IdentifiabilityStudyReserved3618",
    "3619": "IdentifiabilityStudyReserved3619",
    "3620": "IdentifiabilityStudyReserved3620",
    "3621": "IdentifiabilityStudyReserved3621",
    "3622": "IdentifiabilityStudyReserved3622",
    "3623": "IdentifiabilityStudyReserved3623",
    "3624": "IdentifiabilityStudyReserved3624",
    "3625": "IdentifiabilityStudyReserved3625",
    "3626": "IdentifiabilityStudyReserved3626",
    "3627": "IdentifiabilityStudyReserved3627",
    "3628": "IdentifiabilityStudyReserved3628",
    "3629": "IdentifiabilityStudyReserved3629",
    "3630": "IdentifiabilityStudyReserved3630",
    "3631": "IdentifiabilityStudyReserved3631",
    "3632": "IdentifiabilityStudyReserved3632",
    "3633": "IdentifiabilityStudyReserved3633",
    "3634": "IdentifiabilityStudyReserved3634",
    "3635": "IdentifiabilityStudyReserved3635",
    "3636": "IdentifiabilityStudyReserved3636",
    "3637": "IdentifiabilityStudyReserved3637",
    "3638": "IdentifiabilityStudyReserved3638",
    "3639": "IdentifiabilityStudyReserved3639",
    "3640": "IdentifiabilityStudyReserved3640",
    "3641": "IdentifiabilityStudyReserved3641",
    "3642": "IdentifiabilityStudyReserved3642",
    "3643": "IdentifiabilityStudyReserved3643",
    "3644": "IdentifiabilityStudyReserved3644",
    "3645": "IdentifiabilityStudyReserved3645",
    "3646": "IdentifiabilityStudyReserved3646",
    "3647": "IdentifiabilityStudyReserved3647",
    "3648": "IdentifiabilityStudyReserved3648",
    "3649": "IdentifiabilityStudyReserved3649",
    "3650": "IdentifiabilityStudyReserved3650",
    "3651": "IdentifiabilityStudyReserved3651",
    "3652": "IdentifiabilityStudyReserved3652",
    "3653": "IdentifiabilityStudyReserved3653",
    "3654": "IdentifiabilityStudyReserved3654",
    "3655": "IdentifiabilityStudyReserved3655",
    "3656": "IdentifiabilityStudyReserved3656",
    "3657": "IdentifiabilityStudyReserved3657",
    "3658": "IdentifiabilityStudyReserved3658",
    "3659": "IdentifiabilityStudyReserved3659",
    "3660": "IdentifiabilityStudyReserved3660",
    "3661": "IdentifiabilityStudyReserved3661",
    "3662": "IdentifiabilityStudyReserved3662",
    "3663": "IdentifiabilityStudyReserved3663",
    "3664": "IdentifiabilityStudyReserved3664",
    "3665": "IdentifiabilityStudyReserved3665",
    "3666": "IdentifiabilityStudyReserved3666",
    "3667": "IdentifiabilityStudyReserved3667",
    "3668": "IdentifiabilityStudyReserved3668",
    "3669": "IdentifiabilityStudyReserved3669",
    "3670": "IdentifiabilityStudyReserved3670",
    "3671": "IdentifiabilityStudyReserved3671",
    "3672": "IdentifiabilityStudyReserved3672",
    "3673": "IdentifiabilityStudyReserved3673",
    "3674": "IdentifiabilityStudyReserved3674",
    "3675": "IdentifiabilityStudyReserved3675",
    "3676": "IdentifiabilityStudyReserved3676",
    "3677": "IdentifiabilityStudyReserved3677",
    "3678": "IdentifiabilityStudyReserved3678",
    "3679": "IdentifiabilityStudyReserved3679",
    "3680": "IdentifiabilityStudyReserved3680",
    "3681": "IdentifiabilityStudyReserved3681",
    "3682": "UndeferrableThirdPartySubresourceRequestWithCookie",
    "3683": "XRDepthSensing",
    "3684": "XRFrameGetDepthInformation",
    "3685": "XRDepthInformationGetDepth",
    "3686": "XRDepthInformationDataAttribute",
    "3687": "InterestCohortAPI_interestCohort_Method",
    "3688": "AddressSpaceLocalEmbeddedInPrivateSecureContext",
    "3689": "AddressSpaceLocalEmbeddedInPrivateNonSecureContext",
    "3690": "AddressSpaceLocalEmbeddedInPublicSecureContext",
    "3691": "AddressSpaceLocalEmbeddedInPublicNonSecureContext",
    "3692": "AddressSpaceLocalEmbeddedInUnknownSecureContext",
    "3693": "AddressSpaceLocalEmbeddedInUnknownNonSecureContext",
    "3694": "AddressSpacePrivateEmbeddedInPublicSecureContext",
    "3695": "AddressSpacePrivateEmbeddedInPublicNonSecureContext",
    "3696": "AddressSpacePrivateEmbeddedInUnknownSecureContext",
    "3697": "AddressSpacePrivateEmbeddedInUnknownNonSecureContext",
    "3698": "ThirdPartyAccess",
    "3699": "ThirdPartyActivation",
    "3700": "ThirdPartyAccessAndActivation",
    "3701": "FullscreenAllowedByScreensChange",
    "3702": "NewLayoutOverflowDifferentBlock",
    "3703": "NewLayoutOverflowDifferentFlex",
    "3704": "NewLayoutOverflowDifferentAndAlreadyScrollsBlock",
    "3705": "NewLayoutOverflowDifferentAndAlreadyScrollsFlex",
    "3706": "UnicodeBidiPlainText",
    "3707": "ColorSchemeDarkSupportedOnRoot",
    "3708": "WebBluetoothGetAvailability",
    "3709": "DigitalGoodsListPurchases",
    "3710": "CompositedSVG",
    "3711": "BarcodeDetectorDetect",
    "3712": "FaceDetectorDetect",
    "3713": "TextDetectorDetect",
    "3714": "LocalStorageFirstUsedBeforeFcp",
    "3715": "LocalStorageFirstUsedAfterFcp",
    "3716": "CSSPseudoHostCompoundList",
    "3717": "CSSPseudoHostContextCompoundList",
    "3718": "CSSPseudoHostDynamicSpecificity",
    "3719": "GetCurrentBrowsingContextMedia",
    "3720": "MouseEventRelativePositionForInlineElement",
    "3721": "V8SharedArrayBufferConstructedWithoutIsolation",
    "3722": "V8HTMLVideoElement_GetVideoPlaybackQuality_Method",
    "3723": "XRWebGLBindingGetReflectionCubeMap",
    "3724": "XRFrameGetLightEstimate",
    "3725": "V8HTMLDialogElement_Show_Method",
    "3726": "V8HTMLDialogElement_ShowModal_Method",
    "3727": "AdFrameDetected",
    "3728": "MediaStreamTrackGenerator",
    "3729": "MediaStreamTrackProcessor",
    "3730": "AddEventListenerWithAbortSignal",
    "3731": "XRSessionRequestLightProbe",
    "3732": "BeforematchRevealedHiddenMatchable",
    "3733": "AddSourceBufferUsingConfig",
    "3734": "ChangeTypeUsingConfig",
    "3735": "V8SourceBuffer_AppendEncodedChunks_Method",
    "3736": "OversrollBehaviorOnViewportBreaks",
    "3737": "SameOriginJsonTypeForScript",
    "3738": "CrossOriginJsonTypeForScript",
    "3739": "SameOriginStrictNosniffWouldBlock",
    "3740": "CrossOriginStrictNosniffWouldBlock",
    "3741": "CSSSelectorPseudoDir",
    "3742": "CrossOriginSubframeWithoutEmbeddingControl",
    "3743": "ReadableStreamWithByteSource",
    "3744": "ReadableStreamBYOBReader",
    "3746": "SamePartyCookieAttribute",
    "3747": "SamePartyCookieExclusionOverruledSameSite",
    "3748": "SamePartyCookieInclusionOverruledSameSite",
    "3749": "EmbedElementWithoutTypeSrcChanged",
    "3750": "PaymentHandlerStandardizedPaymentMethodIdentifier",
    "3751": "WebCodecsAudioEncoder",
    "3752": "EmbeddedCrossOriginFrameWithoutFrameAncestorsOrXFO",
    "3753": "AddressSpacePrivateSecureContextEmbeddedLocal",
    "3754": "AddressSpacePrivateNonSecureContextEmbeddedLocal",
    "3755": "AddressSpacePublicSecureContextEmbeddedLocal",
    "3756": "AddressSpacePublicNonSecureContextEmbeddedLocal",
    "3757": "AddressSpacePublicSecureContextEmbeddedPrivate",
    "3758": "AddressSpacePublicNonSecureContextEmbeddedPrivate",
    "3759": "AddressSpaceUnknownSecureContextEmbeddedLocal",
    "3760": "AddressSpaceUnknownNonSecureContextEmbeddedLocal",
    "3761": "AddressSpaceUnknownSecureContextEmbeddedPrivate",
    "3762": "AddressSpaceUnknownNonSecureContextEmbeddedPrivate",
    "3763": "AddressSpacePrivateSecureContextNavigatedToLocal",
    "3764": "AddressSpacePrivateNonSecureContextNavigatedToLocal",
    "3765": "AddressSpacePublicSecureContextNavigatedToLocal",
    "3766": "AddressSpacePublicNonSecureContextNavigatedToLocal",
    "3767": "AddressSpacePublicSecureContextNavigatedToPrivate",
    "3768": "AddressSpacePublicNonSecureContextNavigatedToPrivate",
    "3769": "AddressSpaceUnknownSecureContextNavigatedToLocal",
    "3770": "AddressSpaceUnknownNonSecureContextNavigatedToLocal",
    "3771": "AddressSpaceUnknownSecureContextNavigatedToPrivate",
    "3772": "AddressSpaceUnknownNonSecureContextNavigatedToPrivate",
    "3773": "RTCPeerConnectionSdpSemanticsPlanB",
    "3774": "FetchRespondWithNoResponseWithUsedRequestBody",
    "3775": "V8TCPSocket_Close_Method",
    "3776": "V8TCPSocket_Readable_AttributeGetter",
    "3777": "V8TCPSocket_Writable_AttributeGetter",
    "3778": "V8TCPSocket_RemoteAddress_AttributeGetter",
    "3779": "V8TCPSocket_RemotePort_AttributeGetter",
    "3780": "CSSSelectorTargetText",
    "3781": "PopupElement",
    "3782": "V8HTMLPopupElement_Show_Method",
    "3783": "V8HTMLPopupElement_Hide_Method",
    "3784": "WindowOpenWithAdditionalBoolParameter",
    "3785": "RTCPeerConnectionConstructedWithPlanB",
    "3786": "RTCPeerConnectionConstructedWithUnifiedPlan",
    "3787": "RTCPeerConnectionUsingComplexPlanB",
    "3788": "RTCPeerConnectionUsingComplexUnifiedPlan",
    "3789": "WindowScreenIsExtended",
    "3790": "WindowScreenChange",
    "3791": "XRWebGLDepthInformationTextureAttribute",
    "3792": "XRWebGLBindingGetDepthInformation",
    "3793": "SessionStorageFirstUsedBeforeFcp",
    "3794": "SessionStorageFirstUsedAfterFcp",
    "3795": "GravitySensorConstructor",
    "3796": "ElementInternalsStates",
    "3797": "WebPImage",
    "3798": "AVIFImage",
    "3799": "SVGTextEdited",
    "3800": "V8WasmExceptionHandling",
    "3801": "WasmModuleSharing",
    "3802": "CrossOriginWasmModuleSharing",
    "3803": "OverflowClipAlongEitherAxis",
    "3804": "CreateJSONModuleScript",
    "3805": "CreateCSSModuleScript",
    "3806": "InsertHTMLCommandOnInput",
    "3807": "InsertHTMLCommandOnTextarea",
    "3808": "InsertHTMLCommandOnReadWritePlainText",
    "3809": "CSSAtRuleCounterStyle",
    "3810": "CanvasUseColorSpace",
    "3811": "SelectMenuElement",
    "3812": "RTCPeerConnectionSdpSemanticsPlanBWithReverseOriginTrial",
    "3813": "WebAppManifestCaptureLinks",
    "3814": "SanitizerAPICreated",
    "3815": "SanitizerAPIDefaultConfiguration",
    "3816": "SanitizerAPIToString",
    "3817": "SanitizerAPIToFragment",
    "3818": "SanitizerAPIActionTaken",
    "3819": "SanitizerAPIFromString",
    "3820": "SanitizerAPIFromDocument",
    "3821": "SanitizerAPIFromFragment",
    "3822": "StorageFoundationOpen",
    "3823": "StorageFoundationRead",
    "3824": "StorageFoundationReadSync",
    "3825": "StorageFoundationWrite",
    "3826": "StorageFoundationWriteSync",
    "3827": "StorageFoundationFlush",
    "3828": "StorageFoundationFlushSync",
    "3829": "UnrestrictedSharedArrayBuffer",
    "3830": "FeaturePolicyJSAPIAllowsFeatureIFrame",
    "3831": "FeaturePolicyJSAPIAllowsFeatureDocument",
    "3832": "FeaturePolicyJSAPIAllowsFeatureOriginIFrame",
    "3833": "FeaturePolicyJSAPIAllowsFeatureOriginDocument",
    "3834": "FeaturePolicyJSAPIAllowedFeaturesIFrame",
    "3835": "FeaturePolicyJSAPIAllowedFeaturesDocument",
    "3836": "FeaturePolicyJSAPIFeaturesIFrame",
    "3837": "FeaturePolicyJSAPIFeaturesDocument",
    "3838": "FeaturePolicyJSAPIGetAllowlistIFrame",
    "3839": "FeaturePolicyJSAPIGetAllowlistDocument",
    "3840": "V8Screens_Onchange_AttributeGetter",
    "3841": "V8Screens_Onchange_AttributeSetter",
    "3842": "V8ScreenAdvanced_Left_AttributeGetter",
    "3843": "V8ScreenAdvanced_Top_AttributeGetter",
    "3844": "V8ScreenAdvanced_IsPrimary_AttributeGetter",
    "3845": "V8ScreenAdvanced_IsInternal_AttributeGetter",
    "3846": "V8ScreenAdvanced_DevicePixelRatio_AttributeGetter",
    "3847": "V8ScreenAdvanced_Id_AttributeGetter",
    "3848": "V8ScreenAdvanced_PointerTypes_AttributeGetter",
    "3849": "V8ScreenAdvanced_Label_AttributeGetter",
    "3850": "PermissionsPolicyHeader",
    "3851": "WebAppManifestUrlHandlers",
    "3852": "LaxAllowingUnsafeCookies",
    "3853": "V8MediaSession_SetMicrophoneActive_Method",
    "3854": "V8MediaSession_SetCameraActive_Method",
    "3855": "V8Navigator_JoinAdInterestGroup_Method",
    "3856": "V8Navigator_LeaveAdInterestGroup_Method",
    "3857": "V8Navigator_RunAdAuction_Method",
    "3858": "XHRJSONEncodingDetection",
    "3859": "WorkerControlledByServiceWorkerOutOfScope",
    "3860": "XRPlaneDetection",
    "3861": "XRFrameDetectedPlanes",
    "3862": "XRImageTracking",
    "3863": "XRSessionGetTrackedImageScores",
    "3864": "XRFrameGetImageTrackingResults",
    "3865": "OpenWebDatabaseThirdPartyContext",
    "3866": "PointerId",
    "3867": "Transform3dScene",
    "3868": "PrefersColorSchemeMediaFeature",
    "3869": "PrefersContrastMediaFeature",
    "3870": "ForcedColorsMediaFeature",
    "3871": "PaymentRequestCSPViolation",
    "3872": "WorkerControlledByServiceWorkerWithFetchEventHandlerOutOfScope",
    "3873": "AuthorizationCoveredByWildcard",
    "3874": "ElementGetInnerHTML",
    "3875": "FileHandlingLaunch",
    "3876": "SameOriginDocumentsWithDifferentCOOPStatus",
    "3877": "HTMLMediaElementSetSinkId",
    "3878": "PrefixedStorageQuotaThirdPartyContext",
    "3879": "RequestedFileSystemPersistentThirdPartyContext",
    "3880": "PrefixedStorageInfoThirdPartyContext",
    "3881": "CrossOriginEmbedderPolicyCredentialless",
    "3882": "PostMessageFromSecureToSecure",
    "3883": "PostMessageFromInsecureToInsecure",
    "3884": "WebAppManifestProtocolHandlers",
    "3885": "RTCPeerConnectionOfferAllowExtmapMixedFalse",
    "3886": "NewCanvas2DAPI",
    "3887": "ServiceWorkerSubresourceFilter",
    "3888": "WebGPU",
    "3889": "CSSFilterColorMatrix",
    "3890": "HTMLFencedFrameElement",
    "3891": "CSSFilterLuminanceToAlpha",
    "3892": "HandwritingRecognitionCreateRecognizer",
    "3893": "HandwritingRecognitionQuerySupport",
    "3894": "HandwritingRecognitionStartDrawing",
    "3895": "HandwritingRecognitionGetPrediction",
    "3896": "WebBluetoothManufacturerDataFilter",
    "3897": "SanitizerAPIGetConfig",
    "3898": "SanitizerAPIGetDefaultConfig",
    "3899": "ComputePressureObserver_Constructor",
    "3900": "ComputePressureObserver_Observe",
    "3901": "ComputePressureObserver_Stop",
    "3902": "WebAppWindowControlsOverlay",
    "3903": "PaymentRequestShowWithoutGestureOrToken",
    "3904": "V8Navigator_UpdateAdInterestGroups_Method",
    "3905": "V8Screens_Onscreenschange_AttributeGetter",
    "3906": "V8Screens_Onscreenschange_AttributeSetter",
    "3907": "V8Screens_Oncurrentscreenchange_AttributeGetter",
    "3908": "V8Screens_Oncurrentscreenchange_AttributeSetter",
    "3909": "RTCOfferAnswerOptionsVoiceActivityDetection",
    "3910": "MultiColAndListItem",
    "3911": "CaptureHandle",
    "3912": "SVGText",
    "3913": "GetBBoxForText",
    "3914": "SVGTextHangingFromPath",
    "3915": "ClientHintsPrefersColorScheme",
    "3916": "OverscrollBehaviorWillBeFixed",
    "3917": "ControlledWorkerWillBeUncontrolled",
    "3918": "ARIATouchpassthroughAttribute",
    "3919": "ARIAVirtualcontentAttribute",
    "3920": "AccessibilityTouchPassthroughSet",
    "3921": "TextFragmentBlockedByForceLoadAtTop",
    "3922": "UrnDocumentAccessedCookies",
    "3923": "FontFaceAscentOverride",
    "3924": "FontFaceDescentOverride",
    "3925": "FontFaceLineGapOverride",
    "3926": "FontFaceSizeAdjust",
    "3927": "HiddenBackfaceWith3D",
    "3928": "MainFrameNonSecurePrivateAddressSpace",
    "3929": "CSSSelectorPseudoHas",
    "3930": "HTMLMediaElementControlsListNoPlaybackRate",
    "3931": "DocumentTransition",
    "3932": "SpeculationRules",
    "3933": "V8AbortSignal_Abort_Method",
    "3934": "SelectionBackgroundColorInversion",
    "3935": "RTCPeerConnectionPlanBThrewAnException",
    "3936": "HTMLRootContained",
    "3937": "HTMLBodyContained",
    "3938": "XRFrameGetJointPose",
    "3939": "XRFrameFillJointRadii",
    "3940": "XRFrameFillPoses",
    "3941": "WindowOpenNewPopupBehaviorMismatch",
    "3942": "ExplicitPointerCaptureClickTargetDiff",
    "3943": "ControlledNonBlobURLWorkerWillBeUncontrolled",
    "3944": "MediaMetaThemeColor",
    "3945": "ClientHintsUABitness",
    "3946": "DifferentPerspectiveCBOrParent",
    "3947": "WebkitImageSet",
    "3948": "RTCPeerConnectionWithBlockingCsp",
    "3949": "SanitizerAPISanitizeFor",
    "3950": "SanitizerAPIElementSetSanitized",
    "3951": "TextShadowInHighlightPseudo",
    "3952": "TextShadowNotNoneInHighlightPseudo",
    "3953": "SameSiteNoneRequired",
    "3954": "SameSiteNoneIncludedBySamePartyTopResource",
    "3955": "SameSiteNoneIncludedBySamePartyAncestors",
    "3956": "SameSiteNoneIncludedBySameSiteLax",
    "3957": "SameSiteNoneIncludedBySameSiteStrict",
    "3958": "PrivateNetworkAccessNonSecureContextsAllowedDeprecationTrial",
    "3959": "V8URLPattern_Constructor",
    "3960": "V8URLPattern_Test_Method",
    "3961": "V8URLPattern_Exec_Method",
    "3962": "SameSiteCookieInclusionChangedByCrossSiteRedirect",
    "3963": "BlobStoreAccessAcrossAgentClustersInResolveAsURLLoaderFactory",
    "3964": "BlobStoreAccessAcrossAgentClustersInResolveForNavigation",
    "3965": "TapDelayEnabled",
    "3966": "V8URLPattern_CompareComponent_Method",
    "3967": "EarlyHintsPreload",
    "3968": "ClientHintsUAReduced",
    "3969": "SpeculationRulesPrerender",
    "3970": "ExecCommandWithTrustedTypes",
    "3971": "CSSSelectorPseudoHasInSnapshotProfile",
    "3972": "CSSSelectorPseudoHasInLiveProfile",
    "3973": "NavigatorPdfViewerEnabled",
    "3974": "CanvasRenderingContext2DContextLostEvent",
    "3975": "CanvasRenderingContext2DContextRestoredEvent",
    "3976": "ClientHintsViewportHeight",
    "3977": "V8NavigatorManagedData_GetDirectoryId_Method",
    "3978": "V8NavigatorManagedData_GetHostname_Method",
    "3979": "V8NavigatorManagedData_GetSerialNumber_Method",
    "3980": "V8NavigatorManagedData_GetAnnotatedAssetId_Method",
    "3981": "V8NavigatorManagedData_GetAnnotatedLocation_Method",
    "3982": "UserDataFieldFilledPreviously",
    "3983": "TableCollapsedBorderDifferentToVisual",
    "3984": "HighlightAPIRegisterHighlight",
    "3985": "ReadOrWriteWebDatabaseThirdPartyContext",
    "3986": "FontSelectorCSSFontFamilyWebKitPrefixPictograph",
    "3987": "FontSelectorCSSFontFamilyWebKitPrefixStandard",
    "3988": "FontSelectorCSSFontFamilyWebKitPrefixBody",
    "3989": "FontBuilderCSSFontFamilyWebKitPrefixBody",
    "3990": "CapabilityDelegationOfPaymentRequest",
    "3992": "CredentialManagerGetFederatedCredential",
    "3993": "CredentialManagerGetPasswordCredential",
    "3994": "CredentialManagerStoreFederatedCredential",
    "3995": "CredentialManagerStorePasswordCredential",
    "3996": "CredentialManagerCreateFederatedCredential",
    "3997": "CredentialManagerCreatePasswordCredential",
    "3998": "CanvasRenderingContext2DRoundRect",
    "3999": "NewLayoutOverflowDifferentBlockWithNonEmptyInflowBounds",
    "4000": "CanvasRenderingContext2DReset",
    "4001": "CanvasRenderingContext2DLetterSpacing",
    "4002": "CanvasRenderingContext2DWordSpacing",
    "4003": "CanvasRenderingContext2DFontVariantCaps",
    "4004": "CanvasRenderingContext2DFontKerning",
    "4005": "CanvasRenderingContext2DFontStretch",
    "4006": "CanvasRenderingContext2DTextRendering",
    "4007": "CSSCascadeLayers",
    "4008": "CanvasRenderingContext2DConicGradient",
    "4009": "CanvasRenderingContext2DCanvasFilter",
    "4010": "HTMLParamElementURLParameter",
    "4011": "V8HTMLScriptElement_Supports_Method",
    "4012": "HandwritingRecognitionQueryRecognizer",
    "4013": "V8FileSystemFileHandle_CreateSyncAccessHandle_Method",
    "4014": "V8FileSystemSyncAccessHandle_Read_Method",
    "4015": "V8FileSystemSyncAccessHandle_Write_Method",
    "4016": "V8FileSystemSyncAccessHandle_Close_Method",
    "4017": "V8FileSystemSyncAccessHandle_Flush_Method",
    "4018": "V8FileSystemSyncAccessHandle_GetSize_Method",
    "4019": "V8FileSystemSyncAccessHandle_Truncate_Method",
    "4020": "V8SharedArrayBufferConstructedInExtensionWithoutIsolation",
    "4021": "MediaSourceExtensionsForWebCodecs",
    "4023": "PaymentRequestResponse",
    "4024": "PaymentRequestComplete",
    "4025": "HTMLCanvasElement_2D",
    "4026": "HTMLCanvasElement_WebGL",
    "4027": "HTMLCanvasElement_WebGL2",
    "4028": "HTMLCanvasElement_BitmapRenderer",
    "4029": "HTMLCanvasElement_WebGPU",
    "4030": "OffscreenCanvas_2D",
    "4031": "OffscreenCanvas_WebGL",
    "4032": "OffscreenCanvas_WebGL2",
    "4033": "OffscreenCanvas_BitmapRenderer",
    "4034": "OffscreenCanvas_WebGPU",
    "4035": "CanvasRenderingContext2DHasOverdraw",
    "4036": "DigitalGoodsConsume",
    "4037": "DigitalGoodsListPurchaseHistory",
    "4038": "WebShareContainingFiles",
    "4039": "WebShareContainingTitle",
    "4040": "WebShareContainingText",
    "4041": "WebShareContainingUrl",
    "4042": "CoepNoneSharedWorker",
    "4043": "CoepRequireCorpSharedWorker",
    "4044": "CoepCredentiallessSharedWorker",
    "4045": "PaymentRequestBasicCard",
    "4046": "ClientHintsDeviceMemory",
    "4047": "ClientHintsDPR",
    "4048": "ClientHintsResourceWidth",
    "4049": "ClientHintsViewportWidth",
    "4050": "InlineBoxIgnoringContinuation",
    "4051": "OffsetWidthOrHeightIgnoringContinuation",
    "4052": "ConditionalFocus",
    "4053": "V8Navigator_CreateAdRequest_Method",
    "4054": "V8Navigator_FinalizeAd_Method",
    "4055": "RegionCapture",
    "4056": "AppHistory",
    "4057": "FlexboxAlignSingleLineDifference",
    "4058": "ExternalProtocolBlockedBySandbox",
    "4059": "WebAssemblyDynamicTiering",
    "4061": "ReadOrWriteWebDatabase",
    "4062": "AutoDarkMode",
    "4063": "HttpRefreshWhenScriptingDisabled",
    "4064": "V8FragmentDirective_Items_AttributeGetter",
    "4065": "V8FragmentDirective_CreateSelectorDirective_Method",
    "4066": "CSSTransitionBlockedByAnimation",
    "4067": "WebAppManifestHasComments",
    "4068": "AutoExpandedDetailsForFindInPage",
    "4069": "AutoExpandedDetailsForScrollToTextFragment",
    "4070": "WebCodecsVideoFrameDefaultTimestamp",
    "4071": "WebCodecsVideoFrameFromImage",
    "4072": "WebCodecsVideoFrameFromBuffer",
    "4073": "OpenWebDatabaseInsecureContext",
    "4074": "ScriptWebBundle",
    "4075": "RunAdAuction",
    "4076": "JoinAdInterestGroup",
    "4077": "FileSystemUrlNavigation",
    "4078": "V8Navigator_AdAuctionComponents_Method",
    "4079": "ClientHintsUAFullVersionList",
    "4080": "WebAppManifestLaunchHandler",
    "4081": "ClientHintsMetaNameAcceptCH",
    "4082": "CSSMatchMediaUnknown",
    "4083": "CSSMediaListUnknown",
    "4084": "CSSOMMediaConditionUnknown",
    "4085": "DocumentDomainSettingWithoutOriginAgentClusterHeader",
    "4086": "CrossOriginEmbedderPolicyCredentiallessReportOnly",
    "4087": "CrossOriginEmbedderPolicyRequireCorpReportOnly",
    "4088": "CoopAndCoepIsolatedReportOnly",
    "4089": "CrossOriginOpenerPolicySameOriginAllowPopupsReportOnly",
    "4090": "CrossOriginOpenerPolicySameOriginReportOnly",
    "4091": "ImageLoadAtDismissalEvent",
    "4092": "PrivateNetworkAccessIgnoredPreflightError",
    "4093": "AbortPaymentRespondWithTrue",
    "4094": "AllowPaymentRequestAttributeHasEffect",
    "4095": "V8PaymentResponse_Retry_Method",
    "4096": "WebAppManifestUserPreferences",
    "4097": "V8HTMLInputElement_ShowPicker_Method",
    "4098": "LayerXYWithMediaTarget",
    "4099": "LayerXYWithCanvasTarget",
    "4100": "LayerXYWithFrameTarget",
    "4101": "LayerXYWithSVGTarget",
    "4102": "HTMLObjectElementFallback",
    "4103": "SecureContextIncorrectForWorker",
    "4104": "V8UDPSocket_Close_Method",
    "4105": "HTMLInputElementSimulatedClick",
    "4106": "RTCLocalSdpModificationIceUfragPwd",
    "4107": "WebNfcNdefMakeReadOnly",
    "4108": "V8Navigator_DeprecatedURNToURL_Method",
    "4109": "WebAppManifestHandleLinks",
    "4110": "HTMLParamElementURLParameterInUsePdf",
    "4111": "HTMLParamElementURLParameterInUseNonPdf",
    "4112": "WebTransportServerCertificateHashes",
    "4113": "HiddenAttribute",
    "4114": "HiddenUntilFoundAttribute",
    "4115": "WindowProxyCrossOriginAccessBlur",
    "4116": "WindowProxyCrossOriginAccessClose",
    "4117": "WindowProxyCrossOriginAccessClosed",
    "4118": "WindowProxyCrossOriginAccessFocus",
    "4119": "WindowProxyCrossOriginAccessFrames",
    "4120": "WindowProxyCrossOriginAccessIndexedGetter",
    "4121": "WindowProxyCrossOriginAccessLength",
    "4122": "WindowProxyCrossOriginAccessLocation",
    "4123": "WindowProxyCrossOriginAccessNamedGetter",
    "4124": "WindowProxyCrossOriginAccessOpener",
    "4125": "WindowProxyCrossOriginAccessParent",
    "4126": "WindowProxyCrossOriginAccessPostMessage",
    "4127": "WindowProxyCrossOriginAccessSelf",
    "4128": "WindowProxyCrossOriginAccessTop",
    "4129": "WindowProxyCrossOriginAccessWindow",
    "4130": "WindowProxyCrossOriginAccessFromOtherPageBlur",
    "4131": "WindowProxyCrossOriginAccessFromOtherPageClose",
    "4132": "WindowProxyCrossOriginAccessFromOtherPageClosed",
    "4133": "WindowProxyCrossOriginAccessFromOtherPageFocus",
    "4134": "WindowProxyCrossOriginAccessFromOtherPageFrames",
    "4135": "WindowProxyCrossOriginAccessFromOtherPageIndexedGetter",
    "4136": "WindowProxyCrossOriginAccessFromOtherPageLength",
    "4137": "WindowProxyCrossOriginAccessFromOtherPageLocation",
    "4138": "WindowProxyCrossOriginAccessFromOtherPageNamedGetter",
    "4139": "WindowProxyCrossOriginAccessFromOtherPageOpener",
    "4140": "WindowProxyCrossOriginAccessFromOtherPageParent",
    "4141": "WindowProxyCrossOriginAccessFromOtherPagePostMessage",
    "4142": "WindowProxyCrossOriginAccessFromOtherPageSelf",
    "4143": "WindowProxyCrossOriginAccessFromOtherPageTop",
    "4144": "WindowProxyCrossOriginAccessFromOtherPageWindow",
    "4145": "PrivateNetworkAccessFetchedWorkerScript",
    "4146": "FrameNameContainsBrace",
    "4147": "FrameNameContainsNewline",
    "4148": "AbortSignalThrowIfAborted",
    "4149": "ClientHintsUAFull",
    "4150": "PrivateNetworkAccessWithinWorker",
    "4151": "ClientHintsUAWoW64",
    "4152": "FetchSetCookieInRequestGuardedHeaders",
    "4153": "V8Window_RequestPictureInPictureWindow_Method",
    "4154": "V8UDPSocket_LocalPort_AttributeGetter",
    "4155": "V8UDPSocket_Readable_AttributeGetter",
    "4156": "V8UDPSocket_RemoteAddress_AttributeGetter",
    "4157": "V8UDPSocket_RemotePort_AttributeGetter",
    "4158": "V8UDPSocket_Writable_AttributeGetter",
    "4159": "AbortSignalTimeout",
    "4160": "ClientHintsPartitionedCookies",
    "4161": "V8Document_Prerendering_AttributeGetter",
    "4162": "V8Document_Onprerenderingchange_AttributeGetter",
    "4163": "V8Document_Onprerenderingchange_AttributeSetter",
    "4164": "CSSAtRuleFontPaletteValues",
    "4165": "CSSAtRuleContainer",
    "4166": "FederatedCredentialManagement",
    "4167": "FetchEventSourceLastEventIdCorsUnSafe",
    "4168": "WrongBaselineOfMultiLineButtonWithNonSpace",
    "4169": "BlobStoreAccessAcrossTopLevelSite",
    "4170": "BlobStoreAccessUnknownTopLevelSite",
    "4171": "CrossOriginAccessBasedOnDocumentDomain",
    "4172": "CookieWithTruncatingChar",
    "4173": "VideoTrackGenerator",
    "4174": "MediaCapabilitiesDecodingInfoWebrtc",
    "4175": "MediaCapabilitiesEncodingInfoWebrtc",
    "4176": "UsbDeviceForget",
    "4177": "PartitionedCookies",
    "4178": "SecureContextIncorrectForSharedWorker",
    "4179": "V8FunctionPrototypeArguments",
    "4180": "V8FunctionPrototypeCaller",
    "4181": "BluetoothDeviceForget",
    "4182": "TopicsAPI_BrowsingTopics_Method",
    "4183": "BlockingAttributeRenderToken",
    "4184": "ComputePressureObserver_Unobserve",
    "4185": "ComputePressureObserver_Disconnect",
    "4186": "ComputePressureObserver_TakeRecords",
    "4187": "PrivacySandboxAdsAPIs",
    "4188": "Fledge",
    "4189": "ElementShowPopup",
    "4190": "ElementHidePopup",
    "4191": "ValidPopupAttribute",
    "4192": "DeprecationExample",
    "4193": "RTCLocalSdpModificationOpusStereo",
    "4194": "NavigatorUAData_Mobile",
    "4195": "NavigatorUAData_Platform",
    "4196": "NavigatorUAData_Brands",
    "4197": "OldConstraintsParsed",
    "4198": "OldConstraintNotReported",
    "4199": "OldConstraintRejected",
    "4200": "OldConstraintIgnored",
    "4201": "ExplicitOverflowVisibleOnReplacedElement",
    "4202": "ExplicitOverflowVisibleOnReplacedElementWithObjectProp",
    "4203": "PrivateNetworkAccessNullIpAddress",
    "4204": "OBSOLETE_LegacyConstraintGoogScreencastMinBitrate",
    "4205": "OBSOLETE_RTCPeerConnectionLegacyCreateWithMediaConstraints",
    "4206": "ClientHintsSaveData",
    "4207": "LegacyConstraintGoogIPv6",
    "4208": "OBSOLETE_LegacyConstraintGoogSuspendBelowMinBitrate",
    "4209": "OBSOLETE_LegacyConstraintGoogCpuOveruseDetection",
    "4210": "AudioContextOutputLatency",
    "4211": "V8Window_QueryLocalFonts_Method",
    "4212": "CSSAtRuleScope",
    "4213": "DeferredShapingDisabledByPositioned",
    "4214": "CapabilityDelegationOfFullscreenRequest",
    "4215": "SerialPortForget",
    "4216": "CookieHasNotBeenRefreshedIn201To300Days",
    "4217": "CookieHasNotBeenRefreshedIn301To350Days",
    "4218": "CookieHasNotBeenRefreshedIn351To400Days",
    "4219": "AnonymousIframe",
    "4220": "GestureScrollStart",
    "4221": "GestureScrollUpdate",
    "4222": "GestureScrollEnd",
    "4223": "ArrayBufferTooBigForWebAPI",
    "4224": "FedCmRevoke",
    "4225": "FedCmLogout",
    "4226": "FedCmLogoutRps",
    "4227": "V8Navigator_DeprecatedReplaceInURN_Method",
    "4228": "WebAppBorderless",
    "4229": "PaymentInstruments",
    "4230": "V8PaymentInstruments_Clear_Method",
    "4231": "V8PaymentInstruments_Delete_Method",
    "4232": "V8PaymentInstruments_Get_Method",
    "4233": "V8PaymentInstruments_Has_Method",
    "4234": "V8PaymentInstruments_Keys_Method",
    "4235": "V8PaymentInstruments_Set_Method",
    "4236": "PerformanceMeasureFindExistingName",
    "4237": "FlexboxNewAbsPos",
    "4238": "ScriptSchedulingType_Defer",
    "4239": "ScriptSchedulingType_ParserBlocking",
    "4240": "ScriptSchedulingType_ParserBlockingInline",
    "4241": "ScriptSchedulingType_InOrder",
    "4242": "ScriptSchedulingType_Async",
    "4243": "Focusgroup",
    "4244": "V8HTMLElement_Focusgroup_AttributeGetter",
    "4245": "V8HTMLElement_Focusgroup_AttributeSetter",
    "4246": "V8MathMLElement_Focusgroup_AttributeGetter",
    "4247": "V8MathMLElement_Focusgroup_AttributeSetter",
    "4248": "V8SVGElement_Focusgroup_AttributeGetter",
    "4249": "V8SVGElement_Focusgroup_AttributeSetter",
    "4250": "CSSLegacyPerspectiveOrigin",
    "4251": "CSSLegacyTransformOrigin",
    "4252": "CSSLegacyBorderImage",
    "4253": "CSSLegacyBorderImageWidth",
    "4254": "CrossOriginOpenerPolicyRestrictProperties",
    "4255": "CrossOriginOpenerPolicyRestrictPropertiesReportOnly",
    "4256": "EventTimingInteractionId",
    "4257": "SecurePaymentConfirmationOptOut",
    "4258": "AnyPopupAttribute",
    "4259": "DeferredShapingWorked",
    "4260": "DeferredShapingReshapedByForceLayout",
    "4261": "MediaSourceGetHandle",
    "4262": "IdentityInCanMakePaymentEvent",
    "4263": "SharedStorageAPI_SharedStorage_DOMReference",
    "4264": "SharedStorageAPI_AddModule_Method",
    "4265": "SharedStorageAPI_Set_Method",
    "4266": "SharedStorageAPI_Append_Method",
    "4267": "SharedStorageAPI_Delete_Method",
    "4268": "SharedStorageAPI_Clear_Method",
    "4269": "SharedStorageAPI_SelectURL_Method",
    "4270": "SharedStorageAPI_Run_Method",
    "4271": "ViewTimelineConstructor",
    "4272": "H1UserAgentFontSizeInSectionApplied",
    "4273": "V8PendingBeacon_Constructor",
    "4274": "V8PendingBeacon_Url_AttributeGetter",
    "4275": "V8PendingBeacon_Url_AttributeSetter",
    "4276": "V8PendingBeacon_Method_AttributeGetter",
    "4277": "V8PendingBeacon_Method_AttributeSetter",
    "4278": "V8PendingBeacon_PageHideTimeout_AttributeGetter",
    "4279": "V8PendingBeacon_PageHideTimeout_AttributeSetter",
    "4280": "V8PendingBeacon_State_AttributeGetter",
    "4281": "V8PendingBeacon_Deactivate_Method",
    "4282": "V8PendingBeacon_SetData_Method",
    "4283": "V8PendingBeacon_SendNow_Method",
    "4284": "TabSharingBarSwitchToCapturer",
    "4285": "TabSharingBarSwitchToCapturee",
    "4286": "AutomaticLazyAds",
    "4287": "AutomaticLazyEmbeds",
    "4288": "TouchActionChangedAtPointerDown",
    "4289": "DeviceOrientationPermissionRequested",
    "4290": "DeviceOrientationUsedWithoutPermissionRequest",
    "4291": "DeviceMotionPermissionRequested",
    "4292": "DeviceMotionUsedWithoutPermissionRequest",
    "4293": "PrivateNetworkAccessPermissionPrompt",
    "4294": "PseudoBeforeAfterForDateTimeInputElement",
    "4295": "OBSOLETE_kV8PendingBeacon_IsPending_AttributeGetter",
    "4296": "ParentOfDisabledFormControlRespondsToMouseEvents",
    "4297": "UnhandledExceptionCountInMainThread",
    "4298": "UnhandledExceptionCountInWorker",
    "4299": "OBSOLETE_WebCodecsImageDecoderPremultiplyAlphaDeprecation",
    "4300": "CookieDomainNonASCII",
    "4301": "ClientHintsMetaEquivDelegateCH",
    "4302": "ExpectCTHeader",
    "4303": "OBSOLETE_kNavigateEventTransitionWhile",
    "4304": "OBSOLETE_kNavigateEventRestoreScroll",
    "4305": "SendBeaconWithArrayBuffer",
    "4306": "SendBeaconWithArrayBufferView",
    "4307": "SendBeaconWithBlob",
    "4308": "SendBeaconWithFormData",
    "4309": "SendBeaconWithURLSearchParams",
    "4310": "SendBeaconWithUSVString",
    "4311": "ReplacedElementPaintedWithOverflow",
    "4312": "ImageAd",
    "4313": "LinkRelPrefetchAsDocumentSameOrigin",
    "4314": "LinkRelPrefetchAsDocumentCrossOrigin",
    "4315": "PersistentQuotaType",
    "4316": "CrossOriginScrollIntoView",
    "4317": "LinkRelCanonical",
    "4318": "CredentialManagerIsConditionalMediationAvailable",
    "4319": "V8PendingBeacon_Pending_AttributeGetter",
    "4320": "V8PendingBeacon_BackgroundTimeout_AttributeGetter",
    "4321": "V8PendingBeacon_BackgroundTimeout_AttributeSetter",
    "4322": "V8PendingBeacon_Timeout_AttributeGetter",
    "4323": "V8PendingBeacon_Timeout_AttributeSetter",
    "4324": "V8PendingGetBeacon_Constructor",
    "4325": "V8PendingGetBeacon_SetURL_Method",
    "4326": "V8PendingPostBeacon_Constructor",
    "4327": "V8PendingPostBeacon_SetData_Method",
    "4328": "ContentVisibilityAutoStateChangedHandlerRegistered",
    "4329": "ReplacedElementPaintedWithLargeOverflow",
    "4330": "FlexboxAbsPosJustifyContent",
    "4331": "MultipleFetchHandlersInServiceWorker",
    "4332": "StorageAccessAPI_requestStorageAccessForOrigin_Method",
    "4333": "PrivateAggregationApiAll",
    "4334": "PrivateAggregationApiFledge",
    "4335": "PrivateAggregationApiSharedStorage",
    "4336": "DeferredShaping2ReshapedByComputedStyle",
    "4337": "DeferredShaping2ReshapedByDomContentLoaded",
    "4338": "DeferredShaping2ReshapedByFcp",
    "4339": "DeferredShaping2DisabledByFragmentAnchor",
    "4340": "DeferredShaping2ReshapedByFocus",
    "4341": "DeferredShaping2ReshapedByGeometry",
    "4342": "DeferredShaping2ReshapedByInspector",
    "4343": "DeferredShaping2ReshapedByPrinting",
    "4344": "DeferredShaping2ReshapedByScrolling",
    "4345": "LCPCandidateImageFromOriginDirtyStyle",
    "4346": "V8TurboFanOsrCompileStarted",
    "4347": "V8Document_HasRedemptionRecord_Method",
    "4348": "DeferredShaping2ReshapedByLastResort",
    "4349": "AudioContextSinkId",
    "4350": "AudioContextSetSinkId",
    "4351": "ViewportDependentLazyLoadedImageWithSizesAttribute",
    "4352": "XRWebGLBindingGetCameraImage",
    "4353": "SelectiveInOrderScript",
    "4354": "V8AsyncStackTaggingCreateTaskCall",
    "4355": "WebkitBoxWithoutWebkitLineClamp",
    "4356": "DataUrlInSvgUse",
    "4357": "WebAuthnConditionalUiGet",
    "4358": "WebAuthnConditionalUiGetSuccess",
    "4359": "WebAuthnRkRequiredCreationSuccess",
    "4360": "DestructiveDocumentWriteAfterModuleScript",
    "4361": "CSSAtSupportsDropInvalidWhileForgivingParsing",
    "4362": "PermissionsPolicyUnload",
    "4363": "ServiceWorkerSkippedForSubresourceLoad",
    "4364": "ClientHintsPrefersReducedMotion",
    "4365": "WakeLockAcquireScreenLockWithoutActivation",
    "4366": "InteractiveWidgetOverlaysContent",
    "4367": "InteractiveWidgetResizesContent",
    "4368": "InteractiveWidgetResizesVisual",
    "4369": "SerivceWorkerFallbackMainResource",
    "4370": "GetDisplayMediaWithoutUserActivation",
    "4371": "BackForwardCacheNotRestoredReasons",
    "4372": "CSSNesting",
    "4373": "SandboxIneffectiveAllowOriginAllowScript",
    "4374": "DocumentOpenDifferentWindow",
    "4375": "DocumentOpenMutateSandbox",
    "4376": "EligibleForImageLoadingPrioritizationFix",
    "4377": "ExecutedNonTrivialJavaScriptURL",
    "4378": "StorageBucketsOpen",
    "4379": "PerformanceEntryBufferSwaps",
    "4380": "ClearPerformanceEntries",
    "4381": "ViewportDependentLazyLoadedImageWithoutSizesAttribute",
    "4382": "V8MediaStreamTrack_ApplyConstraints_Method",
    "4383": "ViewTransition",
    "4384": "ElementTogglePopover",
    "4385": "LayoutMediaInlineChildren",
    "4386": "ReduceAcceptLanguage",
    "4387": "UuidInPackageUrlNavigation",
    "4388": "CSSValueAppearanceMediaSliderRendered",
    "4389": "CSSValueAppearanceMediaSliderThumbRendered",
    "4390": "CSSValueAppearanceMediaVolumeSliderRendered",
    "4391": "CSSValueAppearanceMediaVolumeSliderThumbRendered",
    "4392": "V8PerformanceResourceTiming_DeliveryType_AttributeGetter",
    "4393": "DocumentLoaderDeliveryTypeNavigationalPrefetch",
    "4394": "SpeculationRulesHeader",
    "4395": "SpeculationRulesDocumentRules",
    "4396": "FedCmIframe",
    "4397": "V8DocumentPictureInPicture_RequestWindow_Method",
    "4398": "V8DocumentPictureInPicture_Window_AttributeGetter",
    "4399": "V8DocumentPictureInPictureEvent_Window_AttributeGetter",
    "4400": "DocumentPictureInPictureEnterEvent",
    "4401": "SoftNavigationHeuristics",
    "4402": "MathMLMathElement",
    "4403": "MathMLMathElementInDocument",
    "4404": "CSSAtRuleStylistic",
    "4405": "CSSAtRuleStyleset",
    "4406": "CSSAtRuleCharacterVariant",
    "4407": "CSSAtRuleSwash",
    "4408": "CSSAtRuleOrnaments",
    "4409": "CSSAtRuleAnnotation",
    "4410": "ServiceWorkerBypassFetchHandlerForMainResource",
    "4411": "V8Document_HasPrivateToken_Method",
    "4412": "ServiceWorkerSkippedForEmptyFetchHandler",
    "4413": "ImageSet",
    "4414": "WindowCloseHistoryLengthOne",
    "4415": "CreateNSResolverWithNonElements",
    "4416": "CSSValueAppearanceNonStandard",
    "4417": "CSSGetComputedAnimationDelayZero",
    "4418": "GetEffectTimingDelayZero",
    "4419": "Scrollend",
    "4420": "DOMWindowOpenCrossOriginIframe",
    "4421": "StreamingDeclarativeShadowDOM",
    "4422": "DialogCloseWatcherCancelSkipped",
    "4423": "DialogCloseWatcherCancelSkippedAndDefaultPrevented",
    "4424": "DialogCloseWatcherCloseSignalClosedMultiple",
    "4425": "NoVarySearch",
    "4426": "FedCmUserInfo",
    "4427": "IDNA2008DeviationCharacterInHostnameOfSubresource",
    "4428": "IDNA2008DeviationCharacterInHostnameOfIFrame",
    "4429": "WindowOpenPopupOnMobile",
    "4430": "WindowOpenedAsPopupOnMobile",
}

##########################################################################
#   CSS feature names from https://source.chromium.org/chromium/chromium/src/+/main:third_party/blink/public/mojom/use_counter/metrics/css_property_id.mojom
##########################################################################
CSS_FEATURES = {
    "2": "CSSPropertyColor",
    "3": "CSSPropertyDirection",
    "4": "CSSPropertyDisplay",
    "5": "CSSPropertyFont",
    "6": "CSSPropertyFontFamily",
    "7": "CSSPropertyFontSize",
    "8": "CSSPropertyFontStyle",
    "9": "CSSPropertyFontVariant",
    "10": "CSSPropertyFontWeight",
    "11": "CSSPropertyTextRendering",
    "12": "CSSPropertyAliasWebkitFontFeatureSettings",
    "13": "CSSPropertyFontKerning",
    "14": "CSSPropertyWebkitFontSmoothing",
    "15": "CSSPropertyFontVariantLigatures",
    "16": "CSSPropertyWebkitLocale",
    "17": "CSSPropertyWebkitTextOrientation",
    "18": "CSSPropertyWebkitWritingMode",
    "19": "CSSPropertyZoom",
    "20": "CSSPropertyLineHeight",
    "21": "CSSPropertyBackground",
    "22": "CSSPropertyBackgroundAttachment",
    "23": "CSSPropertyBackgroundClip",
    "24": "CSSPropertyBackgroundColor",
    "25": "CSSPropertyBackgroundImage",
    "26": "CSSPropertyBackgroundOrigin",
    "27": "CSSPropertyBackgroundPosition",
    "28": "CSSPropertyBackgroundPositionX",
    "29": "CSSPropertyBackgroundPositionY",
    "30": "CSSPropertyBackgroundRepeat",
    "31": "CSSPropertyBackgroundRepeatX",
    "32": "CSSPropertyBackgroundRepeatY",
    "33": "CSSPropertyBackgroundSize",
    "34": "CSSPropertyBorder",
    "35": "CSSPropertyBorderBottom",
    "36": "CSSPropertyBorderBottomColor",
    "37": "CSSPropertyBorderBottomLeftRadius",
    "38": "CSSPropertyBorderBottomRightRadius",
    "39": "CSSPropertyBorderBottomStyle",
    "40": "CSSPropertyBorderBottomWidth",
    "41": "CSSPropertyBorderCollapse",
    "42": "CSSPropertyBorderColor",
    "43": "CSSPropertyBorderImage",
    "44": "CSSPropertyBorderImageOutset",
    "45": "CSSPropertyBorderImageRepeat",
    "46": "CSSPropertyBorderImageSlice",
    "47": "CSSPropertyBorderImageSource",
    "48": "CSSPropertyBorderImageWidth",
    "49": "CSSPropertyBorderLeft",
    "50": "CSSPropertyBorderLeftColor",
    "51": "CSSPropertyBorderLeftStyle",
    "52": "CSSPropertyBorderLeftWidth",
    "53": "CSSPropertyBorderRadius",
    "54": "CSSPropertyBorderRight",
    "55": "CSSPropertyBorderRightColor",
    "56": "CSSPropertyBorderRightStyle",
    "57": "CSSPropertyBorderRightWidth",
    "58": "CSSPropertyBorderSpacing",
    "59": "CSSPropertyBorderStyle",
    "60": "CSSPropertyBorderTop",
    "61": "CSSPropertyBorderTopColor",
    "62": "CSSPropertyBorderTopLeftRadius",
    "63": "CSSPropertyBorderTopRightRadius",
    "64": "CSSPropertyBorderTopStyle",
    "65": "CSSPropertyBorderTopWidth",
    "66": "CSSPropertyBorderWidth",
    "67": "CSSPropertyBottom",
    "68": "CSSPropertyBoxShadow",
    "69": "CSSPropertyBoxSizing",
    "70": "CSSPropertyCaptionSide",
    "71": "CSSPropertyClear",
    "72": "CSSPropertyClip",
    "73": "CSSPropertyAliasWebkitClipPath",
    "74": "CSSPropertyContent",
    "75": "CSSPropertyCounterIncrement",
    "76": "CSSPropertyCounterReset",
    "77": "CSSPropertyCursor",
    "78": "CSSPropertyEmptyCells",
    "79": "CSSPropertyFloat",
    "80": "CSSPropertyFontStretch",
    "81": "CSSPropertyHeight",
    "82": "CSSPropertyImageRendering",
    "83": "CSSPropertyLeft",
    "84": "CSSPropertyLetterSpacing",
    "85": "CSSPropertyListStyle",
    "86": "CSSPropertyListStyleImage",
    "87": "CSSPropertyListStylePosition",
    "88": "CSSPropertyListStyleType",
    "89": "CSSPropertyMargin",
    "90": "CSSPropertyMarginBottom",
    "91": "CSSPropertyMarginLeft",
    "92": "CSSPropertyMarginRight",
    "93": "CSSPropertyMarginTop",
    "94": "CSSPropertyMaxHeight",
    "95": "CSSPropertyMaxWidth",
    "96": "CSSPropertyMinHeight",
    "97": "CSSPropertyMinWidth",
    "98": "CSSPropertyOpacity",
    "99": "CSSPropertyOrphans",
    "100": "CSSPropertyOutline",
    "101": "CSSPropertyOutlineColor",
    "102": "CSSPropertyOutlineOffset",
    "103": "CSSPropertyOutlineStyle",
    "104": "CSSPropertyOutlineWidth",
    "105": "CSSPropertyOverflow",
    "106": "CSSPropertyOverflowWrap",
    "107": "CSSPropertyOverflowX",
    "108": "CSSPropertyOverflowY",
    "109": "CSSPropertyPadding",
    "110": "CSSPropertyPaddingBottom",
    "111": "CSSPropertyPaddingLeft",
    "112": "CSSPropertyPaddingRight",
    "113": "CSSPropertyPaddingTop",
    "114": "CSSPropertyPage",
    "115": "CSSPropertyPageBreakAfter",
    "116": "CSSPropertyPageBreakBefore",
    "117": "CSSPropertyPageBreakInside",
    "118": "CSSPropertyPointerEvents",
    "119": "CSSPropertyPosition",
    "120": "CSSPropertyQuotes",
    "121": "CSSPropertyResize",
    "122": "CSSPropertyRight",
    "123": "CSSPropertySize",
    "124": "CSSPropertySrc",
    "125": "CSSPropertySpeak",
    "126": "CSSPropertyTableLayout",
    "127": "CSSPropertyTabSize",
    "128": "CSSPropertyTextAlign",
    "129": "CSSPropertyTextDecoration",
    "130": "CSSPropertyTextIndent",
    "136": "CSSPropertyTextOverflow",
    "142": "CSSPropertyTextShadow",
    "143": "CSSPropertyTextTransform",
    "149": "CSSPropertyTop",
    "150": "CSSPropertyTransition",
    "151": "CSSPropertyTransitionDelay",
    "152": "CSSPropertyTransitionDuration",
    "153": "CSSPropertyTransitionProperty",
    "154": "CSSPropertyTransitionTimingFunction",
    "155": "CSSPropertyUnicodeBidi",
    "156": "CSSPropertyUnicodeRange",
    "157": "CSSPropertyVerticalAlign",
    "158": "CSSPropertyVisibility",
    "159": "CSSPropertyWhiteSpace",
    "160": "CSSPropertyWidows",
    "161": "CSSPropertyWidth",
    "162": "CSSPropertyWordBreak",
    "163": "CSSPropertyWordSpacing",
    "164": "CSSPropertyWordWrap",
    "165": "CSSPropertyZIndex",
    "166": "CSSPropertyAliasWebkitAnimation",
    "167": "CSSPropertyAliasWebkitAnimationDelay",
    "168": "CSSPropertyAliasWebkitAnimationDirection",
    "169": "CSSPropertyAliasWebkitAnimationDuration",
    "170": "CSSPropertyAliasWebkitAnimationFillMode",
    "171": "CSSPropertyAliasWebkitAnimationIterationCount",
    "172": "CSSPropertyAliasWebkitAnimationName",
    "173": "CSSPropertyAliasWebkitAnimationPlayState",
    "174": "CSSPropertyAliasWebkitAnimationTimingFunction",
    "175": "CSSPropertyWebkitAppearance",
    "176": "CSSPropertyWebkitAspectRatio",
    "177": "CSSPropertyAliasWebkitBackfaceVisibility",
    "178": "CSSPropertyWebkitBackgroundClip",
    "179": "CSSPropertyWebkitBackgroundComposite",
    "180": "CSSPropertyWebkitBackgroundOrigin",
    "181": "CSSPropertyAliasWebkitBackgroundSize",
    "182": "CSSPropertyWebkitBorderAfter",
    "183": "CSSPropertyWebkitBorderAfterColor",
    "184": "CSSPropertyWebkitBorderAfterStyle",
    "185": "CSSPropertyWebkitBorderAfterWidth",
    "186": "CSSPropertyWebkitBorderBefore",
    "187": "CSSPropertyWebkitBorderBeforeColor",
    "188": "CSSPropertyWebkitBorderBeforeStyle",
    "189": "CSSPropertyWebkitBorderBeforeWidth",
    "190": "CSSPropertyWebkitBorderEnd",
    "191": "CSSPropertyWebkitBorderEndColor",
    "192": "CSSPropertyWebkitBorderEndStyle",
    "193": "CSSPropertyWebkitBorderEndWidth",
    "194": "CSSPropertyWebkitBorderFit",
    "195": "CSSPropertyWebkitBorderHorizontalSpacing",
    "196": "CSSPropertyWebkitBorderImage",
    "197": "CSSPropertyAliasWebkitBorderRadius",
    "198": "CSSPropertyWebkitBorderStart",
    "199": "CSSPropertyWebkitBorderStartColor",
    "200": "CSSPropertyWebkitBorderStartStyle",
    "201": "CSSPropertyWebkitBorderStartWidth",
    "202": "CSSPropertyWebkitBorderVerticalSpacing",
    "203": "CSSPropertyWebkitBoxAlign",
    "204": "CSSPropertyWebkitBoxDirection",
    "205": "CSSPropertyWebkitBoxFlex",
    "206": "CSSPropertyWebkitBoxFlexGroup",
    "207": "CSSPropertyWebkitBoxLines",
    "208": "CSSPropertyWebkitBoxOrdinalGroup",
    "209": "CSSPropertyWebkitBoxOrient",
    "210": "CSSPropertyWebkitBoxPack",
    "211": "CSSPropertyWebkitBoxReflect",
    "212": "CSSPropertyAliasWebkitBoxShadow",
    "215": "CSSPropertyWebkitColumnBreakAfter",
    "216": "CSSPropertyWebkitColumnBreakBefore",
    "217": "CSSPropertyWebkitColumnBreakInside",
    "218": "CSSPropertyAliasWebkitColumnCount",
    "219": "CSSPropertyAliasWebkitColumnGap",
    "220": "CSSPropertyWebkitColumnProgression",
    "221": "CSSPropertyAliasWebkitColumnRule",
    "222": "CSSPropertyAliasWebkitColumnRuleColor",
    "223": "CSSPropertyAliasWebkitColumnRuleStyle",
    "224": "CSSPropertyAliasWebkitColumnRuleWidth",
    "225": "CSSPropertyAliasWebkitColumnSpan",
    "226": "CSSPropertyAliasWebkitColumnWidth",
    "227": "CSSPropertyAliasWebkitColumns",
    "228": "CSSPropertyWebkitBoxDecorationBreak",
    "229": "CSSPropertyWebkitFilter",
    "230": "CSSPropertyAlignContent",
    "231": "CSSPropertyAlignItems",
    "232": "CSSPropertyAlignSelf",
    "233": "CSSPropertyFlex",
    "234": "CSSPropertyFlexBasis",
    "235": "CSSPropertyFlexDirection",
    "236": "CSSPropertyFlexFlow",
    "237": "CSSPropertyFlexGrow",
    "238": "CSSPropertyFlexShrink",
    "239": "CSSPropertyFlexWrap",
    "240": "CSSPropertyJustifyContent",
    "241": "CSSPropertyWebkitFontSizeDelta",
    "242": "CSSPropertyGridTemplateColumns",
    "243": "CSSPropertyGridTemplateRows",
    "244": "CSSPropertyGridColumnStart",
    "245": "CSSPropertyGridColumnEnd",
    "246": "CSSPropertyGridRowStart",
    "247": "CSSPropertyGridRowEnd",
    "248": "CSSPropertyGridColumn",
    "249": "CSSPropertyGridRow",
    "250": "CSSPropertyGridAutoFlow",
    "251": "CSSPropertyWebkitHighlight",
    "252": "CSSPropertyWebkitHyphenateCharacter",
    "257": "CSSPropertyWebkitLineBoxContain",
    "258": "CSSPropertyWebkitLineAlign",
    "259": "CSSPropertyWebkitLineBreak",
    "260": "CSSPropertyWebkitLineClamp",
    "261": "CSSPropertyWebkitLineGrid",
    "262": "CSSPropertyWebkitLineSnap",
    "263": "CSSPropertyWebkitLogicalWidth",
    "264": "CSSPropertyWebkitLogicalHeight",
    "265": "CSSPropertyWebkitMarginAfterCollapse",
    "266": "CSSPropertyWebkitMarginBeforeCollapse",
    "267": "CSSPropertyWebkitMarginBottomCollapse",
    "268": "CSSPropertyWebkitMarginTopCollapse",
    "269": "CSSPropertyWebkitMarginCollapse",
    "270": "CSSPropertyWebkitMarginAfter",
    "271": "CSSPropertyWebkitMarginBefore",
    "272": "CSSPropertyWebkitMarginEnd",
    "273": "CSSPropertyWebkitMarginStart",
    "280": "CSSPropertyWebkitMask",
    "281": "CSSPropertyWebkitMaskBoxImage",
    "282": "CSSPropertyWebkitMaskBoxImageOutset",
    "283": "CSSPropertyWebkitMaskBoxImageRepeat",
    "284": "CSSPropertyWebkitMaskBoxImageSlice",
    "285": "CSSPropertyWebkitMaskBoxImageSource",
    "286": "CSSPropertyWebkitMaskBoxImageWidth",
    "287": "CSSPropertyWebkitMaskClip",
    "288": "CSSPropertyWebkitMaskComposite",
    "289": "CSSPropertyWebkitMaskImage",
    "290": "CSSPropertyWebkitMaskOrigin",
    "291": "CSSPropertyWebkitMaskPosition",
    "292": "CSSPropertyWebkitMaskPositionX",
    "293": "CSSPropertyWebkitMaskPositionY",
    "294": "CSSPropertyWebkitMaskRepeat",
    "295": "CSSPropertyWebkitMaskRepeatX",
    "296": "CSSPropertyWebkitMaskRepeatY",
    "297": "CSSPropertyWebkitMaskSize",
    "298": "CSSPropertyWebkitMaxLogicalWidth",
    "299": "CSSPropertyWebkitMaxLogicalHeight",
    "300": "CSSPropertyWebkitMinLogicalWidth",
    "301": "CSSPropertyWebkitMinLogicalHeight",
    "303": "CSSPropertyOrder",
    "304": "CSSPropertyWebkitPaddingAfter",
    "305": "CSSPropertyWebkitPaddingBefore",
    "306": "CSSPropertyWebkitPaddingEnd",
    "307": "CSSPropertyWebkitPaddingStart",
    "308": "CSSPropertyAliasWebkitPerspective",
    "309": "CSSPropertyAliasWebkitPerspectiveOrigin",
    "310": "CSSPropertyWebkitPerspectiveOriginX",
    "311": "CSSPropertyWebkitPerspectiveOriginY",
    "312": "CSSPropertyWebkitPrintColorAdjust",
    "313": "CSSPropertyWebkitRtlOrdering",
    "314": "CSSPropertyWebkitRubyPosition",
    "315": "CSSPropertyWebkitTextCombine",
    "316": "CSSPropertyWebkitTextDecorationsInEffect",
    "317": "CSSPropertyWebkitTextEmphasis",
    "318": "CSSPropertyWebkitTextEmphasisColor",
    "319": "CSSPropertyWebkitTextEmphasisPosition",
    "320": "CSSPropertyWebkitTextEmphasisStyle",
    "321": "CSSPropertyWebkitTextFillColor",
    "322": "CSSPropertyWebkitTextSecurity",
    "323": "CSSPropertyWebkitTextStroke",
    "324": "CSSPropertyWebkitTextStrokeColor",
    "325": "CSSPropertyWebkitTextStrokeWidth",
    "326": "CSSPropertyAliasWebkitTransform",
    "327": "CSSPropertyAliasWebkitTransformOrigin",
    "328": "CSSPropertyWebkitTransformOriginX",
    "329": "CSSPropertyWebkitTransformOriginY",
    "330": "CSSPropertyWebkitTransformOriginZ",
    "331": "CSSPropertyAliasWebkitTransformStyle",
    "332": "CSSPropertyAliasWebkitTransition",
    "333": "CSSPropertyAliasWebkitTransitionDelay",
    "334": "CSSPropertyAliasWebkitTransitionDuration",
    "335": "CSSPropertyAliasWebkitTransitionProperty",
    "336": "CSSPropertyAliasWebkitTransitionTimingFunction",
    "337": "CSSPropertyWebkitUserDrag",
    "338": "CSSPropertyWebkitUserModify",
    "339": "CSSPropertyAliasWebkitUserSelect",
    "340": "CSSPropertyWebkitFlowInto",
    "341": "CSSPropertyWebkitFlowFrom",
    "342": "CSSPropertyWebkitRegionFragment",
    "343": "CSSPropertyWebkitRegionBreakAfter",
    "344": "CSSPropertyWebkitRegionBreakBefore",
    "345": "CSSPropertyWebkitRegionBreakInside",
    "346": "CSSPropertyShapeInside",
    "347": "CSSPropertyShapeOutside",
    "348": "CSSPropertyShapeMargin",
    "349": "CSSPropertyShapePadding",
    "350": "CSSPropertyWebkitWrapFlow",
    "351": "CSSPropertyWebkitWrapThrough",
    "355": "CSSPropertyClipPath",
    "356": "CSSPropertyClipRule",
    "357": "CSSPropertyMask",
    "359": "CSSPropertyFilter",
    "360": "CSSPropertyFloodColor",
    "361": "CSSPropertyFloodOpacity",
    "362": "CSSPropertyLightingColor",
    "363": "CSSPropertyStopColor",
    "364": "CSSPropertyStopOpacity",
    "365": "CSSPropertyColorInterpolation",
    "366": "CSSPropertyColorInterpolationFilters",
    "367": "CSSPropertyColorProfile",
    "368": "CSSPropertyColorRendering",
    "369": "CSSPropertyFill",
    "370": "CSSPropertyFillOpacity",
    "371": "CSSPropertyFillRule",
    "372": "CSSPropertyMarker",
    "373": "CSSPropertyMarkerEnd",
    "374": "CSSPropertyMarkerMid",
    "375": "CSSPropertyMarkerStart",
    "376": "CSSPropertyMaskType",
    "377": "CSSPropertyShapeRendering",
    "378": "CSSPropertyStroke",
    "379": "CSSPropertyStrokeDasharray",
    "380": "CSSPropertyStrokeDashoffset",
    "381": "CSSPropertyStrokeLinecap",
    "382": "CSSPropertyStrokeLinejoin",
    "383": "CSSPropertyStrokeMiterlimit",
    "384": "CSSPropertyStrokeOpacity",
    "385": "CSSPropertyStrokeWidth",
    "386": "CSSPropertyAlignmentBaseline",
    "387": "CSSPropertyBaselineShift",
    "388": "CSSPropertyDominantBaseline",
    "392": "CSSPropertyTextAnchor",
    "393": "CSSPropertyVectorEffect",
    "394": "CSSPropertyWritingMode",
    "399": "CSSPropertyWebkitBlendMode",
    "400": "CSSPropertyWebkitBackgroundBlendMode",
    "401": "CSSPropertyTextDecorationLine",
    "402": "CSSPropertyTextDecorationStyle",
    "403": "CSSPropertyTextDecorationColor",
    "404": "CSSPropertyTextAlignLast",
    "405": "CSSPropertyTextUnderlinePosition",
    "406": "CSSPropertyMaxZoom",
    "407": "CSSPropertyMinZoom",
    "408": "CSSPropertyOrientation",
    "409": "CSSPropertyUserZoom",
    "412": "CSSPropertyWebkitAppRegion",
    "413": "CSSPropertyAliasWebkitFilter",
    "414": "CSSPropertyWebkitBoxDecorationBreak",
    "415": "CSSPropertyWebkitTapHighlightColor",
    "416": "CSSPropertyBufferedRendering",
    "417": "CSSPropertyGridAutoRows",
    "418": "CSSPropertyGridAutoColumns",
    "419": "CSSPropertyBackgroundBlendMode",
    "420": "CSSPropertyMixBlendMode",
    "421": "CSSPropertyTouchAction",
    "422": "CSSPropertyGridArea",
    "423": "CSSPropertyGridTemplateAreas",
    "424": "CSSPropertyAnimation",
    "425": "CSSPropertyAnimationDelay",
    "426": "CSSPropertyAnimationDirection",
    "427": "CSSPropertyAnimationDuration",
    "428": "CSSPropertyAnimationFillMode",
    "429": "CSSPropertyAnimationIterationCount",
    "430": "CSSPropertyAnimationName",
    "431": "CSSPropertyAnimationPlayState",
    "432": "CSSPropertyAnimationTimingFunction",
    "433": "CSSPropertyObjectFit",
    "434": "CSSPropertyPaintOrder",
    "435": "CSSPropertyMaskSourceType",
    "436": "CSSPropertyIsolation",
    "437": "CSSPropertyObjectPosition",
    "438": "CSSPropertyInternalCallback",
    "439": "CSSPropertyShapeImageThreshold",
    "440": "CSSPropertyColumnFill",
    "441": "CSSPropertyTextJustify",
    "443": "CSSPropertyJustifySelf",
    "444": "CSSPropertyScrollBehavior",
    "445": "CSSPropertyWillChange",
    "446": "CSSPropertyTransform",
    "447": "CSSPropertyTransformOrigin",
    "448": "CSSPropertyTransformStyle",
    "449": "CSSPropertyPerspective",
    "450": "CSSPropertyPerspectiveOrigin",
    "451": "CSSPropertyBackfaceVisibility",
    "452": "CSSPropertyGridTemplate",
    "453": "CSSPropertyGrid",
    "454": "CSSPropertyAll",
    "455": "CSSPropertyJustifyItems",
    "457": "CSSPropertyAliasMotionPath",
    "458": "CSSPropertyAliasMotionOffset",
    "459": "CSSPropertyAliasMotionRotation",
    "460": "CSSPropertyMotion",
    "461": "CSSPropertyX",
    "462": "CSSPropertyY",
    "463": "CSSPropertyRx",
    "464": "CSSPropertyRy",
    "465": "CSSPropertyFontSizeAdjust",
    "466": "CSSPropertyCx",
    "467": "CSSPropertyCy",
    "468": "CSSPropertyR",
    "469": "CSSPropertyAliasEpubCaptionSide",
    "470": "CSSPropertyAliasEpubTextCombine",
    "471": "CSSPropertyAliasEpubTextEmphasis",
    "472": "CSSPropertyAliasEpubTextEmphasisColor",
    "473": "CSSPropertyAliasEpubTextEmphasisStyle",
    "474": "CSSPropertyAliasEpubTextOrientation",
    "475": "CSSPropertyAliasEpubTextTransform",
    "476": "CSSPropertyAliasEpubWordBreak",
    "477": "CSSPropertyAliasEpubWritingMode",
    "478": "CSSPropertyAliasWebkitAlignContent",
    "479": "CSSPropertyAliasWebkitAlignItems",
    "480": "CSSPropertyAliasWebkitAlignSelf",
    "481": "CSSPropertyAliasWebkitBorderBottomLeftRadius",
    "482": "CSSPropertyAliasWebkitBorderBottomRightRadius",
    "483": "CSSPropertyAliasWebkitBorderTopLeftRadius",
    "484": "CSSPropertyAliasWebkitBorderTopRightRadius",
    "485": "CSSPropertyAliasWebkitBoxSizing",
    "486": "CSSPropertyAliasWebkitFlex",
    "487": "CSSPropertyAliasWebkitFlexBasis",
    "488": "CSSPropertyAliasWebkitFlexDirection",
    "489": "CSSPropertyAliasWebkitFlexFlow",
    "490": "CSSPropertyAliasWebkitFlexGrow",
    "491": "CSSPropertyAliasWebkitFlexShrink",
    "492": "CSSPropertyAliasWebkitFlexWrap",
    "493": "CSSPropertyAliasWebkitJustifyContent",
    "494": "CSSPropertyAliasWebkitOpacity",
    "495": "CSSPropertyAliasWebkitOrder",
    "496": "CSSPropertyAliasWebkitShapeImageThreshold",
    "497": "CSSPropertyAliasWebkitShapeMargin",
    "498": "CSSPropertyAliasWebkitShapeOutside",
    "499": "CSSPropertyScrollSnapType",
    "500": "CSSPropertyScrollSnapPointsX",
    "501": "CSSPropertyScrollSnapPointsY",
    "502": "CSSPropertyScrollSnapCoordinate",
    "503": "CSSPropertyScrollSnapDestination",
    "504": "CSSPropertyTranslate",
    "505": "CSSPropertyRotate",
    "506": "CSSPropertyScale",
    "507": "CSSPropertyImageOrientation",
    "508": "CSSPropertyBackdropFilter",
    "509": "CSSPropertyTextCombineUpright",
    "510": "CSSPropertyTextOrientation",
    "511": "CSSPropertyGridColumnGap",
    "512": "CSSPropertyGridRowGap",
    "513": "CSSPropertyGridGap",
    "514": "CSSPropertyFontFeatureSettings",
    "515": "CSSPropertyVariable",
    "516": "CSSPropertyFontDisplay",
    "517": "CSSPropertyContain",
    "518": "CSSPropertyD",
    "519": "CSSPropertySnapHeight",
    "520": "CSSPropertyBreakAfter",
    "521": "CSSPropertyBreakBefore",
    "522": "CSSPropertyBreakInside",
    "523": "CSSPropertyColumnCount",
    "524": "CSSPropertyColumnGap",
    "525": "CSSPropertyColumnRule",
    "526": "CSSPropertyColumnRuleColor",
    "527": "CSSPropertyColumnRuleStyle",
    "528": "CSSPropertyColumnRuleWidth",
    "529": "CSSPropertyColumnSpan",
    "530": "CSSPropertyColumnWidth",
    "531": "CSSPropertyColumns",
    "532": "CSSPropertyApplyAtRule",
    "533": "CSSPropertyFontVariantCaps",
    "534": "CSSPropertyHyphens",
    "535": "CSSPropertyFontVariantNumeric",
    "536": "CSSPropertyTextSizeAdjust",
    "537": "CSSPropertyAliasWebkitTextSizeAdjust",
    "538": "CSSPropertyOverflowAnchor",
    "539": "CSSPropertyUserSelect",
    "540": "CSSPropertyOffsetDistance",
    "541": "CSSPropertyOffsetPath",
    "542": "CSSPropertyOffsetRotation",
    "543": "CSSPropertyOffset",
    "544": "CSSPropertyOffsetAnchor",
    "545": "CSSPropertyOffsetPosition",
    "546": "CSSPropertyTextDecorationSkip",
    "547": "CSSPropertyCaretColor",
    "548": "CSSPropertyOffsetRotate",
    "549": "CSSPropertyFontVariationSettings",
    "550": "CSSPropertyInlineSize",
    "551": "CSSPropertyBlockSize",
    "552": "CSSPropertyMinInlineSize",
    "553": "CSSPropertyMinBlockSize",
    "554": "CSSPropertyMaxInlineSize",
    "555": "CSSPropertyMaxBlockSize",
    "556": "CSSPropertyAliasLineBreak",
    "557": "CSSPropertyPlaceContent",
    "558": "CSSPropertyPlaceItems",
    "559": "CSSPropertyTransformBox",
    "560": "CSSPropertyPlaceSelf",
    "561": "CSSPropertyScrollSnapAlign",
    "562": "CSSPropertyScrollPadding",
    "563": "CSSPropertyScrollPaddingTop",
    "564": "CSSPropertyScrollPaddingRight",
    "565": "CSSPropertyScrollPaddingBottom",
    "566": "CSSPropertyScrollPaddingLeft",
    "567": "CSSPropertyScrollPaddingBlock",
    "568": "CSSPropertyScrollPaddingBlockStart",
    "569": "CSSPropertyScrollPaddingBlockEnd",
    "570": "CSSPropertyScrollPaddingInline",
    "571": "CSSPropertyScrollPaddingInlineStart",
    "572": "CSSPropertyScrollPaddingInlineEnd",
    "573": "CSSPropertyScrollSnapMargin",
    "574": "CSSPropertyScrollSnapMarginTop",
    "575": "CSSPropertyScrollSnapMarginRight",
    "576": "CSSPropertyScrollSnapMarginBottom",
    "577": "CSSPropertyScrollSnapMarginLeft",
    "578": "CSSPropertyScrollSnapMarginBlock",
    "579": "CSSPropertyScrollSnapMarginBlockStart",
    "580": "CSSPropertyScrollSnapMarginBlockEnd",
    "581": "CSSPropertyScrollSnapMarginInline",
    "582": "CSSPropertyScrollSnapMarginInlineStart",
    "583": "CSSPropertyScrollSnapMarginInlineEnd",
    "584": "CSSPropertyScrollSnapStop",
    "585": "CSSPropertyScrollBoundaryBehavior",
    "586": "CSSPropertyScrollBoundaryBehaviorX",
    "587": "CSSPropertyScrollBoundaryBehaviorY",
    "588": "CSSPropertyFontVariantEastAsian",
    "589": "CSSPropertyTextDecorationSkipInk",
    "590": "CSSPropertyScrollCustomization",
    "591": "CSSPropertyRowGap",
    "592": "CSSPropertyGap",
    "593": "CSSPropertyViewportFit",
    "594": "CSSPropertyMarginBlockStart",
    "595": "CSSPropertyMarginBlockEnd",
    "596": "CSSPropertyMarginInlineStart",
    "597": "CSSPropertyMarginInlineEnd",
    "598": "CSSPropertyPaddingBlockStart",
    "599": "CSSPropertyPaddingBlockEnd",
    "600": "CSSPropertyPaddingInlineStart",
    "601": "CSSPropertyPaddingInlineEnd",
    "602": "CSSPropertyBorderBlockEndColor",
    "603": "CSSPropertyBorderBlockEndStyle",
    "604": "CSSPropertyBorderBlockEndWidth",
    "605": "CSSPropertyBorderBlockStartColor",
    "606": "CSSPropertyBorderBlockStartStyle",
    "607": "CSSPropertyBorderBlockStartWidth",
    "608": "CSSPropertyBorderInlineEndColor",
    "609": "CSSPropertyBorderInlineEndStyle",
    "610": "CSSPropertyBorderInlineEndWidth",
    "611": "CSSPropertyBorderInlineStartColor",
    "612": "CSSPropertyBorderInlineStartStyle",
    "613": "CSSPropertyBorderInlineStartWidth",
    "614": "CSSPropertyBorderBlockStart",
    "615": "CSSPropertyBorderBlockEnd",
    "616": "CSSPropertyBorderInlineStart",
    "617": "CSSPropertyBorderInlineEnd",
    "618": "CSSPropertyMarginBlock",
    "619": "CSSPropertyMarginInline",
    "620": "CSSPropertyPaddingBlock",
    "621": "CSSPropertyPaddingInline",
    "622": "CSSPropertyBorderBlockColor",
    "623": "CSSPropertyBorderBlockStyle",
    "624": "CSSPropertyBorderBlockWidth",
    "625": "CSSPropertyBorderInlineColor",
    "626": "CSSPropertyBorderInlineStyle",
    "627": "CSSPropertyBorderInlineWidth",
    "628": "CSSPropertyBorderBlock",
    "629": "CSSPropertyBorderInline",
    "630": "CSSPropertyInsetBlockStart",
    "631": "CSSPropertyInsetBlockEnd",
    "632": "CSSPropertyInsetBlock",
    "633": "CSSPropertyInsetInlineStart",
    "634": "CSSPropertyInsetInlineEnd",
    "635": "CSSPropertyInsetInline",
    "636": "CSSPropertyInset",
    "637": "CSSPropertyColorScheme",
    "638": "CSSPropertyOverflowInline",
    "639": "CSSPropertyOverflowBlock",
    "640": "CSSPropertyForcedColorAdjust",
    "641": "CSSPropertyInherits",
    "642": "CSSPropertyInitialValue",
    "643": "CSSPropertySyntax",
    "644": "CSSPropertyOverscrollBehaviorInline",
    "645": "CSSPropertyOverscrollBehaviorBlock",
    "647": "CSSPropertyFontOpticalSizing",
    "648": "CSSPropertyContainIntrinsicBlockSize",
    "649": "CSSPropertyContainIntrinsicHeight",
    "650": "CSSPropertyContainIntrinsicInlineSize",
    "651": "CSSPropertyContainIntrinsicSize",
    "652": "CSSPropertyContainIntrinsicWidth",
    "654": "CSSPropertyOriginTrialTestProperty",
    "656": "CSSPropertyMathStyle",
    "657": "CSSPropertyAspectRatio",
    "658": "CSSPropertyAppearance",
    "660": "CSSPropertyRubyPosition",
    "661": "CSSPropertyTextUnderlineOffset",
    "662": "CSSPropertyContentVisibility",
    "663": "CSSPropertyTextDecorationThickness",
    "664": "CSSPropertyPageOrientation",
    "665": "CSSPropertyAnimationTimeline",
    "666": "CSSPropertyCounterSet",
    "667": "CSSPropertySource",
    "668": "CSSPropertyStart",
    "669": "CSSPropertyEnd",
    "670": "CSSPropertyTimeRange",
    "671": "CSSPropertyScrollbarGutter",
    "672": "CSSPropertyAscentOverride",
    "673": "CSSPropertyDescentOverride",
    "674": "CSSPropertyAdvanceOverride",
    "675": "CSSPropertyLineGapOverride",
    "676": "CSSPropertyMathShift",
    "677": "CSSPropertyMathDepth",
    "679": "CSSPropertyOverflowClipMargin",
    "680": "CSSPropertyScrollbarWidth",
    "681": "CSSPropertySystem",
    "682": "CSSPropertyNegative",
    "683": "CSSPropertyPrefix",
    "684": "CSSPropertySuffix",
    "685": "CSSPropertyRange",
    "686": "CSSPropertyPad",
    "687": "CSSPropertyFallback",
    "688": "CSSPropertySymbols",
    "689": "CSSPropertyAdditiveSymbols",
    "690": "CSSPropertySpeakAs",
    "691": "CSSPropertyBorderStartStartRadius",
    "692": "CSSPropertyBorderStartEndRadius",
    "693": "CSSPropertyBorderEndStartRadius",
    "694": "CSSPropertyBorderEndEndRadius",
    "695": "CSSPropertyAccentColor",
    "696": "CSSPropertySizeAdjust",
    "697": "CSSPropertyContainerName",
    "698": "CSSPropertyContainerType",
    "699": "CSSPropertyContainer",
    "700": "CSSPropertyFontSynthesisWeight",
    "701": "CSSPropertyFontSynthesisStyle",
    "702": "CSSPropertyAppRegion",
    "703": "CSSPropertyFontSynthesisSmallCaps",
    "704": "CSSPropertyFontSynthesis",
    "705": "CSSPropertyTextEmphasis",
    "706": "CSSPropertyTextEmphasisColor",
    "707": "CSSPropertyTextEmphasisPosition",
    "708": "CSSPropertyTextEmphasisStyle",
    "709": "CSSPropertyFontPalette",
    "710": "CSSPropertyBasePalette",
    "711": "CSSPropertyOverrideColors",
    "712": "CSSPropertyPageTransitionTag",
    "713": "CSSPropertyObjectViewBox",
    "714": "CSSPropertyObjectOverflow",
    "715": "CSSPropertyToggleGroup",
    "716": "CSSPropertyToggleRoot",
    "717": "CSSPropertyToggleTrigger",
    "718": "CSSPropertyToggle",
    "719": "CSSPropertyAnchorName",
    "720": "CSSPropertyPositionFallback",
    "721": "CSSPropertyAnchorScroll",
    "722": "CSSPropertyPopUpShowDelay",
    "723": "CSSPropertyPopUpHideDelay",
    "724": "CSSPropertyHyphenateCharacter",
    "725": "CSSPropertyScrollTimeline",
    "726": "CSSPropertyScrollTimelineName",
    "727": "CSSPropertyScrollTimelineAxis",
    "728": "CSSPropertyViewTimeline",
    "729": "CSSPropertyViewTimelineAxis",
    "730": "CSSPropertyViewTimelineInset",
    "731": "CSSPropertyViewTimelineName",
    "732": "CSSPropertyToggleVisibility",
    "733": "CSSPropertyInitialLetter",
    "734": "CSSPropertyHyphenateLimitChars",
    "735": "CSSPropertyAnimationDelayStart",
    "736": "CSSPropertyAnimationDelayEnd",
    "737": "CSSPropertyFontVariantPosition",
    "738": "CSSPropertyFontVariantAlternates",
    "739": "CSSPropertyBaselineSource",
}

if '__main__' == __name__:
    #import cProfile
    #cProfile.run('main()', None, 2)
    main()
