# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for interfacing with Chrome's remote debugging protocol"""
import base64
import glob
import gzip
import logging
import math
import os
import re
import subprocess
import time
import ujson as json

VIDEO_SIZE = 400

class DevTools(object):
    """Interface into Chrome's remote dev tools protocol"""
    def __init__(self, job, task):
        self.url = "http://localhost:{0:d}/json".format(task['port'])
        self.path_base = os.path.join(task['dir'], task['prefix'])
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
        self.video_path = os.path.join(task['dir'], 'video')
        self.video_prefix = os.path.join(self.video_path, 'ms_')
        if not os.path.isdir(self.video_path):
            os.makedirs(self.video_path)
        self.websocket = None
        self.job = job
        self.task = task
        self.command_id = 0
        self.page_loaded = False
        self.main_frame = None
        self.is_navigating = False
        self.last_activity = time.clock()
        self.error = None
        self.dev_tools_file = None
        self.trace_file = None
        self.trace_file_path = None
        self.trace_ts_start = None

    def connect(self, timeout):
        """Connect to the browser"""
        import requests
        ret = False
        end_time = time.clock() + timeout
        while not ret and time.clock() < end_time:
            try:
                response = requests.get(self.url, timeout=timeout)
                if len(response.text):
                    tabs = response.json()
                    logging.debug("Dev Tools tabs: %s", json.dumps(tabs))
                    if len(tabs):
                        websocket_url = None
                        for index in xrange(len(tabs)):
                            if 'type' in tabs[index] and \
                                    tabs[index]['type'] == 'page' and \
                                    'webSocketDebuggerUrl' in tabs[index]:
                                websocket_url = tabs[index]['webSocketDebuggerUrl']
                                break
                        if websocket_url is not None:
                            from websocket import create_connection
                            self.websocket = create_connection(websocket_url)
                            if self.websocket:
                                self.websocket.settimeout(1)
                                ret = True
                        else:
                            time.sleep(1)
            except BaseException as err:
                logging.critical("Connect to dev tools Error: %s", err.__str__)
        return ret

    def close(self):
        """Close the dev tools connection"""
        if self.websocket:
            self.websocket.close()
            self.websocket = None

    def start_recording(self):
        """Start capturing dev tools, timeline and trace data"""
        self.page_loaded = False
        self.is_navigating = True
        self.error = None
        if 'Capture Video' in self.job and self.job['Capture Video']:
            self.grab_screenshot(self.video_prefix + '000000.png')
        self.flush_pending_messages()
        self.send_command('Page.enable', {})
        self.send_command('Network.enable', {})
        self.send_command('Security.enable', {})
        self.send_command('Console.enable', {})
        if 'trace' in self.job and self.job['trace']:
            if 'traceCategories' in self.job:
                trace = self.job['traceCategories']
            else:
                trace = "-*,blink,v8,cc,gpu,blink.net,netlog,disabled-by-default-v8.runtime_stats"
        else:
            trace = "-*"
        if 'timeline' in self.job and self.job['timeline']:
            trace += ",blink.console,disabled-by-default-devtools.timeline,devtools.timeline"
            trace += ",disabled-by-default-blink.feature_usage"
            trace += ",toplevel,disabled-by-default-devtools.timeline.frame,devtools.timeline.frame"
        if 'Capture Video' in self.job and self.job['Capture Video']:
            trace += ",disabled-by-default-devtools.screenshot"
        trace += ",blink.user_timing"
        self.send_command('Tracing.start',
                          {'categories': trace, 'options': 'record-as-much-as-possible'})
        if 'web10' not in self.task or not self.task['web10']:
            self.last_activity = time.clock()

    def stop_recording(self):
        """Stop capturing dev tools, timeline and trace data"""
        self.send_command('Page.disable', {})
        self.send_command('Network.disable', {})
        self.send_command('Security.disable', {})
        self.send_command('Console.disable', {})
        if self.dev_tools_file is not None:
            self.dev_tools_file.write("\n]")
            self.dev_tools_file.close()
            self.dev_tools_file = None

        self.send_command('Tracing.end', {})
        # Keep pumping messages until we get tracingComplete or
        # we get a gap of 30 seconds between messages
        if self.websocket:
            logging.info('Collecting trace events')
            done = False
            last_message = time.clock()
            self.websocket.settimeout(1)
            while not done and time.clock() - last_message < 30:
                try:
                    raw = self.websocket.recv()
                    if raw is not None and len(raw):
                        msg = json.loads(raw)
                        if 'method' in msg:
                            if msg['method'] == 'Tracing.tracingComplete':
                                done = True
                            elif msg['method'] == 'Tracing.dataCollected':
                                last_message = time.clock()
                                self.process_trace_event(msg)
                except BaseException as _:
                    pass
        if self.trace_file is not None:
            self.trace_file.write("\n]}")
            self.trace_file.close()
            self.trace_file = None
        # Post-process the trace file
        if self.trace_file_path is not None:
            logging.info('Processing trace file')
            user_timing = self.path_base + 'user_timing.json.gz'
            cpu_slices = self.path_base + 'timeline_cpu.json.gz'
            script_timing = self.path_base + 'script_timing.json.gz'
            feature_usage = self.path_base + 'feature_usage.json.gz'
            interactive = self.path_base + 'interactive.json.gz'
            v8_stats = self.path_base + 'v8stats.json.gz'
            trace_parser = os.path.join(self.support_path, "trace-parser.py")
            subprocess.call(['python', trace_parser, '-vvvv',
                             '-t', self.trace_file_path, '-u', user_timing, '-c', cpu_slices,
                             '-j', script_timing, '-f', feature_usage, '-i', interactive,
                             '-s', v8_stats])
            self.trace_file_path = None
        # Post Process the video frames
        if 'Capture Video' in self.job and self.job['Capture Video']:
            self.process_video()

    def flush_pending_messages(self):
        """Clear out any pending websocket messages"""
        if self.websocket:
            try:
                self.websocket.settimeout(0)
                while True:
                    raw = self.websocket.recv()
                    if not raw:
                        break
            except BaseException as _:
                pass

    def send_command(self, method, params, wait=False):
        """Send a raw dev tools message and optionally wait for the response"""
        ret = None
        if self.websocket:
            self.command_id += 1
            msg = {'id': self.command_id, 'method': method, 'params': params}
            try:
                out = json.dumps(msg)
                logging.debug("Sending: %s", out)
                self.websocket.send(out)
                if wait:
                    self.websocket.settimeout(1)
                    end_time = time.clock() + 30
                    while ret is None and time.clock() < end_time:
                        try:
                            raw = self.websocket.recv()
                            if raw is not None and len(raw):
                                logging.debug(raw[:1000])
                                msg = json.loads(raw)
                                if 'id' in msg and int(msg['id']) == msg['id']:
                                    ret = msg
                        except BaseException as _:
                            pass
            except BaseException as err:
                logging.critical("Websocket send error: %s", err.__str__)
        return ret

    def wait_for_page_load(self):
        """Wait for the page load and activity to finish"""
        if self.websocket:
            self.websocket.settimeout(1)
            now = time.clock()
            end_time = now + self.task['time_limit']
            done = False
            while not done:
                try:
                    raw = self.websocket.recv()
                    if raw is not None and len(raw):
                        logging.debug(raw[:1000])
                        msg = json.loads(raw)
                        if 'method' in msg:
                            self.process_message(msg)
                except BaseException as _:
                    # ignore timeouts when we're in a polling read loop
                    pass
                now = time.clock()
                elapsed_activity = now - self.last_activity
                if self.page_loaded and elapsed_activity >= 2:
                    done = True
                elif self.error is not None:
                    done = True
                elif now >= end_time:
                    done = True
                    self.error = "Timeout"
        return self.error

    def grab_screenshot(self, path, png=True):
        """Save the screen shot (png or jpeg)"""
        response = self.send_command("Page.captureScreenshot", {}, True)
        if response is not None and 'result' in response and 'data' in response['result']:
            if png:
                with open(path, 'wb') as image_file:
                    image_file.write(base64.b64decode(response['result']['data']))
            else:
                tmp_file = path + '.png'
                with open(tmp_file, 'wb') as image_file:
                    image_file.write(base64.b64decode(response['result']['data']))
                args = ['convert', '-quality', str(self.job['iq']), tmp_file, path]
                subprocess.call(args, shell=True)
                if os.path.isfile(tmp_file):
                    os.remove(tmp_file)

    def process_message(self, msg):
        """Process an inbound dev tools message"""
        parts = msg['method'].split('.')
        if len(parts) >= 2:
            category = parts[0]
            event = parts[1]
            if category == 'Page':
                self.process_page_event(event, msg)
                self.log_dev_tools_event(msg)
            elif category == 'Network':
                self.process_network_event()
                self.log_dev_tools_event(msg)
            elif category == 'Inspector':
                self.process_inspector_event(event)
            elif category == 'Tracing':
                self.process_trace_event(msg)
            else:
                self.log_dev_tools_event(msg)

    def process_page_event(self, event, msg):
        """Process Page.* dev tools events"""
        if event == 'loadEventFired':
            self.page_loaded = True
        elif event == 'frameStartedLoading' and 'params' in msg and 'frameId' in msg['params']:
            if self.is_navigating and self.main_frame is None:
                self.is_navigating = False
                self.main_frame = msg['params']['frameId']
            if self.main_frame == msg['params']['frameId']:
                logging.debug("Navigating main frame")
                self.last_activity = time.clock()
                self.page_loaded = False
        elif event == 'javascriptDialogOpening':
            self.error = "Page opened a modal dailog"

    def process_network_event(self):
        """Process Network.* dev tools events"""
        if 'web10' not in self.task or not self.task['web10']:
            logging.debug('Activity detected')
            self.last_activity = time.clock()

    def process_inspector_event(self, event):
        """Process Inspector.* dev tools events"""
        if event == 'detached':
            self.error = 'Inspector detached, possibly crashed.'
        elif event == 'targetCrashed':
            self.error = 'Browser crashed.'

    def process_trace_event(self, msg):
        """Process Tracing.* dev tools events"""
        if msg['method'] == 'Tracing.dataCollected' and \
                'params' in msg and \
                'value' in msg['params'] and \
                len(msg['params']['value']):
            if self.trace_file is None:
                self.trace_file_path = self.path_base + 'trace.json.gz'
                self.trace_file = gzip.open(self.trace_file_path, 'wb')
                self.trace_file.write('{"traceEvents":[{}')
            # write out the trace events one-per-line but pull out any
            # devtools screenshots as separate files.
            if self.trace_file is not None:
                for index in xrange(len(msg['params']['value'])):
                    trace_event = msg['params']['value'][index]
                    is_screenshot = False
                    if 'cat' in trace_event and 'name' in trace_event and 'ts' in trace_event:
                        if self.trace_ts_start is None and \
                                trace_event['name'] == 'navigationStart' and \
                                trace_event['cat'].find('blink.user_timing') > -1:
                            self.trace_ts_start = trace_event['ts']
                        if trace_event['name'] == 'Screenshot' and \
                                trace_event['cat'].find('devtools.screenshot') > -1:
                            is_screenshot = True
                            if self.trace_ts_start is not None and \
                                    'args' in trace_event and \
                                    'snapshot' in trace_event['args']:
                                ms_elapsed = int(round(float(trace_event['ts'] - \
                                                             self.trace_ts_start) / 1000.0))
                                if ms_elapsed >= 0:
                                    path = '{0}{1:06d}.png'.format(self.video_prefix, ms_elapsed)
                                    with open(path, 'wb') as image_file:
                                        image_file.write(
                                            base64.b64decode(trace_event['args']['snapshot']))
                    if not is_screenshot:
                        self.trace_file.write(",\n")
                        self.trace_file.write(json.dumps(trace_event))
                logging.debug("Processed %d trace events", len(msg['params']['value']))

    def log_dev_tools_event(self, msg):
        """Log the dev tools events to a file"""
        if self.dev_tools_file is None:
            path = self.path_base + 'devtools.json.gz'
            self.dev_tools_file = gzip.open(path, 'wb')
            self.dev_tools_file.write("[{}")
        if self.dev_tools_file is not None:
            self.dev_tools_file.write(",\n")
            self.dev_tools_file.write(json.dumps(msg))

    def process_video(self):
        """Make all of the image frames the same size, run
        visualmetrics against them and convert them all to jpeg"""
        from PIL import Image
        if os.path.isdir(self.video_path):
            # Make them all the same size
            logging.debug("Resizing video frames")
            min_width = None
            min_height = None
            images = {}
            for filename in os.listdir(self.video_path):
                filepath = os.path.join(self.video_path, filename)
                with Image.open(filepath) as image:
                    width, height = image.size
                    images[filename] = {'path': filepath, 'width': width, 'height': height}
                    if min_width is None or (width > 0 and width < min_width):
                        min_width = width
                    if min_height is None or (height > 0 and height < min_height):
                        min_height = height
            if min_width is not None and min_height is not None:
                for filename in os.listdir(self.video_path):
                    if filename in images and \
                            (images[filename]['width'] > min_width or \
                                    images[filename]['height'] > min_height):
                        filepath = os.path.join(self.video_path, filename)
                        tmp = filepath + '.png'
                        os.rename(filepath, tmp)
                        if subprocess.call(['convert', tmp, '-resize',
                                            '{0:d}x{1:d}'.format(min_width, min_height), filepath],
                                           shell=True) == 0:
                            os.remove(tmp)
                        else:
                            os.rename(tmp, filepath)
            # Eliminate duplicate frames
            logging.debug("Removing duplicate video frames")
            self.cap_frame_count(self.video_path, 50)
            files = sorted(glob.glob(os.path.join(self.video_path, 'ms_*.png')))
            count = len(files)
            if count > 2:
                baseline = files[0]
                for index in xrange(1, count):
                    if self.frames_match(baseline, files[index], 1, 0):
                        logging.debug('Removing similar frame %s', os.path.basename(files[index]))
                        os.remove(files[index])
                    else:
                        baseline = files[index]
            # Run visualmetrics against them
            logging.debug("Processing video frames")
            filename = '{0:d}.{1:d}.histograms.json.gz'.format(self.task['run'],
                                                               self.task['cached'])
            histograms = os.path.join(self.task['dir'], filename)
            visualmetrics = os.path.join(self.support_path, "visualmetrics.py")
            subprocess.call(['python', visualmetrics, '-vvvv',
                             '-d', self.video_path, '--histogram', histograms])
            # Convert them all to jpeg
            logging.debug("Converting video frames to jpeg")
            for filename in os.listdir(self.video_path):
                ext = filename.find('.png')
                if ext > 0:
                    src = os.path.join(self.video_path, filename)
                    dst = os.path.join(self.video_path, filename[0:ext] + '.jpg')
                    args = ['convert', src, '-resize', '{0:d}x{0:d}'.format(VIDEO_SIZE),
                            '-quality', str(self.job['iq']), dst]
                    logging.debug(' '.join(args))
                    subprocess.call(args, shell=True)
                    os.remove(src)

    def frames_match(self, image1, image2, fuzz_percent, max_differences):
        """Compare video frames"""
        match = False
        args = ['compare', '-metric', 'AE']
        if fuzz_percent > 0:
            args.extend(['-fuzz', '{0:d}%'.format(fuzz_percent)])
        args.extend([image1, image2, 'null:'])
        compare = subprocess.Popen(args, stderr=subprocess.PIPE, shell=True)
        _, err = compare.communicate()
        if re.match('^[0-9]+$', err):
            different_pixels = int(err)
            if different_pixels <= max_differences:
                match = True
        return match

    def cap_frame_count(self, directory, maxframes):
        """Limit the number of video frames using an decay for later times"""
        frames = sorted(glob.glob(os.path.join(directory, 'ms_*.png')))
        frame_count = len(frames)
        if frame_count > maxframes:
            # First pass, sample all video frames at 10fps instead of 60fps,
            # keeping the first 20% of the target
            logging.debug('Sampling 10fps: Reducing %d frames to target of %d...',
                          frame_count, maxframes)
            skip_frames = int(maxframes * 0.2)
            self.sample_frames(frames, 100, 0, skip_frames)
            frames = sorted(glob.glob(os.path.join(directory, 'ms_*.png')))
            frame_count = len(frames)
            if frame_count > maxframes:
                # Second pass, sample all video frames after the first 5 seconds
                # at 2fps, keeping the first 40% of the target
                logging.debug('Sampling 2fps: Reducing %d frames to target of %d...',
                              frame_count, maxframes)
                skip_frames = int(maxframes * 0.4)
                self.sample_frames(frames, 500, 5000, skip_frames)
                frames = sorted(glob.glob(os.path.join(directory, 'ms_*.png')))
                frame_count = len(frames)
                if frame_count > maxframes:
                    # Third pass, sample all video frames after the first 10 seconds
                    # at 1fps, keeping the first 60% of the target
                    logging.debug('Sampling 1fps: Reducing %d frames to target of %d...',
                                  frame_count, maxframes)
                    skip_frames = int(maxframes * 0.6)
                    self.sample_frames(frames, 1000, 10000, skip_frames)
        logging.debug('%d frames final count with a target max of %d frames...',
                      frame_count, maxframes)


    def sample_frames(self, frames, interval, start_ms, skip_frames):
        """Sample frames at a given interval"""
        frame_count = len(frames)
        if frame_count > 3:
            # Always keep the first and last frames, only sample in the middle
            first_frame = frames[0]
            first_change = frames[1]
            last_frame = frames[-1]
            match = re.compile(r'ms_(?P<ms>[0-9]+)\.')
            matches = re.search(match, first_change)
            first_change_time = 0
            if matches is not None:
                first_change_time = int(matches.groupdict().get('ms'))
            last_bucket = None
            logging.debug('Sapling frames in %d ms intervals after %d ms, '
                          'skipping %d frames...', interval,
                          first_change_time + start_ms, skip_frames)
            frame_count = 0
            for frame in frames:
                matches = re.search(match, frame)
                if matches is not None:
                    frame_count += 1
                    frame_time = int(matches.groupdict().get('ms'))
                    frame_bucket = int(math.floor(frame_time / interval))
                    if (frame_time > first_change_time + start_ms and
                            frame_bucket == last_bucket and
                            frame != first_frame and
                            frame != first_change and
                            frame != last_frame and
                            frame_count > skip_frames):
                        logging.debug('Removing sampled frame ' + frame)
                        os.remove(frame)
                    last_bucket = frame_bucket

