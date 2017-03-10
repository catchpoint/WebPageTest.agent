# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Run the various optimization checks"""
import binascii
import gzip
import os
import re
import shutil
import struct
import subprocess
import threading
import time
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
            self.check_keep_alive()
            self.check_cache_static()

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

    def check_keep_alive(self):
        """Check for requests where the connection is force-closed"""
        from urlparse import urlparse
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                if 'url' in request:
                    check = {'score': 100}
                    domain = urlparse(request['url']).hostname
                    # See if there are any other requests on the same domain
                    other_requests = False
                    for r_id in self.requests:
                        if r_id != request_id:
                            if 'url' in self.requests[r_id]:
                                other_domain = urlparse(self.requests[r_id]['url']).hostname
                                if other_domain == domain:
                                    other_requests = True
                                    break
                    if other_requests:
                        check['score'] = 100
                        keep_alive = self.get_header_value(request['response_headers'],
                                                           'Connection')
                        if keep_alive is not None and keep_alive.lower().strip().find('close') > -1:
                            check['score'] = 0
                    if request_id not in self.results:
                        self.results[request_id] = {}
                    self.results[request_id]['keep_alive'] = check
            except Exception:
                pass

    def check_cache_static(self):
        """Check static resources for how long they are cacheable for"""
        from email.utils import parsedate
        re_max_age = re.compile(r'max-age[ ]*=[ ]*(?P<maxage>[\d]+)')
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                check = {'score': -1, 'time': 0}
                content_type = self.get_header_value(request['response_headers'],
                                                     'Content-Type')
                if content_type is None or \
                    (content_type.find('/html') == -1 and \
                     content_type.find('/cache-manifest') == -1):
                    is_static = True
                    cache = self.get_header_value(request['response_headers'], 'Cache-Control')
                    pragma = self.get_header_value(request['response_headers'], 'Pragma')
                    expires = self.get_header_value(request['response_headers'], 'Expires')
                    if cache is not None:
                        cache = cache.lower()
                        if cache.find('no-store') > -1 or cache.find('no-cache') > -1:
                            is_static = False
                    if is_static and pragma is not None:
                        pragma = pragma.lower()
                        if pragma.find('no-cache') > -1:
                            is_static = False
                    if is_static:
                        time_remaining = 0
                        if cache is not None:
                            matches = re.search(re_max_age, cache)
                            if matches:
                                time_remaining = int(matches.groupdict().get('maxage'))
                                age = self.get_header_value(request['response_headers'], 'Age')
                                if age is not None:
                                    time_remaining -= int(age.strip())
                        elif expires is not None:
                            date = self.get_header_value(request['response_headers'], 'Date')
                            exp = time.mktime(parsedate(expires))
                            if date is not None:
                                now = time.mktime(parsedate(date))
                            else:
                                now = time.time()
                            time_remaining = int(exp - now)
                            if time_remaining < 0:
                                is_static = False
                        if is_static:
                            check['time'] = time_remaining
                            if time_remaining > 604800: # 7 days
                                check['score'] = 100
                            elif time_remaining > 3600: # 1 hour
                                check['score'] = 50
                            else:
                                check['score'] = 0
                if check['score'] >= 0:
                    if request_id not in self.results:
                        self.results[request_id] = {}
                    self.results[request_id]['cache'] = check
            except Exception:
                pass


    def check_cdn(self):
        """Check each request to see if it was served from a CDN"""

    def check_gzip(self):
        """Check each request to see if it can be compressed"""
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                content_length = self.get_header_value(request['response_headers'],
                                                       'Content-Length')
                if content_length is not None:
                    content_length = int(content_length)
                elif 'transfer_size' in request:
                    content_length = request['transfer_size']
                check = {'score': 0, 'size': content_length, 'target_size': content_length}
                encoding = None
                if 'response_headers' in request:
                    encoding = self.get_header_value(request['response_headers'],
                                                     'Content-Encoding')
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
                            try:
                                os.remove(out_file)
                            except Exception:
                                pass
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
                if check['score'] >= 0:
                    self.gzip_results[request_id] = check
            except Exception:
                pass

    def check_images(self):
        """Check each request to see if images can be compressed better"""
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                content_length = self.get_header_value(request['response_headers'],
                                                       'Content-Length')
                if content_length is not None:
                    content_length = int(content_length)
                elif 'transfer_size' in request:
                    content_length = request['transfer_size']
                check = {'score': -1, 'size': content_length, 'target_size': content_length}
                if content_length and 'body' in request:
                    sniff_type = self.sniff_content(request['body'])
                    if sniff_type == 'jpeg':
                        if content_length < 1400:
                            check['score'] = 100
                        else:
                            # Compress it as a quality 85 stripped progressive image and compare
                            jpeg_file = request['body'] + '.jpg'
                            command = 'convert -strip -interlace Plane -quality 85 '\
                                '"{0}" "{1}"'.format(request['body'], jpeg_file)
                            subprocess.call(command, shell=True)
                            if os.path.isfile(jpeg_file):
                                target_size = os.path.getsize(jpeg_file)
                                try:
                                    os.remove(jpeg_file)
                                except Exception:
                                    pass
                                delta = content_length - target_size
                                # Only count it if there is at least 1 packet savings
                                if target_size > 0 and delta > 1400:
                                    check['target_size'] = target_size
                                    check['score'] = int(target_size * 100 / content_length)
                                else:
                                    check['score'] = 100
                    elif sniff_type == 'png':
                        if content_length < 1400:
                            check['score'] = 100
                        else:
                            image_chunks = ["iCCP", "tIME", "gAMA", "PLTE", "acTL", "IHDR", "cHRM",
                                            "bKGD", "tRNS", "sBIT", "sRGB", "pHYs", "hIST", "vpAg",
                                            "oFFs", "fcTL", "fdAT", "IDAT"]
                            file_size = os.path.getsize(request['body'])
                            with open(request['body']) as image:
                                valid = True
                                target_size = 8
                                bytes_remaining = file_size - 8
                                image.seek(8, 0)
                                while valid and bytes_remaining >= 4:
                                    chunk_len = struct.unpack('>I', image.read(4))[0]
                                    if chunk_len + 12 <= bytes_remaining:
                                        chunk_type = image.read(4)
                                        if chunk_type in image_chunks:
                                            target_size += chunk_len + 12
                                        image.seek(chunk_len + 4, 1) # Skip the data and CRC
                                        bytes_remaining -= chunk_len + 12
                                    else:
                                        valid = False
                                        bytes_remaining = 0
                                if valid:
                                    delta = content_length - target_size
                                    # Only count it if there is at least 1 packet savings
                                    if target_size > 0 and delta > 1400:
                                        check['target_size'] = target_size
                                        check['score'] = int(target_size * 100 / content_length)
                                    else:
                                        check['score'] = 100
                    elif sniff_type == 'gif':
                        if content_length < 1400:
                            check['score'] = 100
                        else:
                            is_animated = False
                            from PIL import Image
                            with Image.open(request['body']) as gif:
                                try:
                                    gif.seek(1)
                                except EOFError:
                                    is_animated = False
                                else:
                                    is_animated = True
                            if is_animated:
                                check['score'] = 100
                            else:
                                # Convert it to a PNG
                                png_file = request['body'] + '.png'
                                command = 'convert "{0}" "{1}"'.format(request['body'], png_file)
                                subprocess.call(command, shell=True)
                                if os.path.isfile(png_file):
                                    target_size = os.path.getsize(png_file)
                                    try:
                                        os.remove(png_file)
                                    except Exception:
                                        pass
                                    delta = content_length - target_size
                                    # Only count it if there is at least 1 packet savings
                                    if target_size > 0 and delta > 1400:
                                        check['target_size'] = target_size
                                        check['score'] = int(target_size * 100 / content_length)
                                    else:
                                        check['score'] = 100
                    elif sniff_type == 'webp':
                        check['score'] = 100
                    if check['score'] >= 0:
                        self.image_results[request_id] = check
            except Exception:
                pass

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
            elif raw[:6] == 'GIF87a' or raw[:6] == 'GIF89a':
                content_type = 'gif'
            elif raw[:4] == 'RIFF' and raw[8:14] == 'WEBPVP':
                content_type = 'webp'
            elif raw[:4] == 'wOF2':
                content_type = 'WOFF2'
        return content_type
