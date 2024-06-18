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
        # Load the feature name mappings
        path = os.path.abspath(os.path.dirname(__file__))
        with open(os.path.join(path, 'FEATURES', 'blink.json'), 'rt', encoding='utf-8') as f:
            self.BLINK_FEATURES = json.load(f)
        with open(os.path.join(path, 'FEATURES', 'css.json'), 'rt', encoding='utf-8') as f:
            self.CSS_FEATURES = json.load(f)

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
        if 'name' in trace_event and\
                'args' in trace_event and\
                'feature' in trace_event['args'] and\
            (trace_event['name'] == 'FeatureFirstUsed' or trace_event['name'] == 'CSSFirstUsed'):
            if self.feature_usage is None:
                self.feature_usage = {
                    'Features': {}, 'CSSFeatures': {}, 'AnimatedCSSFeatures': {}}
            id = '{0:d}'.format(trace_event['args']['feature'])
            if trace_event['name'] == 'FeatureFirstUsed':
                if id in self.BLINK_FEATURES:
                    name = self.BLINK_FEATURES[id]
                else:
                    name = 'Feature_{0}'.format(id)
                if id not in self.feature_usage['Features']:
                    self.feature_usage['Features'][id] = {'name': name, 'firstUsed': []}
                self.feature_usage['Features'][id]['firstUsed'].append(trace_event['ts'])
            elif trace_event['name'] == 'CSSFirstUsed':
                if id in self.CSS_FEATURES:
                    name = self.CSS_FEATURES[id]
                else:
                    name = 'CSSFeature_{0}'.format(id)
                if id not in self.feature_usage['CSSFeatures']:
                    self.feature_usage['CSSFeatures'][id] = {'name': name, 'firstUsed': []}
                self.feature_usage['CSSFeatures'][id]['firstUsed'].append(trace_event['ts'])
            elif trace_event['name'] == 'AnimatedCSSFirstUsed':
                if id in self.CSS_FEATURES:
                    name = self.CSS_FEATURES[id]
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

if '__main__' == __name__:
    #import cProfile
    #cProfile.run('main()', None, 2)
    main()
