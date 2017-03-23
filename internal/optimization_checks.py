# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Run the various optimization checks"""
import binascii
import gzip
import logging
import os
import Queue
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
        self.progressive_thread = None
        self.cdn_results = {}
        self.gzip_results = {}
        self.image_results = {}
        self.progressive_results = {}
        self.results = {}
        self.dns_lookup_queue = Queue.Queue()
        self.dns_result_queue = Queue.Queue()
        self.cdn_cnames = {
            'Advanced Hosters CDN': ['.pix-cdn.org'],
            'afxcdn.net': ['.afxcdn.net'],
            'Akamai': ['.akamai.net',
                       '.akamaized.net',
                       '.akamaiedge.net',
                       '.akamaihd.net',
                       '.edgesuite.net',
                       '.edgekey.net',
                       '.srip.net',
                       '.akamaitechnologies.com',
                       '.akamaitechnologies.fr'],
            'Akamai China CDN': ['.tl88.net'],
            'Alimama': ['.gslb.tbcache.com'],
            'Amazon CloudFront': ['.cloudfront.net'],
            'Aryaka': ['.aads1.net',
                       '.aads-cn.net',
                       '.aads-cng.net'],
            'AT&T': ['.att-dsa.net'],
            'Azion': ['.azioncdn.net',
                      '.azioncdn.com',
                      '.azion.net'],
            'Bison Grid': ['.bisongrid.net'],
            'BitGravity': ['.bitgravity.com'],
            'Blue Hat Network': ['.bluehatnetwork.com'],
            'BO.LT': ['bo.lt'],
            'BunnyCDN': ['.b-cdn.net'],
            'Cachefly': ['.cachefly.net'],
            'Caspowa': ['.caspowa.com'],
            'CDN77': ['.cdn77.net',
                      '.cdn77.org'],
            'CDNetworks': ['.cdngc.net',
                           '.gccdn.net',
                           '.panthercdn.com'],
            'CDNsun': ['.cdnsun.net'],
            'ChinaCache': ['.ccgslb.com'],
            'ChinaNetCenter': ['.lxdns.com',
                               '.wscdns.com',
                               '.wscloudcdn.com',
                               '.ourwebpic.com'],
            'Cloudflare': ['.cloudflare.com'],
            'Cotendo CDN': ['.cotcdn.net'],
            'cubeCDN': ['.cubecdn.net'],
            'Edgecast': ['edgecastcdn.net',
                         '.systemcdn.net',
                         '.transactcdn.net',
                         '.v1cdn.net',
                         '.v2cdn.net',
                         '.v3cdn.net',
                         '.v4cdn.net',
                         '.v5cdn.net',],
            'Facebook': ['.facebook.com',
                         '.facebook.net',
                         '.fbcdn.net',
                         '.cdninstagram.com'],
            'Fastly': ['.fastly.net',
                       '.fastlylb.net',
                       '.nocookie.net'],
            'GoCache': ['.cdn.gocache.net'],
            'Google': ['.google.',
                       'googlesyndication.',
                       'youtube.',
                       '.googleusercontent.com',
                       'googlehosted.com',
                       '.gstatic.com',
                       '.doubleclick.net'],
            'HiberniaCDN': ['.hiberniacdn.com'],
            'Highwinds': ['hwcdn.net'],
            'Hosting4CDN': ['.hosting4cdn.com'],
            'Incapsula': ['.incapdns.net'],
            'Instart Logic': ['.insnw.net',
                              '.inscname.net'],
            'Internap': ['.internapcdn.net'],
            'jsDelivr': ['cdn.jsdelivr.net'],
            'KeyCDN': ['.kxcdn.com'],
            'KINX CDN': ['.kinxcdn.com',
                         '.kinxcdn.net'],
            'LeaseWeb CDN': ['.lswcdn.net',
                             '.lswcdn.eu'],
            'Level 3': ['.footprint.net',
                        '.fpbns.net'],
            'Limelight': ['.llnwd.net'],
            'MediaCloud': ['.cdncloud.net.au'],
            'Medianova': ['.mncdn.com',
                          '.mncdn.net',
                          '.mncdn.org'],
            'Microsoft Azure': ['.vo.msecnd.net',
                                '.azureedge.net'],
            'Mirror Image': ['.instacontent.net',
                             '.mirror-image.net'],
            'NetDNA': ['.netdna-cdn.com',
                       '.netdna-ssl.com',
                       '.netdna.com'],
            'Netlify': ['.netlify.com'],
            'NGENIX': ['.ngenix.net'],
            'NYI FTW': ['.nyiftw.net',
                        '.nyiftw.com'],
            'OnApp': ['.r.worldcdn.net',
                      '.r.worldssl.net'],
            'Optimal CDN': ['.optimalcdn.com'],
            'PageRain': ['.pagerain.net'],
            'Rackspace': ['.raxcdn.com'],
            'Reapleaf': ['.rlcdn.com'],
            'Reflected Networks': ['.rncdn1.com'],
            'ReSRC.it': ['.resrc.it'],
            'Rev Software': ['.revcn.net',
                             '.revdn.net'],
            'section.io': ['.squixa.net'],
            'SFR': ['cdn.sfr.net'],
            'Simple CDN': ['.simplecdn.net'],
            'StackPath': ['.stackpathdns.com'],
            'SwiftCDN': ['.swiftcdn1.com'],
            'Taobao': ['.gslb.taobao.com',
                       'tbcdn.cn',
                       '.taobaocdn.com'],
            'Telenor': ['.cdntel.net'],
            'Twitter': ['.twimg.com'],
            'UnicornCDN': ['.unicorncdn.net'],
            'VoxCDN': ['.voxcdn.net'],
            'WordPress': ['.wp.com'],
            'Yahoo': ['.ay1.b.yahoo.com',
                      '.yimg.',
                      '.yahooapis.com'],
            'Yottaa': ['.yottaa.net'],
            'Zenedge': ['.zenedge.net']
        }
        self.cdn_headers = {
            'Airee': [{'Server': 'Airee'}],
            'Amazon CloudFront': [{'Via': 'CloudFront'}],
            'Aryaka': [{'X-Ar-Debug': ''}],
            'BunnyCDN': [{'Server': 'BunnyCDN'}],
            'Caspowa': [{'Server': 'Caspowa'}],
            'CDN': [{'X-Edge-IP': ''},
                    {'X-Edge-Location': ''}],
            'CDNetworks': [{'X-Px': ''}],
            'ChinaNetCenter': [{'X-Cache': 'cache.51cdn.com'}],
            'Cloudflare': [{'Server': 'cloudflare'}],
            'Edgecast': [{'Server': 'ECS'},
                         {'Server': 'ECAcc'},
                         {'Server': 'ECD'}],
            'Fastly': [{'Via': '', 'X-Served-By': 'cache-', 'X-Cache': ''}],
            'GoCache': [{'Server': 'gocache'}],
            'Google': [{'Server': 'sffe'},
                       {'Server': 'gws'},
                       {'Server': 'GSE'},
                       {'Server': 'Golfe2'},
                       {'Via': 'google'}],
            'HiberniaCDN': [{'Server': 'hiberniacdn'}],
            'Highwinds': [{'X-HW': ''}],
            'Incapsula': [{'X-CDN': 'Incapsula'},
                          {'X-Iinfo': ''}],
            'Instart Logic': [{'X-Instart-Request-ID': 'instart'}],
            'LeaseWeb CDN': [{'Server': 'leasewebcdn'}],
            'Medianova': [{'Server': 'MNCDN'}],
            'Naver': [{'Server': 'Testa/'}],
            'NetDNA': [{'Server': 'NetDNA'}],
            'Netlify': [{'Server': 'Netlify'}],
            'NYI FTW': [{'X-Powered-By': 'NYI FTW'},
                        {'X-Delivered-By': 'NYI FTW'}],
            'Optimal CDN': [{'Server': 'Optimal CDN'}],
            'OVH CDN': [{'X-CDN-Geo': ''},
                        {'X-CDN-Pop': ''}],
            'ReSRC.it': [{'Server': 'ReSRC'}],
            'Rev Software': [{'Via': 'Rev-Cache'},
                             {'X-Rev-Cache': ''}],
            'section.io': [{'section-io-id': ''}],
            'Sucuri Firewall': [{'Server': 'Sucuri/Cloudproxy'},
                                {'x-sucuri-id': ''}],
            'Surge': [{'Server': 'SurgeCDN'}],
            'Twitter': [{'Server': 'tsa_b'}],
            'UnicornCDN': [{'Server': 'UnicornCDN'}],
            'Yunjiasu': [{'Server': 'yunjiasu'}],
            'Zenedge': [{'X-Cdn': 'Zenedge'}]
        }

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
            self.progressive_thread = threading.Thread(target=self.check_progressive)
            self.progressive_thread.start()
            # collect the miscellaneous results directly
            self.check_keep_alive()
            self.check_cache_static()

    def join(self):
        """Wait for the optimization checks to complete and record the results"""
        logging.debug('Waiting for progressive JPEG check to complete')
        if self.progressive_thread is not None:
            self.progressive_thread.join()
            self.progressive_thread = None
        logging.debug('Waiting for gzip check to complete')
        if self.gzip_thread is not None:
            self.gzip_thread.join()
            self.gzip_thread = None
        logging.debug('Waiting for image check to complete')
        if self.image_thread is not None:
            self.image_thread.join()
            self.image_thread = None
        logging.debug('Waiting for CDN check to complete')
        if self.cdn_thread is not None:
            self.cdn_thread.join()
            self.cdn_thread = None
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
        for request_id in self.progressive_results:
            if request_id not in self.results:
                self.results[request_id] = {}
            self.results[request_id]['progressive'] = self.progressive_results[request_id]
        # Save the results
        if self.results:
            path = os.path.join(self.task['dir'], self.task['prefix']) + '_optimization.json.gz'
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

    def get_time_remaining(self, request):
        """See if a request is static and how long it can be cached for"""
        from email.utils import parsedate
        re_max_age = re.compile(r'max-age[ ]*=[ ]*(?P<maxage>[\d]+)')
        is_static = False
        time_remaining = -1
        if 'response_headers' in request:
            content_length = self.get_header_value(request['response_headers'], 'Content-Length')
            if content_length is not None:
                content_length = int(content_length)
                if content_length == 0:
                    return is_static, time_remaining
            if 'response_headers' in request:
                content_type = self.get_header_value(request['response_headers'], 'Content-Type')
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
        return is_static, time_remaining

    def check_cache_static(self):
        """Check static resources for how long they are cacheable for"""
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                check = {'score': -1, 'time': 0}
                is_static, time_remaining = self.get_time_remaining(request)
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
        from urlparse import urlparse
        # First pass, build a list of domains and see if the headers or domain matches
        static_requests = {}
        domains = {}
        for request_id in self.requests:
            request = self.requests[request_id]
            is_static, _ = self.get_time_remaining(request)
            if is_static:
                static_requests[request_id] = True
                if 'url' in request:
                    domain = urlparse(request['url']).hostname
                    if domain is not None:
                        if domain not in domains:
                            # Check the domain itself against the CDN list
                            domains[domain] = ''
                            provider = self.check_cdn_name(domain)
                            if provider is not None:
                                domains[domain] = provider
                        if not len(domains[domain]) and 'response_headers' in request:
                            # Check the headers on the current response_headers
                            provider = self.check_cdn_headers(request['response_headers'])
                            if provider is not None:
                                domains[domain] = provider
        # Spawn several workers to do CNAME lookups for the unknown domains
        count = 0
        for domain in domains:
            if not len(domains[domain]):
                count += 1
                self.dns_lookup_queue.put(domain)
        if count:
            thread_count = min(10, count)
            threads = []
            for _ in xrange(thread_count):
                thread = threading.Thread(target=self.dns_worker)
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()
            try:
                while True:
                    dns_result = self.dns_result_queue.get_nowait()
                    domains[dns_result['domain']] = dns_result['provider']
            except Exception:
                pass
        # Final pass, populate the CDN infor for each request
        for request_id in self.requests:
            if request_id in static_requests:
                request = self.requests[request_id]
                if 'url' in request:
                    check = {'score': 0, 'provider': ''}
                    domain = urlparse(request['url']).hostname
                    if domain is not None:
                        if domain in domains and len(domains[domain]):
                            check['score'] = 100
                            check['provider'] = domains[domain]
                    self.cdn_results[request_id] = check

    def dns_worker(self):
        """Handle the DNS CNAME lookups and checking in multiple threads"""
        import dns.resolver
        try:
            while True:
                domain = self.dns_lookup_queue.get_nowait()
                try:
                    answers = dns.resolver.query(domain, 'CNAME')
                    if answers and len(answers):
                        for rdata in answers:
                            name = '.'.join(rdata.target).strip(' .')
                            provider = self.check_cdn_name(name)
                            if provider is not None:
                                self.dns_result_queue.put({'domain': domain, 'provider': provider})
                                break
                except Exception:
                    pass
        except Exception:
            pass

    def check_cdn_name(self, domain):
        """Check the given domain against our cname list"""
        if domain is not None and len(domain):
            check_name = domain.lower()
            for cdn in self.cdn_cnames:
                for cname in self.cdn_cnames[cdn]:
                    if check_name.find(cname) > -1:
                        return cdn
        return None

    def check_cdn_headers(self, headers):
        """Check the given headers against our header list"""
        for cdn in self.cdn_headers:
            for header_group in self.cdn_headers[cdn]:
                all_match = True
                for name in header_group:
                    value = self.get_header_value(headers, name)
                    if value is None:
                        all_match = False
                        break
                    else:
                        value = value.lower()
                        check = header_group[name].lower()
                        if len(check) and value.find(check) == -1:
                            all_match = False
                            break
                if all_match:
                    return cdn
        return None

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

    def check_progressive(self):
        """Count the number of scan lines in each jpeg"""
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                if 'body' in request:
                    sniff_type = self.sniff_content(request['body'])
                    if sniff_type == 'jpeg':
                        content_length = os.path.getsize(request['body'])
                        check = {'size': content_length, 'scan_count': 0}
                        with open(request['body'], 'rb') as jpeg:
                            try:
                                while True:
                                    block = struct.unpack('B', jpeg.read(1))[0]
                                    if block != 0xff:
                                        break
                                    block = struct.unpack('B', jpeg.read(1))[0]
                                    while block == 0xff:
                                        block = struct.unpack('B', jpeg.read(1))[0]
                                    if block == 0x01 or (block >= 0xd0 and block <= 0xd9):
                                        continue
                                    elif block == 0xda: # Image data
                                        check['scan_count'] += 1
                                        # Seek to the next non-padded 0xff to find the next marker
                                        found = False
                                        while not found:
                                            value = struct.unpack('B', jpeg.read(1))[0]
                                            if value == 0xff:
                                                value = struct.unpack('B', jpeg.read(1))[0]
                                                if value != 0x00:
                                                    found = True
                                                    jpeg.seek(-2, 1)
                                    else:
                                        block_size = struct.unpack('2B', jpeg.read(2))
                                        block_size = block_size[0] * 256 + block_size[1] - 2
                                        jpeg.seek(block_size, 1)
                            except Exception:
                                pass
                        self.progressive_results[request_id] = check
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
