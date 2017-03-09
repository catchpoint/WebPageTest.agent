# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Run the various optimization checks"""
import binascii
import gzip
import os
import shutil
import threading
import ujson as json

class OptimizationChecks(object):
    """Threaded optimization checks"""
    def __init__(self, job, task, requests):
        self.job = job
        self.task = task
        self.requests = requests
        self.cdn_thread = None
        self.gzip_thread = None
        self.image_thread = None
        self.cdn_results = {}
        self.gzip_results = {}
        self.image_results = {}
        self.results = {}

    def start(self):
        """Start running the optimization checks"""
        if self.requests is not None:
            # Run the slow checks in background threads
            self.cdn_thread = threading.Thread(target=self.check_cdn)
            self.cdn_thread.start()
            self.gzip_thread = threading.Thread(target=self.check_gzip)
            self.gzip_thread.start()
            self.image_thread = threading.Thread(target=self.check_images)
            self.image_thread.start()
            # collect the miscellaneous results directly
            self.check_misc()

    def join(self):
        """Wait for the optimization checks to complete and record the results"""
        if self.cdn_thread is not None:
            self.cdn_thread.join()
            self.cdn_thread = None
        if self.gzip_thread is not None:
            self.gzip_thread.join()
            self.gzip_thread = None
        if self.image_thread is not None:
            self.image_thread.join()
            self.image_thread = None
        # Merge the results together
        for request_id in self.cdn_results:
            if request_id not in self.results:
                self.results[request_id] = {}
            self.results[request_id]['cdn'] = self.cdn_results[request_id]
        for request_id in self.gzip_results:
            if request_id not in self.results:
                self.results[request_id] = {}
            self.results[request_id]['gzip'] = self.gzip_results[request_id]
        for request_id in self.image_results:
            if request_id not in self.results:
                self.results[request_id] = {}
            self.results[request_id]['image'] = self.image_results[request_id]
        # Save the results
        if self.results:
            path = os.path.join(self.task['dir'], self.task['prefix']) + 'optimization.json.gz'
            gz_file = gzip.open(path, 'wb')
            if gz_file:
                gz_file.write(json.dumps(self.results))
                gz_file.close()

    def check_cdn(self):
        """Check each request to see if it was served from a CDN"""

    def check_gzip(self):
        """Check each request to see if it can be compressed"""
        for request_id in self.requests:
            request = self.requests[request_id]
            content_length = self.get_header_value(request['response_headers'], 'Content-Length')
            if content_length is not None:
                content_length = int(content_length)
            elif 'transfer_size' in request:
                content_length = request['transfer_size']
            check = {'score': 0, 'size': content_length, 'target_size': content_length}
            encoding = None
            if 'response_headers' in request:
                encoding = self.get_header_value(request['response_headers'], 'Content-Encoding')
            # Check for responses that are already compressed (ignore the level)
            if encoding is not None:
                if encoding.find('gzip') >= 0 or \
                        encoding.find('deflate') >= 0 or \
                        encoding.find('br') >= 0:
                    check['score'] = 100
            # Ignore small responses that will fit in a packet
            if not check['score'] and content_length < 1400:
                check['score'] = -1
            # Try compressing it if it isn't an image
            if not check['score'] and 'body' in request:
                sniff_type = self.sniff_content(request['body'])
                if sniff_type is not None:
                    check['score'] = -1
                else:
                    out_file = request['body'] + '.gzip'
                    with open(request['body'], 'rb') as f_in:
                        with gzip.open(out_file, 'wb', 7) as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    if os.path.isfile(out_file):
                        target_size = os.path.getsize(out_file)
                        delta = content_length - target_size
                        # Only count it if there is at least 1 packet and 10% savings
                        if target_size > 0 and \
                                delta > 1400 and \
                                target_size < (content_length * 0.9):
                            check['target_size'] = target_size
                            check['score'] = int(target_size * 100 / content_length)
                        else:
                            check['score'] = -1
                    else:
                        check['score'] = -1
            self.gzip_results[request_id] = check

    def check_images(self):
        """Check each request to see if images can be compressed better"""

    def check_misc(self):
        """Check each request to see if various other optimizations can be done"""

    def get_header_value(self, headers, name):
        """Get the value for the requested header"""
        value = None
        if headers:
            if name in headers:
                value = headers[name]
            else:
                find = name.lower()
                for header_name in headers:
                    check = header_name.lower()
                    if check == find or (check[0] == ':' and check[1:] == find):
                        value = headers[header_name]
                        break
        return value

    def sniff_content(self, image_file):
        """Check the beginning of the file to see if it is a known image type"""
        content_type = None
        with open(image_file, 'rb') as f_in:
            raw = f_in.read(14)
            hex_bytes = binascii.hexlify(raw).lower()
            if hex_bytes[0:6] == 'ffd8ff':
                content_type = 'jpeg'
            elif hex_bytes[0:16] == '89504e470d0a1a0a':
                content_type = 'png'
            elif hex_bytes[0:8] == '47494638' and hex_bytes[10:12] == '61':
                content_type = 'gif'
            elif raw[:4] == 'RIFF' and raw[8:14] == 'WEBPVP':
                content_type = 'webp'
            elif raw[:4] == 'wOF2':
                content_type = 'WOFF2'
        return content_type
