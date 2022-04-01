# Copyright 2021 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""
Support for post-processing test results:
- Collect the page data from the various separate files into the devtools requests file.
- Calculate any post-processed metrics (like core web vitals)
- Optionally (if requested) generate HAR
"""
import gzip
import logging
import os
import re
import shutil
import sys
if (sys.version_info >= (3, 0)):
    from time import monotonic
    from urllib.parse import quote_plus # pylint: disable=import-error
    from urllib.parse import urlsplit # pylint: disable=import-error
    GZIP_READ_TEXT = 'rt'
    GZIP_TEXT = 'wt'
    string_types = str
else:
    from monotonic import monotonic
    from urllib import quote_plus # pylint: disable=import-error,no-name-in-module
    from urlparse import urlsplit # pylint: disable=import-error
    GZIP_READ_TEXT = 'r'
    GZIP_TEXT = 'w'
    string_types = basestring
try:
    import ujson as json
except BaseException:
    import json


class ProcessTest(object):
    """Controller for interfacing with the WebPageTest server"""
    # pylint: disable=E0611
    def __init__(self, options, job, task):
        self.options = options
        self.job = job
        self.task = task
        # Loop through all of the steps from this run
        if 'steps' in self.task:
            for step in self.task['steps']:
                self.prefix = step['prefix']
                self.video_subdirectory = step['video_subdirectory']
                self.step_name = step['step_name']
                self.step_num = step['num']
                self.data = None
                self.delete = []
                self.run_processing()
    
    def run_processing(self):
        """Run the post-processing for the given test step"""
        self.load_data()
        page_data = self.data['pageData']

        self.merge_user_timing_events()
        self.merge_custom_metrics()
        self.merge_interactive_periods()
        self.merge_long_tasks()
        self.calculate_visual_metrics()
        self.process_chrome_timings()
        self.merge_blink_features()
        self.merge_priority_streams()
        self.calculate_TTI()
        self.add_summary_metrics()
        self.merge_crux_data()
        self.merge_lighthouse_data()

        # Mark the data as having been processed so the server can know not to re-process it
        page_data['edge-processed'] = True

        self.save_data()

        # TODO: Delete any stand-alone files that were post-processed (keep them as backup for now)
        """
        for file in self.delete:
            try:
                logging.debug('Deleting merged metrics file %s', file)
                os.unlink(file)
            except Exception:
                pass
        """

    def load_data(self):
        """Load the main page and requests data file (basis for post-processing)"""
        devtools_file = os.path.join(self.task['dir'], self.prefix + '_devtools_requests.json.gz')
        if os.path.isfile(devtools_file):
            with gzip.open(devtools_file, GZIP_READ_TEXT) as f:
                self.data = json.load(f)
        if not self.data or 'pageData' not in self.data:
            raise Exception("Devtools file not present")

    def save_data(self):
        """Write-out the post-processed devtools data"""
        devtools_file = os.path.join(self.task['dir'], self.prefix + '_devtools_requests.json.gz')
        with gzip.open(devtools_file, GZIP_TEXT, 7) as f:
            json.dump(self.data, f)

    def merge_user_timing_events(self):
        """Load and process the timed_events json file"""
        page_data = self.data['pageData']
        timed_events_file = os.path.join(self.task['dir'], self.prefix + '_timed_events.json.gz')
        if os.path.isfile(timed_events_file):
            with gzip.open(timed_events_file, GZIP_READ_TEXT) as f:
                events = json.load(f)
                if events:
                    self.delete.append(timed_events_file)
                    last_event = 0
                    for event in events:
                        try:
                            if event and 'name' in event and 'startTime' in event and 'entryType' in event:
                                name = re.sub(r'[^a-zA-Z0-9\.\-_\(\) ]', '_', event['name'])
                                # Marks
                                if event['entryType'] == 'mark':
                                    time = round(event['startTime'])
                                    if time > 0 and time < 3600000:
                                        if event['startTime'] > last_event:
                                            last_event = event['startTime']
                                        page_data['userTime.{}'.format(name)] = time
                                        if 'userTimes' not in page_data:
                                            page_data['userTimes'] = {}
                                        page_data['userTimes'][name] = time
                                # Measures
                                elif event['entryType'] == 'measure' and 'duration' in event: # User timing measure
                                    duration = round(event['duration'])
                                    page_data['userTimingMeasure.{}'.format(name)] = duration
                                    if 'userTimingMeasures' not in page_data:
                                        page_data['userTimingMeasures'] = []
                                    page_data['userTimingMeasures'].append({
                                        'name': name,
                                        'startTime': event['startTime'],
                                        'duration': event['duration']
                                    })
                        except Exception:
                            logging.exception('Error processing timed event')
                    # Overall time is the time of the last mark
                    page_data['userTime'] = round(last_event)

    def merge_custom_metrics(self):
        """Load the custom metrics into the page data"""
        page_data = self.data['pageData']
        metrics_file = os.path.join(self.task['dir'], self.prefix + '_metrics.json.gz')
        if os.path.isfile(metrics_file):
            with gzip.open(metrics_file, GZIP_READ_TEXT) as f:
                metrics = json.load(f)
                if metrics:
                    self.delete.append(metrics_file)
                    page_data['custom'] = []
                    for name in metrics:
                        try:
                            value = metrics[name]
                            if isinstance(value, string_types):
                                if re.match(r'^[0-9]+$', value):
                                    value = int(value)
                                elif re.match(r'^[0-9]*\.[0-9]+$', value):
                                    value = float(value)
                            page_data[name] = value
                            page_data['custom'].append(name)
                        except Exception:
                            logging.exception('Error processing custom metric %s', name)

    def merge_interactive_periods(self):
        page_data = self.data['pageData']
        metrics_file = os.path.join(self.task['dir'], self.prefix + '_interactive.json.gz')
        if os.path.isfile(metrics_file):
            with gzip.open(metrics_file, GZIP_READ_TEXT) as f:
                metrics = json.load(f)
                if metrics:
                    page_data['interactivePeriods'] = metrics

    def merge_long_tasks(self):
        page_data = self.data['pageData']
        metrics_file = os.path.join(self.task['dir'], self.prefix + '_long_tasks.json.gz')
        if os.path.isfile(metrics_file):
            with gzip.open(metrics_file, GZIP_READ_TEXT) as f:
                metrics = json.load(f)
                if metrics:
                    page_data['longTasks'] = metrics
                    self.delete.append(metrics_file)

    def calculate_visual_metrics(self):
        page_data = self.data['pageData']
        progress_file = os.path.join(self.task['dir'], self.prefix + '_visual_progress.json.gz')
        if os.path.isfile(progress_file):
            with gzip.open(progress_file, GZIP_READ_TEXT) as f:
                progress = json.load(f)
                if progress:
                    speed_index = 0.0
                    last_time = 0
                    last_progress = 0
                    frame = 0
                    for entry in progress:
                        if 'time' in entry and 'progress' in entry:
                            frame += 1
                            progress = min(round(entry['progress']), 100)
                            elapsed = max(entry['time'] - last_time, 0)
                            speed_index += (float(100 - last_progress) / 100.0) * float(elapsed)
                            if 'render' not in page_data and frame > 1:
                                page_data['render'] = entry['time']
                            page_data['lastVisualChange'] = entry['time']
                            if progress >= 85 and 'visualComplete85' not in page_data:
                                page_data['visualComplete85'] = entry['time']
                            if progress >= 90 and 'visualComplete90' not in page_data:
                                page_data['visualComplete90'] = entry['time']
                            if progress >= 95 and 'visualComplete95' not in page_data:
                                page_data['visualComplete95'] = entry['time']
                            if progress >= 99 and 'visualComplete99' not in page_data:
                                page_data['visualComplete99'] = entry['time']
                            if progress >= 100 and 'visualComplete' not in page_data:
                                page_data['visualComplete'] = entry['time']
                            last_time = entry['time']
                            last_progress = progress
                    page_data['SpeedIndex'] = round(speed_index)

    def process_chrome_timings(self):
        """Process the chrome timings pulled from trace events (LCP, FCP, etc)"""
        page_data = self.data['pageData']
        events_file = os.path.join(self.task['dir'], self.prefix + '_user_timing.json.gz')
        if os.path.isfile(events_file):
            with gzip.open(events_file, GZIP_READ_TEXT) as f:
                events = json.load(f)
                if events:
                    layout_shifts = []
                    user_timing = None
                    start_time = None
                    events.sort(key=lambda x: x['CompareTimestamps'] if 'CompareTimestamps' in x else 0)

                    # Make a first pass looking to see if the start time is explicitly set
                    for event in events:
                        if 'startTime' in event:
                            start_time = event['startTime']
                    
                    # Make a pass looking for explicitly tagged main frames
                    main_frames = []
                    for event in events:
                        if 'name' in event and 'args' in event and 'frame' in event['args'] and event['args']['frame'] not in main_frames:
                            if 'data' in event['args'] and \
                                    'isLoadingMainFrame' in event['args']['data'] and event['args']['data']['isLoadingMainFrame'] and \
                                    'documentLoaderURL' in event['args']['data'] and len(event['args']['data']['documentLoaderURL']):
                                main_frames.append(event['args']['frame'])
                            elif 'data' in event['args'] and 'isMainFrame' in event['args']['data'] and event['args']['data']['isMainFrame']:
                                main_frames.append(event['args']['frame'])
                            elif event['name'] == 'markAsMainFrame':
                                main_frames.append(event['args']['frame'])

                    # Find the first navigation to determine which is the main frame
                    for event in events:
                        if 'name' in event and 'ts' in event:
                            if start_time is None:
                                start_time = event['ts']
                            if not main_frames and event['name'] in ['navigationStart', 'unloadEventStart', 'redirectStart', 'domLoading']:
                                if 'args' in event and 'frame' in event['args']:
                                    main_frames.append(event['args']['frame'])
                                    break

                    if main_frames and start_time is not None:
                        # Pre-process the "LargestXXX" events, just recording the biggest one
                        largest = {}
                        for event in events:
                            if 'name' in event and 'ts' in event and 'args' in event and 'frame' in event['args'] and \
                                    event['args']['frame'] in main_frames and \
                                    (event['ts'] >= start_time or 'value' in event['args']) and \
                                    event['name'].lower().find('largest') and \
                                    'data' in event['args'] and 'size' in event['args']['data']:
                                name = event['name']
                                if name not in largest or event['args']['data']['size'] > largest[name]['args']['data']['size']:
                                    time = None
                                    if 'durationInMilliseconds' in event['args']['data']:
                                        time = event['args']['data']['durationInMilliseconds']
                                    elif 'value' in event['args']:
                                        time = event['args']['value']
                                    else:
                                        time = round(float(event['ts'] - start_time) / 1000.0)
                                    if time is not None:
                                        event['time'] = time
                                        largest[name] = event
                                        paint_event = {
                                            'event': name,
                                            'time': time,
                                            'size': event['args']['data']['size']
                                        }
                                        if 'DOMNodeId' in event['args']['data']:
                                            paint_event['DOMNodeId'] = event['args']['data']['DOMNodeId']
                                        if 'node' in event['args']['data']:
                                            paint_event['nodeInfo'] = event['args']['data']['node']
                                        if 'element' in event['args']['data']:
                                            paint_event['element'] = event['args']['data']['element']
                                        if 'type' in event['args']['data']:
                                            paint_event['type'] = event['args']['data']['type']
                                        if 'largestPaints' not in page_data:
                                            page_data['largestPaints'] = []
                                        page_data['largestPaints'].append(paint_event)

                            # grab the element timing stuff while we're here to avoid a separate loop
                            if 'name' in event and 'ts' in event and 'args' in event and 'frame' in event['args'] and \
                                    event['args']['frame'] in main_frames and event['name'] == 'PerformanceElementTiming':
                                try:
                                    if 'elementTiming' not in page_data:
                                        page_data['elementTiming'] = []
                                    page_data['elementTiming'].append({
                                        'identifier': event['args']['data']['identifier'],
                                        'time': event['args']['data']['renderTime'],
                                        'elementType': event['args']['data']['elementType'],
                                        'url': event['args']['data']['url']
                                    })
                                    page_data['elementTiming.{}'.format(event['args']['data']['identifier'])] = event['args']['data']['renderTime']
                                except Exception:
                                    logging.exception('Error processing element timing entry')

                        # Calculate CLS
                        total_layout_shift = 0.0
                        max_layout_window = 0
                        first_shift = 0
                        prev_shift = 0
                        curr = 0
                        shift_window_count = 0
                        for event in events:
                            if 'name' in event and 'ts' in event and 'args' in event and 'frame' in event['args'] and \
                                    event['args']['frame'] in main_frames and \
                                    (event['ts'] >= start_time or 'value' in event['args']):
                                if user_timing is None:
                                    user_timing = []
                                name = event['name']
                                time = None
                                if 'data' in event['args'] and 'durationInMilliseconds' in event['args']['data']:
                                    time = event['args']['data']['durationInMilliseconds']
                                elif 'value' in event['args']:
                                    time = event['args']['value']
                                else:
                                    time = round(float(event['ts'] - start_time) / 1000.0)
                                if name == 'LayoutShift' and 'data' in event['args'] and \
                                        'is_main_frame' in event['args']['data'] and event['args']['data']['is_main_frame'] and \
                                        'score' in event['args']['data']:
                                    if time is not None:
                                        if total_layout_shift is None:
                                            total_layout_shift = 0
                                        total_layout_shift += event['args']['data']['score']

                                        if time - first_shift > 5000 or time - prev_shift > 1000:
                                            # New shift window
                                            first_shift = time
                                            curr = 0
                                            shift_window_count += 1
                                        
                                        prev_shift = time
                                        curr += event['args']['data']['score']
                                        max_layout_window = max(curr, max_layout_window)

                                        shift = {
                                            'time': time,
                                            'score': event['args']['data']['score'],
                                            'cumulative_score': total_layout_shift,
                                            'window_score': curr,
                                            'shift_window_num': shift_window_count
                                        }

                                        if 'region_rects' in event['args']['data']:
                                            shift['rects'] = event['args']['data']['region_rects']
                                        if 'sources' in event['args']['data']:
                                            shift['sources'] = event['args']['data']['sources']
                                        layout_shifts.append(shift)

                                if name is not None and time is not None and name not in largest:
                                    user_timing.append({'name': name, 'time': time})

                        for name in largest:
                            event = largest[name]
                            if user_timing is None:
                                user_timing = []
                            user_timing.append({'name': event['name'], 'time': event['time']})
                        
                        if 'LargestContentfulPaint' in largest:
                            event = largest['LargestContentfulPaint']
                            if 'args' in event and 'data' in event['args'] and 'type' in event['args']['data']:
                                page_data['LargestContentfulPaintType'] = event['args']['data']['type']
                                # For images, extract the URL if there is one
                                if event['args']['data']['type'] == 'image' and 'largestPaints' in page_data:
                                    for paint_event in page_data['largestPaints']:
                                        if paint_event['event'] == 'LargestImagePaint' and paint_event['time'] == event['time']:
                                            if 'nodeInfo' in paint_event and 'nodeType' in paint_event['nodeInfo']:
                                                page_data['LargestContentfulPaintNodeType'] = paint_event['nodeInfo']['nodeType']
                                            if 'nodeInfo' in paint_event and 'sourceURL' in paint_event['nodeInfo']:
                                                page_data['LargestContentfulPaintImageURL'] = paint_event['nodeInfo']['sourceURL']
                                            elif 'nodeInfo' in paint_event and 'styles' in paint_event['nodeInfo'] and 'background-image' in paint_event['nodeInfo']['styles']:
                                                matches = re.match(r'url\("?\'?([^"\'\)]+)', paint_event['nodeInfo']['styles']['background-image'])
                                                if matches:
                                                    page_data['LargestContentfulPaintType'] = 'background-image'
                                                    page_data['LargestContentfulPaintImageURL'] = matches.group(1)
                                elif 'largestPaints' in page_data:
                                    for paint_event in page_data['largestPaints']:
                                        if paint_event['event'] == 'LargestTextPaint' and paint_event['time'] == event['time']:
                                            if 'nodeInfo' in paint_event  and 'nodeType' in paint_event['nodeInfo']:
                                                page_data['LargestContentfulPaintNodeType'] = paint_event['nodeInfo']['nodeType']

                        if user_timing is None:
                            user_timing = []
                        user_timing.append({'name': 'TotalLayoutShift', 'value': total_layout_shift})
                        user_timing.append({'name': 'CumulativeLayoutShift', 'value': max_layout_window})

                    # process the user_timing data
                    if user_timing is not None:
                        page_data['chromeUserTiming'] = user_timing
                        for value in user_timing:
                            key = 'chromeUserTiming.{}'.format(value['name'])
                            if 'time' in value:
                                # Prefer the earliest for "first" events and the latest for others
                                if 'first' in value['name'].lower():
                                    if key not in page_data or value['time'] < page_data[key]:
                                        page_data[key] = value['time']
                                elif key not in page_data or value['time'] > page_data[key]:
                                    page_data[key] = value['time']
                            elif 'value' in value:
                                page_data[key] = value['value']

                    if layout_shifts:
                        page_data['LayoutShifts'] = layout_shifts

                        # Count the number of LayoutShifts before first paint
                        if 'chromeUserTiming.TotalLayoutShift' in page_data and 'chromeUserTiming.firstPaint' in page_data:
                            count = 0
                            cls = 0
                            fraction = 0
                            for shift in page_data['LayoutShifts']:
                                if 'time' in shift and shift['time'] <= page_data['chromeUserTiming.firstPaint']:
                                    count += 1
                                    cls = shift['cumulative_score']
                            if page_data['chromeUserTiming.TotalLayoutShift'] > 0:
                                fraction = float(cls) / float(page_data['chromeUserTiming.TotalLayoutShift'])

                            page_data['LayoutShiftsBeforePaint'] = {
                                'count': count,
                                'cumulative_score': cls,
                                'fraction_of_total': fraction
                            }

    def merge_blink_features(self):
        """Merge the blink featured flags that were detected"""
        page_data = self.data['pageData']
        metrics_file = os.path.join(self.task['dir'], self.prefix + '_feature_usage.json.gz')
        if os.path.isfile(metrics_file):
            with gzip.open(metrics_file, GZIP_READ_TEXT) as f:
                metrics = json.load(f)
                if metrics:
                    page_data['blinkFeatureFirstUsed'] = metrics
                    self.delete.append(metrics_file)

    def merge_priority_streams(self):
        """Merge the list of HTTP/2 priority-only stream data"""
        page_data = self.data['pageData']
        metrics_file = os.path.join(self.task['dir'], self.prefix + '_priority_streams.json.gz')
        if os.path.isfile(metrics_file):
            with gzip.open(metrics_file, GZIP_READ_TEXT) as f:
                metrics = json.load(f)
                if metrics:
                    page_data['priorityStreams'] = metrics
                    self.delete.append(metrics_file)

    def calculate_TTI(self):
        """Calculate Time to Interactive if we have First Contentful Paint and interactive Windows"""
        page_data = self.data['pageData']
        if 'interactivePeriods' in page_data and page_data['interactivePeriods']:
            interactive_periods = page_data['interactivePeriods']
            start_time = 0
            tti = None
            last_interactive = 0
            first_interactive = None
            measurement_end = 0
            max_fid = None
            total_blocking_time = None
            if 'render' in page_data and page_data['render'] > 0:
                start_time = page_data['render']
            elif 'firstContentfulPaint' in page_data and page_data['firstContentfulPaint'] > 0:
                start_time = page_data['firstContentfulPaint']
            elif 'firstPaint' in page_data and page_data['firstPaint'] > 0:
                start_time = page_data['firstPaint']
            dcl = None
            if 'domContentLoadedEventEnd' in page_data:
                dcl = page_data['domContentLoadedEventEnd']
            long_tasks = None
            if 'longTasks' in page_data:
                long_tasks = page_data['longTasks']

            # Run the actual TTI calculation
            if start_time > 0:
                # See when the absolute last interaction measurement was
                for window in interactive_periods:
                    if window[1] > measurement_end:
                        measurement_end = max(window[1], start_time)
                        last_interactive = max(window[0], start_time)

                # Start by filtering the interactive windows to only include 5 second windows that don't
                # end before the start time.                
                end = 0
                iw = []
                for window in interactive_periods:
                    end = window[1]
                    duration = window[1] - window[0]
                    if end > start_time and duration >= 5000:
                        iw.append(window)
                        if first_interactive is None or window[0] < first_interactive:
                            first_interactive = max(window[0], start_time)
                
                # Find all of the request windows with 5 seconds of no more than 2 concurrent document requests
                rw = []
                requests = self.data['requests']
                if iw and requests:
                    # Build a list of start/end events for document requests
                    req = []
                    for request in requests:
                        if 'contentType' in request and \
                                'load_start' in request and request['load_start'] >= 0 and \
                                'load_end' in request and request['load_end'] > start_time:
                            if 'method' not in request or request['method'] == 'GET':
                                req.append({'type': 'start', 'time': request['load_start']})
                                req.append({'type': 'end', 'time': request['load_end']})
                
                    # walk the list of events tracking the number of in-flight requests and log any windows > 5 seconds
                    if req:
                        req.sort(key=lambda x: x['time'])
                        window_start = 0
                        in_flight = 0
                        for e in req:
                            if e['type'] == 'start':
                                in_flight += 1
                                if window_start is not None and in_flight > 2:
                                    window_end = e['time']
                                    if window_end - window_start >= 5000:
                                        rw.append([window_start, window_end])
                                    window_start = None
                            else:
                                in_flight -= 1
                                if window_start is not None and in_flight <= 2:
                                    window_start = e['time']
                        if window_start is not None and end - window_start >= 5000:
                            rw.append([window_start, end])
                
                # Find the first interactive window that also has at least a 5 second intersection with one of the request windows
                if rw:
                    window = None
                    for i in iw:
                        if window is None:
                            for r in rw:
                                if window is None:
                                    intersect = [max(i[0], r[0]), min(i[1], r[1])]
                                    if intersect[1] - intersect[0] >= 5000:
                                        window = i
                                        break
                    if window is not None:
                        tti = max(start_time, window[0])
                
                # Calculate the total blocking time - https://web.dev/tbt/
                # and the max possible FID (longest task)
                end_time = tti if tti is not None else last_interactive
                if long_tasks:
                    total_blocking_time = 0
                    max_fid = 0
                    if end_time > start_time:
                        for task in long_tasks:
                            start = max(task[0], start_time) + 50 # "blocking" time excludes the first 50ms
                            end = min(task[1], end_time)
                            busy_time = end - start
                            if busy_time > 0:
                                total_blocking_time += busy_time
                                if busy_time > max_fid:
                                    max_fid = busy_time
                
                # DOM Content loaded is the floor for any possible TTI times
                if dcl is not None:
                    if tti is not None and tti > 0 and dcl > tti:
                        tti = dcl
                    if first_interactive is not None and first_interactive > 0 and dcl > first_interactive:
                        first_interactive = dcl

            # Merge the metrics into the page data
            if first_interactive is not None and first_interactive > 0:
                page_data['FirstInteractive'] = first_interactive
            if tti is not None and tti > 0:
                page_data['TimeToInteractive'] = tti
            if max_fid is not None:
                page_data['maxFID'] = max_fid
            if measurement_end > 0:
                page_data['TTIMeasurementEnd'] = measurement_end
            if last_interactive > 0:
                page_data['LastInteractive'] = last_interactive
            if 'TimeToInteractive' not in page_data and measurement_end > 0 and last_interactive > 0 and measurement_end - last_interactive >= 5000:
                page_data['TimeToInteractive'] = last_interactive
                if 'FirstInteractive' not in page_data:
                    page_data['FirstInteractive'] = last_interactive
            if 'FirstInteractive' in page_data:
                page_data['FirstCPUIdle'] = page_data['FirstInteractive']
            if total_blocking_time is not None:
                page_data['TotalBlockingTime'] = total_blocking_time

    def add_summary_metrics(self):
        """Do the metric cleanup and some top-level metrics"""
        page_data = self.data['pageData']

        # Patch the loadTime metric
        if 'loadTime' in page_data and page_data['loadTime'] <= 0 and 'fullyLoaded' in page_data and page_data['fullyLoaded'] > 0:
            page_data['loadTime'] = page_data['fullyLoaded']

        # For visual tests (black-box browser testing) use the visual metrics as the base timings
        if 'visualTest' in page_data and page_data['visualTest'] and 'visualComplete' in page_data:
            page_data['loadTime'] = page_data['visualComplete']
            page_data['docTime'] = page_data['visualComplete']
            page_data['fullyLoaded'] = page_data['lastVisualChange']
        
        # See if we have pcap-based versions of the various metrics
        if ('bytesIn' not in page_data or page_data['bytesIn'] <= 0) and 'pcapBytesIn' in page_data and page_data['pcapBytesIn'] > 0:
            page_data['bytesIn'] = page_data['pcapBytesIn']
        if ('bytesInDoc' not in page_data or page_data['bytesInDoc'] <= 0) and 'pcapBytesIn' in page_data and page_data['pcapBytesIn'] > 0:
            page_data['bytesInDoc'] = page_data['pcapBytesIn']
        if ('bytesOut' not in page_data or page_data['bytesOut'] <= 0) and 'pcapBytesOut' in page_data and page_data['pcapBytesOut'] > 0:
            page_data['bytesOut'] = page_data['pcapBytesOut']
        if ('bytesOutDoc' not in page_data or page_data['bytesOutDoc'] <= 0) and 'pcapBytesOut' in page_data and page_data['pcapBytesOut'] > 0:
            page_data['bytesOutDoc'] = page_data['pcapBytesOut']
        
        # Basic run information
        page_data['testID'] = self.task['id']
        page_data['run'] = self.task['run']
        page_data['cached'] = 1 if self.task['cached'] else 0
        page_data['step'] = self.step_num
        if 'metadata' in self.job:
            page_data['metadata'] = self.job['metadata']

        # Calculate effective bps
        if 'fullyLoaded' in page_data and page_data['fullyLoaded'] > 0 and \
                'TTFB' in page_data and page_data['TTFB'] > 0 and \
                'bytesIn' in page_data and page_data['bytesIn'] > 0 and \
                page_data['fullyLoaded'] > page_data['TTFB']:
            page_data['effectiveBps'] = round(float(page_data['bytesIn']) / (float(page_data['fullyLoaded'] - page_data['TTFB']) / 1000.0))
        if 'docTime' in page_data and page_data['docTime'] > 0 and \
                'TTFB' in page_data and page_data['TTFB'] > 0 and \
                'bytesInDoc' in page_data and page_data['bytesInDoc'] > 0 and \
                page_data['docTime'] > page_data['TTFB']:
            page_data['effectiveBps'] = round(float(page_data['bytesInDoc']) / (float(page_data['docTime'] - page_data['TTFB']) / 1000.0))

        # clean up any insane values (from negative numbers as unsigned most likely)
        if 'firstPaint' in page_data and 'fullyLoaded' in page_data and page_data['firstPaint'] > page_data['fullyLoaded']:
            page_data['firstPaint'] = 0
        times = ['loadTime', 'TTFB', 'render', 'fullyLoaded', 'docTime', 'domTime', 'aft', 'titleTime', 'loadEventStart', 'loadEventEnd',
                 'domContentLoadedEventStart', 'domContentLoadedEventEnd', 'domLoading', 'domInteractive',
                 'lastVisualChange', 'visualComplete', 'server_rtt', 'firstPaint']
        for key in times:
            if key not in page_data or page_data[key] > 3600000 or page_data[key] < 0:
                page_data[key] = 0
        if 'fullyLoaded' in page_data:
            page_data['fullyLoaded'] = round(page_data['fullyLoaded'])
        if 'firstContentfulPaint' not in page_data:
            if 'chromeUserTiming.firstContentfulPaint' in page_data:
                page_data['firstContentfulPaint'] = page_data['chromeUserTiming.firstContentfulPaint']
            elif 'PerformancePaintTiming.first-contentful-paint' in page_data:
                page_data['firstContentfulPaint'] = page_data['PerformancePaintTiming.first-contentful-paint']

        # See if there is a test-level error that needs to be exposed
        if 'error' in self.task and self.task['error']:
            if 'result' not in page_data or page_data['result'] == 0 or page_data['result'] == 99999:
                page_data['result'] = 99995
            page_data['error'] = self.task['error']

    def merge_crux_data(self):
        """Pull in the crux data if it is present"""
        page_data = self.data['pageData']
        # Copy the local crux data file to the shared test directory if there is one
        file_name = 'crux.json.gz'
        local_file = os.path.join(self.task['dir'], file_name)
        metrics_file = os.path.join(self.job['test_shared_dir'], file_name)
        if os.path.isfile(local_file) and not os.path.isfile(metrics_file):
            shutil.copyfile(local_file, metrics_file)
            self.delete.append(local_file)
        if os.path.isfile(metrics_file):
            with gzip.open(metrics_file, GZIP_READ_TEXT) as f:
                metrics = json.load(f)
                if metrics:
                    if 'record' in metrics:
                        page_data['CrUX'] = metrics['record']
                    else:
                        page_data['CrUX'] = metrics

    def merge_lighthouse_data(self):
        """Pull in the lighthouse audit info if present"""
        page_data = self.data['pageData']
        # Copy the local crux data file to the shared test directory if there is one
        file_name = 'lighthouse_audits.json.gz'
        local_file = os.path.join(self.task['dir'], file_name)
        metrics_file = os.path.join(self.job['test_shared_dir'], file_name)
        if os.path.isfile(local_file) and not os.path.isfile(metrics_file):
            shutil.copyfile(local_file, metrics_file)
            self.delete.append(local_file)
        if os.path.isfile(metrics_file):
            with gzip.open(metrics_file, GZIP_READ_TEXT) as f:
                audits = json.load(f)
                if audits:
                    logging.debug(audits)
                    for name in audits:
                        page_data['lighthouse.{}'.format(name)] = audits[name]
