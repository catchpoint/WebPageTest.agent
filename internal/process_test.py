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

        # Patch the loadTime metric
        if 'loadTime' in page_data and page_data['loadTime'] <= 0 and 'fullyLoaded' in page_data and page_data['fullyLoaded'] > 0:
            page_data['loadTime'] = page_data['fullyLoaded']

        self.save_data()
        logging.debug(json.dumps(self.data['pageData'], indent=4, sort_keys=True))

        # Delete any stand-alone files that were post-processed
        for file in self.delete:
            try:
                logging.debug('Deleting merged metrics file %s', file)
                os.unlink(file)
            except Exception:
                pass

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
