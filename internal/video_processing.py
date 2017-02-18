# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Video processing logic"""
import glob
import logging
import math
import os
import re
import subprocess

VIDEO_SIZE = 400

class VideoProcessing(object):
    """Interface into Chrome's remote dev tools protocol"""
    def __init__(self, job, task):
        self.video_path = os.path.join(task['dir'], task['video_subdirectory'])
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
        self.job = job
        self.task = task

    def process(self):
        """Post Process the video"""
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
