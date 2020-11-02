# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Video processing logic"""
import glob
import logging
import math
import os
import re
import subprocess
import sys

VIDEO_SIZE = 400


class VideoProcessing(object):
    """Interface into Chrome's remote dev tools protocol"""
    def __init__(self, options, job, task):
        self.video_path = os.path.join(task['dir'], task['video_subdirectory'])
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
        self.options = options
        self.job = job
        self.task = task

    def process(self):
        """Post Process the video"""
        if os.path.isdir(self.video_path):
            self.cap_frame_count(self.video_path, 50)
            # Crop the video frames
            if not self.options.android and not self.options.iOS and \
                    'mobile' in self.job and self.job['mobile'] and \
                    'crop_pct' in self.task:
                crop = '{0:d}%x{1:d}%+0+0'.format(self.task['crop_pct']['width'],
                                                  self.task['crop_pct']['height'])
                for path in sorted(glob.glob(os.path.join(self.video_path, 'ms_*.jpg'))):
                    command = '{0} -define jpeg:dct-method=fast -crop {1} "{2}"'.format(
                        self.job['image_magick']['mogrify'], crop, path)
                    logging.debug(command)
                    subprocess.call(command, shell=True)
            # Make the initial screen shot the same size as the video
            logging.debug("Resizing initial video frame")
            from PIL import Image
            files = sorted(glob.glob(os.path.join(self.video_path, 'ms_*.jpg')))
            count = len(files)
            width = 0
            height = 0
            if count > 1:
                with Image.open(files[1]) as image:
                    width, height = image.size
                    command = '{0} "{1}" -resize {2:d}x{3:d} "{1}"'.format(
                        self.job['image_magick']['convert'],
                        files[0], width, height)
                    logging.debug(command)
                    subprocess.call(command, shell=True)
            # Eliminate duplicate frames ignoring 25 pixels across the bottom and
            # right sides for status and scroll bars
            crop = None
            if width > 25 and height > 25:
                crop = '{0:d}x{1:d}+0+0'.format(width - 25, height - 25)
            logging.debug("Removing duplicate video frames")
            files = sorted(glob.glob(os.path.join(self.video_path, 'ms_*.jpg')))
            count = len(files)
            if count > 1:
                baseline = files[0]
                for index in range(1, count):
                    if self.frames_match(baseline, files[index], crop, 1, 0):
                        logging.debug('Removing similar frame %s', os.path.basename(files[index]))
                        try:
                            os.remove(files[index])
                        except Exception:
                            pass
                    else:
                        baseline = files[index]
            # Compress to the target quality and size
            for path in sorted(glob.glob(os.path.join(self.video_path, 'ms_*.jpg'))):
                thumb_size = VIDEO_SIZE
                if 'thumbsize' in self.job:
                    try:
                        size = int(self.job['thumbsize'])
                        if size > 0 and size <= 2000:
                            thumb_size = size
                    except Exception:
                        pass
                command = '{0} -define jpeg:dct-method=fast -resize {1:d}x{1:d} '\
                    '-quality {2:d} "{3}"'.format(self.job['image_magick']['mogrify'],
                                                  thumb_size, self.job['imageQuality'], path)
                logging.debug(command)
                subprocess.call(command, shell=True)
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
            progress_file = os.path.join(self.task['dir'], self.task['prefix']) + \
                '_visual_progress.json.gz'
            visualmetrics = os.path.join(self.support_path, "visualmetrics.py")
            args = [sys.executable, visualmetrics, '-d', self.video_path,
                    '--histogram', histograms, '--progress', progress_file]
            if 'renderVideo' in self.job and self.job['renderVideo']:
                video_out = os.path.join(self.task['dir'], self.task['prefix']) + \
                    '_rendered_video.mp4'
                args.extend(['--render', video_out])
            if 'fullSizeVideo' in self.job and self.job['fullSizeVideo']:
                args.append('--full')
            if 'thumbsize' in self.job:
                try:
                    thumbsize = int(self.job['thumbsize'])
                    if thumbsize > 0 and thumbsize <= 2000:
                        args.extend(['--thumbsize', str(thumbsize)])
                except Exception:
                    pass
            subprocess.call(args)

    def frames_match(self, image1, image2, crop_region, fuzz_percent, max_differences):
        """Compare video frames"""
        crop = ''
        if crop_region is not None:
            crop = '-crop {0} '.format(crop_region)
        match = False
        command = '{0} {1} {2} {3}miff:- | {4} -metric AE -'.format(
            self.job['image_magick']['convert'],
            image1, image2, crop,
            self.job['image_magick']['compare'])
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
        frames = sorted(glob.glob(os.path.join(directory, 'ms_*.jpg')))
        frame_count = len(frames)
        if frame_count > maxframes:
            # First pass, sample all video frames after the first 5 seconds
            # at 2fps, keeping the first 40% of the target
            logging.debug('Sampling 2fps: Reducing %d frames to target of %d...',
                          frame_count, maxframes)
            skip_frames = int(maxframes * 0.4)
            self.sample_frames(frames, 500, 5000, skip_frames)
            frames = sorted(glob.glob(os.path.join(directory, 'ms_*.jpg')))
            frame_count = len(frames)
            if frame_count > maxframes:
                # Second pass, sample all video frames after the first 10 seconds
                # at 1fps, keeping the first 60% of the target
                logging.debug('Sampling 1fps: Reducing %d frames to target of %d...',
                              frame_count, maxframes)
                skip_frames = int(maxframes * 0.6)
                self.sample_frames(frames, 1000, 10000, skip_frames)
        frames = sorted(glob.glob(os.path.join(directory, 'ms_*.jpg')))
        frame_count = len(frames)
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
