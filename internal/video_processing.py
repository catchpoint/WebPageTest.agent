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
import threading

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
        if os.path.isdir(self.video_path):
            # Make the initial screen shot the same size as the video
            logging.debug("Resizing initial video frame")
            from PIL import Image
            files = sorted(glob.glob(os.path.join(self.video_path, 'ms_*.png')))
            count = len(files)
            width = 0
            height = 0
            if count > 1:
                with Image.open(files[1]) as image:
                    width, height = image.size
                    command = 'convert "{0}" -resize {1:d}x{2:d} "{0}"'.format(
                        files[0], width, height)
                    logging.debug(command)
                    subprocess.call(command, shell=True)
            # Eliminate duplicate frames ignoring 25 pixels across the bottom and
            # right sides for status and scroll bars
            crop = None
            if width > 25 and height > 25:
                crop = '{0:d}x{1:d}+0+0'.format(width - 25, height - 25)
            logging.debug("Removing duplicate video frames")
            self.cap_frame_count(self.video_path, 50)
            files = sorted(glob.glob(os.path.join(self.video_path, 'ms_*.png')))
            count = len(files)
            if count > 1:
                baseline = files[0]
                for index in xrange(1, count):
                    if self.frames_match(baseline, files[index], crop, 1, 0):
                        logging.debug('Removing similar frame %s', os.path.basename(files[index]))
                        try:
                            os.remove(files[index])
                        except Exception:
                            pass
                    else:
                        baseline = files[index]
            # start a background thread to convert the images to jpeg
            logging.debug("Converting video frames to jpeg")
            jpeg_thread = threading.Thread(target=self.convert_to_jpeg)
            jpeg_thread.start()
            # Run visualmetrics against them
            logging.debug("Processing video frames")
            if self.task['current_step'] == 1:
                filename = '{0:d}.{1:d}.histograms.json.gz'.format(self.task['run'],
                                                                   self.task['cached'])
            else:
                filename = '{0:d}.{1:d}.{2:d}.histograms.json.gz'.format(self.task['run'],
                                                                         self.task['cached'],
                                                                         self.task['current_step'])
            histograms = os.path.join(self.task['dir'], filename)
            visualmetrics = os.path.join(self.support_path, "visualmetrics.py")
            subprocess.call(['python', visualmetrics, '-d', self.video_path,
                             '--histogram', histograms, '-vvvv'])
            # Wait for the jpeg task to complete and delete the png's
            logging.debug("Waiting for jpeg conversion to finish")
            jpeg_thread.join()
            for filepath in sorted(glob.glob(os.path.join(self.video_path, 'ms_*.png'))):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

    def convert_to_jpeg(self):
        """Convert all of the pngs in the given directory to jpeg"""
        for src in sorted(glob.glob(os.path.join(self.video_path, 'ms_*.png'))):
            dst = os.path.splitext(src)[0] + '.jpg'
            command = 'convert "{0}" -resize {1:d}x{1:d} -quality {2:d} "{3}"'.format(
                src, VIDEO_SIZE, self.job['iq'], dst)
            logging.debug(command)
            subprocess.call(command, shell=True)

    def frames_match(self, image1, image2, crop_region, fuzz_percent, max_differences):
        """Compare video frames"""
        crop = ''
        if crop_region is not None:
            crop = '-crop {0} '.format(crop_region)
        match = False
        command = 'convert {0} {1} {2}miff:- | compare -metric AE -'.format(image1, image2, crop)
        if fuzz_percent > 0:
            command += ' -fuzz {0:d}%'.format(fuzz_percent)
        command += ' null:'.format()
        compare = subprocess.Popen(command, stderr=subprocess.PIPE, shell=True)
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
