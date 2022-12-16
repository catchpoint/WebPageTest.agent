# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Run the various optimization checks"""
import binascii
import gzip
import logging
import multiprocessing
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
if (sys.version_info >= (3, 0)):
    from time import monotonic
    GZIP_TEXT = 'wt'
    unicode = str
else:
    from monotonic import monotonic
    GZIP_TEXT = 'w'
try:
    import ujson as json
except BaseException:
    import json


class OptimizationChecks(object):
    """Threaded optimization checks"""
    def __init__(self, job, task, requests):
        self.job = job
        self.task = task
        self.running_checks = False
        self.requests = requests
        self.cdn_thread = None
        self.hosting_thread = None
        self.gzip_thread = None
        self.image_thread = None
        self.progressive_thread = None
        self.font_thread = None
        self.wasm_thread = None
        self.cdn_time = None
        self.hosting_time = None
        self.gzip_time = None
        self.image_time = None
        self.progressive_time = None
        self.font_time = None
        self.wasm_time = None
        self.cdn_results = {}
        self.hosting_results = {}
        self.gzip_results = {}
        self.image_results = {}
        self.progressive_results = {}
        self.font_results = {}
        self.wasm_results = {}
        self.results = {}
        self.dns_lookup_queue = multiprocessing.JoinableQueue()
        self.dns_result_queue = multiprocessing.JoinableQueue()
        self.fetch_queue = multiprocessing.JoinableQueue()
        self.fetch_result_queue = multiprocessing.JoinableQueue()
        # spell-checker: disable
        self.cdn_cnames = {
            'Advanced Hosters CDN': ['.pix-cdn.org'],
            'afxcdn.net': ['.afxcdn.net'],
            'Akamai': ['.akamai.net',
                       '.akamaized.net',
                       '.akamaized-staging.net',
                       '.akamaiedge.net',
                       '.akamaiedge-staging.net',
                       '.akamaihd.net',
                       '.edgesuite.net',
                       '.edgesuite-staging.net',
                       '.edgekey.net',
                       '.edgekey-staging.net',
                       '.srip.net',
                       '.akamaitechnologies.com',
                       '.akamaitechnologies.fr'],
            'Akamai China CDN': ['.tl88.net'],
            'Alibaba':['a.lahuashanbx.com',
                       'cdn.gl102.com',
                       '.alicdn.com',
                       'danuoyi.tbcache.com',
                       'gl102.com',
                       'kunlundns.com',
                       'm.alikunlun.com',
                       'm.alikunlun.net',
                       'm.cdngslb.com',
                       'm.kunlunaq.com',
                       'm.kunlunAr.com',
                       'm.kunlunCa.com',
                       'm.kunlunCan.com',
                       'm.kunlunea.com',
                       'm.kunlungem.com',
                       'm.kunlungr.com',
                       'm.kunlunhuf.com',
                       'm.kunlunle.com',
                       'm.kunlunLi.com',
                       'm.kunlunno.com',
                       'm.kunlunpi.com',
                       'm.kunlunra.com',
                       'm.kunlunSa.com',
                       'm.kunlunSc.com',
                       'm.kunlunsl.com',
                       'm.kunlunso.com',
                       'm.kunlunTa.com',
                       'm.kunlunVi.com',
                       'm.kunlunwe.com',
                       'mobgslb.tbcache.com',
                       'w.alikunlun.com',
                       'w.alikunlun.net',
                       'w.cdngslb.com',
                       'w.kunlunaq.com',
                       'w.kunlunAr.com',
                       'w.kunlunCa.com',
                       'w.kunlunCan.com',
                       'w.kunlunea.com',
                       'w.kunlungem.com',
                       'w.kunlungr.com',
                       'w.kunlunhuf.com',
                       'w.kunlunle.com',
                       'w.kunlunLi.com',
                       'w.kunlunno.com',
                       'w.kunlunpi.com',
                       'w.kunlunra.com',
                       'w.kunlunSa.com',
                       'w.kunlunSc.com',
                       'w.kunlunsl.com',
                       'w.kunlunso.com',
                       'w.kunlunTa.com',
                       'w.kunlunVi.com',
                       'w.kunlunwe.com',
                       'w.queniucdn.com',
                       'w.queniucg.com',
                       'w.queniueh.com',
                       'w.queniuei.com',
                       'w.queniufz.com',
                       'w.queniugslb.com',
                       'w.queniuhx.com',
                       'w.queniujd.com',
                       'w.queniujg.com',
                       'w.queniunh.com',
                       'w.queniunz.com',
                       'w.queniurv.com',
                       'w.queniuso.com',
                       'w.queniusp.com',
                       'w.queniusy.com',
                       'w.queniutt.com',
                       'w.queniuuf.com',
                       'w.queniuuq.com',
                       'w.queniuyk.com'],
            'Alimama': ['.gslb.tbcache.com'],
            'Amazon CloudFront': ['.cloudfront.net'],
            'ArvanCloud': ['.arvancloud.com'],
            'Aryaka': ['.aads1.net',
                       '.aads-cn.net',
                       '.aads-cng.net'],
            'AT&T': ['.att-dsa.net'],
            'Automattic': ['.wp.com',
                           '.wordpress.com',
                           '.gravatar.com'],
            'Azion': ['.azioncdn.net',
                      '.azioncdn.com',
                      '.azion.net',
                      '.azionedge.net'],
            'Baleen': ['.baleen.cshield.net'],
            'BelugaCDN': ['.belugacdn.com',
                          '.belugacdn.link'],
            'Bison Grid': ['.bisongrid.net'],
            'BitGravity': ['.bitgravity.com'],
            'Blue Hat Network': ['.bluehatnetwork.com'],
            'BO.LT': ['bo.lt'],
            'BunnyCDN': ['.b-cdn.net'],
            'Cachefly': ['.cachefly.net'],
            'Caspowa': ['.caspowa.com'],
            'Cedexis': ['.cedexis.net'],
            'CDN77': ['.cdn77.net',
                      '.cdn77.org'],
            'CDNetworks': ['.cdngc.net',
                           '.gccdn.net',
                           '.panthercdn.com'],
            'CDNsun': ['.cdnsun.net'],
            'CDNvideo': ['.cdnvideo.ru',
                         '.cdnvideo.net'],
            'ChinaCache': ['.ccgslb.com'],
            'ChinaNetCenter': ['.lxdns.com',
                               '.wscdns.com',
                               '.wscloudcdn.com',
                               '.ourwebpic.com'],
            'Cloudflare': ['.cloudflare.com',
                           '.cloudflare.net'],
            'Cotendo CDN': ['.cotcdn.net'],
            'cubeCDN': ['.cubecdn.net'],
            'DigitalOcean Spaces CDN': ['.cdn.digitaloceanspaces.com'],
            'Edgecast': ['edgecastcdn.net',
                         '.systemcdn.net',
                         '.transactcdn.net',
                         '.v1cdn.net',
                         '.v2cdn.net',
                         '.v3cdn.net',
                         '.v4cdn.net',
                         '.v5cdn.net'],
            'Erstream': ['.ercdn.net',
                         'ercdn.com'],
            'Facebook': ['.facebook.com',
                         '.facebook.net',
                         '.fbcdn.net',
                         '.cdninstagram.com'],
            'Fastly': ['.fastly.net',
                       '.fastlylb.net',
                       '.nocookie.net'],
            'GoCache': ['.cdn.gocache.net'],
            'G-Core CDN': ['.gcdn.co'],
            'Google': ['.google.',
                       'googlesyndication.',
                       'youtube.',
                       '.googleusercontent.com',
                       'googlehosted.com',
                       'googletagmanager.com',
                       'googleadservices.com',
                       '.gstatic.com',
                       '.googleapis.com',
                       '.doubleclick.net'],
            'HiberniaCDN': ['.hiberniacdn.com'],
            'Highwinds': ['hwcdn.net'],
            'Hosting4CDN': ['.hosting4cdn.com'],
            'HyosungITX': ['.gtmc.hscdn.com'],
            'ImageEngine': ['.imgeng.in'],
            'Incapsula': ['.incapdns.net'],
            'Instart Logic': ['.insnw.net',
                              '.inscname.net'],
            'Internap': ['.internapcdn.net'],
            'jsDelivr': ['cdn.jsdelivr.net'],
            'JuraganCDN': ['.b.juragancdn.com',
                          'juragancdn.com'],
            'KeyCDN': ['.kxcdn.com'],
            'KINX CDN': ['.kinxcdn.com',
                         '.kinxcdn.net'],
            'LeaseWeb CDN': ['.lswcdn.net',
                             '.lswcdn.eu'],
            'Level 3': ['.footprint.net',
                        '.fpbns.net'],
            'Limelight': ['.llnwd.net',
                          '.llnw.net',
                          '.llnwi.net',
                          '.lldns.net'],
            'MediaCloud': ['.cdncloud.net.au'],
            'Medianova': ['.mncdn.com',
                          '.mncdn.net',
                          '.mncdn.org'],
            'MerlinCDN': ['.merlincdn.net'],
            'Microsoft Azure': ['.vo.msecnd.net',
                                '.azureedge.net',
                                '.azurefd.net',
                                '.azure.microsoft.com',
                                '-msedge.net'],
            'Mirror Image': ['.instacontent.net',
                             '.mirror-image.net'],
            'NetDNA': ['.netdna-cdn.com',
                       '.netdna-ssl.com',
                       '.netdna.com'],
            'Netlify': ['.netlify.com'],
            'Nexcess CDN': ['.nxedge.io',
                        '.nexcesscdn.net'],
            'NGENIX': ['.ngenix.net'],
            'NYI FTW': ['.nyiftw.net',
                        '.nyiftw.com'],
            'OnApp': ['.r.worldcdn.net',
                      '.r.worldssl.net'],
            'Optimal CDN': ['.optimalcdn.com'],
            'PageCDN': ['pagecdn.io'],
            'PageRain': ['.pagerain.net'],
            'Parspack CDN': ['.parspack.net'],
            'Pressable CDN': ['.pressablecdn.com'],
            'PUSHR': ['.pushrcdn.com'],
            'Rackspace': ['.raxcdn.com'],
            'Reapleaf': ['.rlcdn.com'],
            'Reflected Networks': ['.rncdn1.com',
                                   '.rncdn7.com'],
            'ReSRC.it': ['.resrc.it'],
            'Rev Software': ['.revcn.net',
                             '.revdn.net'],
            'Roast.io': ['.roast.io'],
            'Rocket CDN': ['.streamprovider.net'],
            'section.io': ['.section.io'],
            'SFR': ['cdn.sfr.net'],
            'Shift8 CDN': ['.shift8cdn.com'],
            'Simple CDN': ['.simplecdn.net'],
            'Singular CDN': ['.singularcdn.net.br'],
            'Sirv CDN': ['.sirv.com'],
            'StackPath': ['.stackpathdns.com'],
            'SwiftCDN': ['.swiftcdn1.com',
                         '.swiftserve.com'],
            'SwiftyCDN': ['.swiftycdn.net'],
            'Taobao': ['.gslb.taobao.com',
                       'tbcdn.cn',
                       '.taobaocdn.com'],
            'Telenor': ['.cdntel.net'],
            'Tencent': ['.cdn.dnsv1.com',
                        '.cdn.dnsv1.com.cn',
                        '.dsa.dnsv1.com',
                        '.dsa.dnsv1.com.cn'],
            'TRBCDN': ['.trbcdn.net'],
            'Twitter': ['.twimg.com'],
            'UnicornCDN': ['.unicorncdn.net'],
            'Universal CDN': ['.cdn12.com',
                              '.cdn13.com',
                              '.cdn15.com'],
            'VegaCDN': ['.vegacdn.vn',
                        '.vegacdn.com'],
            'Vercel': ['.vercel.com',
                       '.zeit.co'],
            'VoxCDN': ['.voxcdn.net'],
            'WP Compress': ['.zapwp.com'],
            'XLabs Security': ['.xlabs.com.br',
                               '.armor.zone'],
            'Yahoo': ['.ay1.b.yahoo.com',
                      '.yimg.',
                      '.yahooapis.com',
                      'cdn.vidible.tv',
                      'cdn-ssl.vidible.tv'],
            'Yottaa': ['.yottaa.net'],
            'Zenedge': ['.zenedge.net']
        }
        self.cdn_headers = {
            'Airee': [{'Server': 'Airee'}],
            'Akamai': [{'x-akamai-staging': 'ESSL'},
                       {'x-akamai-request-id': ''}],
            'Amazon CloudFront': [{'Via': 'CloudFront'}],
            'Aryaka': [{'X-Ar-Debug': ''}],
            'Azion' : [{'Server' : 'Azion Technologies'}],
            'Baleen': [{'bln-version': ''}],
            'BelugaCDN': [{'Server': 'Beluga'},
                          {'X-Beluga-Cache-Status': ''}],
            'BunnyCDN': [{'Server': 'BunnyCDN'}],
            'Caspowa': [{'Server': 'Caspowa'}],
            'CDN': [{'X-Edge-IP': ''},
                    {'X-Edge-Location': ''}],
            'CDN77': [{'Server': 'CDN77'}],
            'CDNetworks': [{'X-Px': ''}],
            'ChinaNetCenter': [{'X-Cache': 'cache.51cdn.com'}],
            'Cloudflare': [{'Server': 'cloudflare'}],
            'Edgecast': [{'Server': 'ECS'},
                         {'Server': 'ECAcc'},
                         {'Server': 'ECD'}],
            'Erstream': [{'Server': 'ersRV'}],
            'Fastly': [{'X-Served-By': 'cache-', 'X-Cache': ''},
                       {'Server-Timing': 'fastly'}],
            'Fly': [{'Server': 'Fly.io'}],
            'GoCache': [{'Server': 'gocache'}],
            'Google': [{'Server': 'sffe'},
                       {'Server': 'gws'},
                       {'Server': 'ESF'},
                       {'Server': 'GSE'},
                       {'Server': 'Golfe2'},
                       {'Via': 'google'}],
            'HiberniaCDN': [{'Server': 'hiberniacdn'}],
            'Highwinds': [{'X-HW': ''}],
            'Hosting4CDN': [{'x-cdn': 'H4CDN'}],
            'ImageEngine': [{'Server': 'ScientiaMobile ImageEngine'}],
            'Incapsula': [{'X-CDN': 'Incapsula'},
                          {'X-Iinfo': ''}],
            'Instart Logic': [{'X-Instart-Request-ID': 'instart'}],
            'LeaseWeb CDN': [{'Server': 'leasewebcdn'}],
            'Medianova': [{'Server': 'MNCDN'}],
            'MerlinCDN': [{'Server': 'MerlinCDN'}],
            'Microsoft Azure': [{'x-azure-ref': ''},
                                {'x-azure-ref-originshield': ''}],
            'Myra Security CDN': [{'Server': 'myracloud'}],
            'Naver': [{'Server': 'Testa/'}],
            'NetDNA': [{'Server': 'NetDNA'}],
            'Netlify': [{'Server': 'Netlify'}],
            'NGENIX': [{'x-ngenix-cache': ''}],
            'NOC.org': [{'Server': 'noc.org/cdn'}],
            'NYI FTW': [{'X-Powered-By': 'NYI FTW'},
                        {'X-Delivered-By': 'NYI FTW'}],
            'Optimal CDN': [{'Server': 'Optimal CDN'}],
            'OVH CDN': [{'X-CDN-Geo': ''},
                        {'X-CDN-Pop': ''}],
            'PageCDN': [{'X-CDN': 'PageCDN'}],
            'PUSHR': [{'Via': 'PUSHR'}],
            'QUIC.cloud': [{'X-QC-POP': '', 'X-QC-Cache': ''}],
            'ReSRC.it': [{'Server': 'ReSRC'}],
            'Rev Software': [{'Via': 'Rev-Cache'},
                             {'X-Rev-Cache': ''}],
            'Roast.io': [{'Server': 'Roast.io'}],
            'Rocket CDN': [{'x-rocket-node': ''}],
            'section.io': [{'section-io-id': ''}],
            'SwiftyCDN': [{'X-CDN': 'SwiftyCDN'}],
            'Singular CDN': [{'Server': 'SingularCDN'}],
            'Sirv CDN': [{'x-sirv-server': ''}],
            'Sucuri Firewall': [{'Server': 'Sucuri/Cloudproxy'},
                                {'x-sucuri-id': ''}],
            'Surge': [{'Server': 'SurgeCDN'}],
            'Twitter': [{'Server': 'tsa_b'}],
            'UnicornCDN': [{'Server': 'UnicornCDN'}],
            'Vercel': [{'Server': 'Vercel'},
                       {'Server': 'now'}],
            'WP Compress': [{'Server': 'WPCompress'}],
            'XLabs Security': [{'x-cdn': 'XLabs Security'}],
            'Yunjiasu': [{'Server': 'yunjiasu'}],
            'Zenedge': [{'X-Cdn': 'Zenedge'}],
            'Zycada Networks': [{'Zy-Server': ''}]
        }
        # spell-checker: enable

    def start(self):
        """Start running the optimization checks"""
        logging.debug('Starting optimization checks...')
        optimization_checks_disabled = bool('noopt' in self.job and self.job['noopt'])
        if self.requests is not None and not optimization_checks_disabled:
            self.running_checks = True
            # Run the slow checks in background threads
            self.cdn_thread = threading.Thread(target=self.check_cdn)
            self.hosting_thread = threading.Thread(target=self.check_hosting)
            self.gzip_thread = threading.Thread(target=self.check_gzip)
            self.image_thread = threading.Thread(target=self.check_images)
            self.progressive_thread = threading.Thread(target=self.check_progressive)
            self.font_thread = threading.Thread(target=self.check_fonts)
            self.wasm_thread = threading.Thread(target=self.check_wasm)
            self.cdn_thread.start()
            self.hosting_thread.start()
            self.gzip_thread.start()
            self.image_thread.start()
            self.progressive_thread.start()
            self.font_thread.start()
            self.wasm_thread.start()
            # collect the miscellaneous results directly
            logging.debug('Checking keep-alive.')
            self.check_keep_alive()
            logging.debug('Checking caching.')
            self.check_cache_static()
        logging.debug('Optimization checks started.')

    def join(self):
        """Wait for the optimization checks to complete and record the results"""
        logging.debug('Waiting for optimization checks to complete')
        if self.running_checks:
            logging.debug('Waiting for progressive JPEG check to complete')
            if self.progressive_thread is not None:
                self.progressive_thread.join()
                self.progressive_thread = None
            if self.progressive_time is not None:
                logging.debug("Progressive JPEG check took %0.3f seconds", self.progressive_time)
            logging.debug('Waiting for gzip check to complete')
            if self.gzip_thread is not None:
                self.gzip_thread.join()
                self.gzip_thread = None
            if self.gzip_time is not None:
                logging.debug("gzip check took %0.3f seconds", self.gzip_time)
            logging.debug('Waiting for font check to complete')
            if self.font_thread is not None:
                self.font_thread.join()
                self.font_thread = None
            if self.font_time is not None:
                logging.debug("font check took %0.3f seconds", self.font_time)
            logging.debug('Waiting for wasm check to complete')
            if self.wasm_thread is not None:
                self.wasm_thread.join()
                self.wasm_thread = None
            if self.wasm_time is not None:
                logging.debug("wasm check took %0.3f seconds", self.wasm_time)
            logging.debug('Waiting for image check to complete')
            if self.image_thread is not None:
                self.image_thread.join()
                self.image_thread = None
            if self.image_time is not None:
                logging.debug("image check took %0.3f seconds", self.image_time)
            logging.debug('Waiting for CDN check to complete')
            if self.cdn_thread is not None:
                self.cdn_thread.join()
                self.cdn_thread = None
            if self.cdn_time is not None:
                logging.debug("CDN check took %0.3f seconds", self.cdn_time)
            logging.debug('Waiting for Hosting check to complete')
            if self.hosting_thread is not None:
                self.hosting_thread.join()
                self.hosting_thread = None
            if self.hosting_time is not None:
                logging.debug("Hosting check took %0.3f seconds", self.hosting_time)
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
            for request_id in self.font_results:
                if request_id not in self.results:
                    self.results[request_id] = {}
                self.results[request_id]['font'] = self.font_results[request_id]
            for request_id in self.wasm_results:
                if request_id not in self.results:
                    self.results[request_id] = {}
                self.results[request_id]['wasm'] = self.wasm_results[request_id]
            if self.task is not None and 'page_data' in self.task:
                for name in self.hosting_results:
                    self.task['page_data'][name] = self.hosting_results[name]
            # Save the results
            if self.results:
                path = os.path.join(self.task['dir'], self.task['prefix']) + '_optimization.json.gz'
                gz_file = gzip.open(path, GZIP_TEXT, 7)
                if gz_file:
                    gz_file.write(json.dumps(self.results))
                    gz_file.close()
        logging.debug('Optimization checks complete')
        return self.results

    def check_keep_alive(self):
        """Check for requests where the connection is force-closed"""
        self.profile_start('keep_alive')
        if (sys.version_info >= (3, 0)):
            from urllib.parse import urlsplit # pylint: disable=import-error
        else:
            from urlparse import urlsplit # pylint: disable=import-error
            
        # build a list of origins and how many requests were issued to each
        origins = {}
        for request_id in self.requests:
            request = self.requests[request_id]
            if 'url' in request:
                url = request['full_url'] if 'full_url' in request else request['url']
                parsed = urlsplit(url)
                origin = parsed.scheme + '://' + parsed.netloc
                if origin not in origins:
                    origins[origin] = 0
                origins[origin] += 1
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                if 'url' in request and 'response_headers' in request:
                    check = {'score': 100}
                    url = request['full_url'] if 'full_url' in request else request['url']
                    parsed = urlsplit(url)
                    origin = parsed.scheme + '://' + parsed.netloc
                    if origins[origin] > 1:
                        check['score'] = 100
                        keep_alive = self.get_header_value(request['response_headers'], 'Connection')
                        if keep_alive is not None and keep_alive.lower().strip().find('close') > -1:
                            check['score'] = 0
                    if request_id not in self.results:
                        self.results[request_id] = {}
                    self.results[request_id]['keep_alive'] = check
            except Exception:
                logging.exception('Error checking keep-alive')
        self.profile_end('keep_alive')

    def get_time_remaining(self, request):
        """See if a request is static and how long it can be cached for"""
        from email.utils import parsedate
        re_max_age = re.compile(r'max-age[ ]*=[ ]*(?P<maxage>[\d]+)')
        is_static = False
        time_remaining = -1
        try:
            if 'response_headers' in request:
                content_length = self.get_header_value(request['response_headers'],
                                                       'Content-Length')
                if content_length is not None:
                    content_length = int(re.search(r'\d+', str(content_length)).group())
                    if content_length == 0:
                        return is_static, time_remaining
                if 'response_headers' in request:
                    content_type = self.get_header_value(request['response_headers'],
                                                         'Content-Type')
                    if content_type is None or \
                            (content_type.find('/html') == -1 and
                             content_type.find('/cache-manifest') == -1):
                        is_static = True
                        cache = self.get_header_value(request['response_headers'], 'Cache-Control')
                        pragma = self.get_header_value(request['response_headers'], 'Pragma')
                        expires = self.get_header_value(request['response_headers'], 'Expires')
                        max_age_matches = None
                        if cache is not None:
                            max_age_matches = re.search(re_max_age, cache)
                            cache = cache.lower()
                            if cache.find('no-store') > -1 or cache.find('no-cache') > -1:
                                is_static = False
                        if is_static and pragma is not None:
                            pragma = pragma.lower()
                            if pragma.find('no-cache') > -1:
                                is_static = False
                        if is_static:
                            time_remaining = 0
                            if max_age_matches is not None:
                                time_remaining = int(max_age_matches.groupdict().get('maxage'))
                                age = self.get_header_value(request['response_headers'], 'Age')
                                if time_remaining == 0:
                                    is_static = False
                                    time_remaining = -1
                                elif age is not None:
                                    time_remaining -= int(re.search(r'\d+',
                                                                    str(age).strip()).group())
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
        except Exception:
            logging.exception('Error calculating time remaining')
        return is_static, time_remaining

    def check_cache_static(self):
        """Check static resources for how long they are cacheable for"""
        self.profile_start('cache_static')
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                check = {'score': -1, 'time': 0}
                if 'status' in request and request['status'] == 200:
                    is_static, time_remaining = self.get_time_remaining(request)
                    if is_static:
                        check['time'] = time_remaining
                        if time_remaining >= 604800:  # 7 days
                            check['score'] = 100
                        elif time_remaining >= 3600:  # 1 hour
                            check['score'] = 50
                        else:
                            check['score'] = 0
                if check['score'] >= 0:
                    if request_id not in self.results:
                        self.results[request_id] = {}
                    self.results[request_id]['cache'] = check
            except Exception:
                logging.exception('Error checking cache static')
        self.profile_end('cache_static')

    def check_hosting(self):
        """Pull the data needed to determine the hosting"""
        self.profile_start('hosting')
        start = monotonic()
        self.hosting_results['base_page_ip_ptr'] = ''
        self.hosting_results['base_page_cname'] = ''
        self.hosting_results['base_page_dns_server'] = ''
        domain = None
        if self.task is not None and 'page_data' in self.task and \
                'document_hostname' in self.task['page_data']:
            domain = self.task['page_data']['document_hostname']
        if domain is not None:
            try:
                from dns import resolver, reversename
                dns_resolver = resolver.Resolver()
                dns_resolver.timeout = 1
                dns_resolver.lifetime = 1
                # reverse-lookup the edge server
                try:
                    addresses = dns_resolver.query(domain)
                    if addresses:
                        addr = str(addresses[0])
                        addr_name = reversename.from_address(addr)
                        if addr_name:
                            name = str(dns_resolver.query(addr_name, "PTR")[0])
                            if name:
                                self.hosting_results['base_page_ip_ptr'] = name.strip('. ')
                except Exception:
                    pass
                # get the CNAME for the address
                try:
                    answers = dns_resolver.query(domain, 'CNAME')
                    if answers and len(answers):
                        for rdata in answers:
                            name = '.'.join(rdata.target).strip(' .')
                            if name != domain:
                                self.hosting_results['base_page_cname'] = name
                                break
                except Exception:
                    pass
                # get the name server for the domain
                done = False
                while domain is not None and not done:
                    try:
                        dns_servers = dns_resolver.query(domain, "NS")
                        dns_name = str(dns_servers[0].target).strip('. ')
                        if dns_name:
                            self.hosting_results['base_page_dns_server'] = dns_name
                            done = True
                    except Exception:
                        pass
                    pos = domain.find('.')
                    if pos > 0:
                        domain = domain[pos + 1:]
                    else:
                        domain = None
            except Exception:
                logging.exception('Error checking hosting')
        self.hosting_time = monotonic() - start
        self.profile_end('hosting')

    def check_cdn(self):
        """Check each request to see if it was served from a CDN"""
        if (sys.version_info >= (3, 0)):
            from urllib.parse import urlparse # pylint: disable=import-error
        else:
            from urlparse import urlparse # pylint: disable=import-error
        self.profile_start('cdn')
        start = monotonic()
        # First pass, build a list of domains and see if the headers or domain matches
        static_requests = {}
        domains = {}
        for request_id in self.requests:
            request = self.requests[request_id]
            is_static, _ = self.get_time_remaining(request)
            if is_static:
                static_requests[request_id] = True
            if 'url' in request:
                url = request['full_url'] if 'full_url' in request else request['url']
                domain = urlparse(url).hostname
                if domain is not None:
                    if domain not in domains:
                        # Check the domain itself against the CDN list
                        domains[domain] = ''
                        provider = self.check_cdn_name(domain)
                        if provider is not None:
                            domains[domain] = provider
        # Spawn several workers to do CNAME lookups for the unknown domains
        count = 0
        for domain in domains:
            if not domains[domain]:
                count += 1
                self.dns_lookup_queue.put(domain)
        if count:
            thread_count = min(10, count)
            threads = []
            for _ in range(thread_count):
                self.dns_lookup_queue.put(None)
            for _ in range(thread_count):
                thread = threading.Thread(target=self.dns_worker)
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()
            try:
                while True:
                    dns_result = self.dns_result_queue.get(5)
                    if dns_result is None:
                        thread_count -= 1
                        self.dns_result_queue.task_done()
                        if thread_count == 0:
                            break
                    else:
                        domains[dns_result['domain']] = dns_result['provider']
                        self.dns_result_queue.task_done()
            except Exception:
                logging.exception("Error getting CDN DNS results")
        # Final pass, populate the CDN info for each request
        for request_id in self.requests:
            check = {'score': -1, 'provider': ''}
            request = self.requests[request_id]
            if request_id in static_requests:
                check['score'] = 0
            if 'url' in request:
                url = request['full_url'] if 'full_url' in request else request['url']
                domain = urlparse(url).hostname
                if domain is not None:
                    if domain in domains and domains[domain]:
                        check['score'] = 100
                        check['provider'] = domains[domain]
                if not check['provider'] and 'response_headers' in request:
                    provider = self.check_cdn_headers(request['response_headers'])
                    if provider is not None:
                        check['score'] = 100
                        check['provider'] = provider
                self.cdn_results[request_id] = check
        self.cdn_time = monotonic() - start
        self.profile_end('cdn')

    def find_dns_cdn(self, domain, depth=0):
        """Recursively check a CNAME chain"""
        from dns import resolver, reversename
        dns_resolver = resolver.Resolver()
        dns_resolver.timeout = 5
        dns_resolver.lifetime = 5
        provider = self.check_cdn_name(domain)
        # First do a CNAME check
        if provider is None:
            try:
                answers = dns_resolver.query(domain, 'CNAME')
                if answers and len(answers):
                    for rdata in answers:
                        try:
                            name = str(rdata.target).strip(' .')
                            logging.debug("CNAME %s => %s", domain, name)
                            if name != domain:
                                provider = self.check_cdn_name(name)
                                if provider is None and depth < 10:
                                    provider = self.find_dns_cdn(name, depth + 1)
                            if provider is not None:
                                logging.debug("provider %s => %s", domain, provider)
                                break
                        except Exception:
                            pass
            except Exception:
                pass
        # Try a reverse-lookup of the address
        if provider is None:
            try:
                addresses = dns_resolver.query(domain)
                if addresses:
                    addr = str(addresses[0])
                    logging.debug("PTR %s => %s", domain, addr)
                    addr_name = reversename.from_address(addr)
                    if addr_name:
                        name = str(dns_resolver.query(addr_name, "PTR")[0])
                        logging.debug("PTR %s => %s => %s", domain, addr, name)
                        if name:
                            provider = self.check_cdn_name(name)
            except Exception:
                pass
        return provider

    def dns_worker(self):
        """Handle the DNS CNAME lookups and checking in multiple threads"""
        try:
            while True:
                domain = self.dns_lookup_queue.get(5)
                if domain is None:
                    self.dns_lookup_queue.task_done()
                    self.dns_result_queue.put(None)
                    break
                else:
                    try:
                        provider = self.find_dns_cdn(domain)
                        if provider is not None:
                            self.dns_result_queue.put({'domain': domain, 'provider': provider})
                    except Exception:
                        logging.debug('Error in dns worker')
                    self.dns_lookup_queue.task_done()
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
        matched_cdns = []
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
                    matched_cdns.append(cdn)
                    break

        if not len(matched_cdns):
            return None

        return ', '.join(matched_cdns)

    def check_gzip(self):
        """Check each request to see if it can be compressed"""
        self.profile_start('gzip')
        start = monotonic()
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                content_length = None
                if 'response_headers' in request:
                    content_length = self.get_header_value(request['response_headers'], 'Content-Length')
                if 'objectSize' in request:
                    content_length = request['objectSize']
                elif content_length is not None:
                    content_length = int(re.search(r'\d+', str(content_length)).group())
                elif 'transfer_size' in request:
                    content_length = request['transfer_size']
                if content_length is None:
                    content_length = 0
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
                if 'body' not in request:
                    check['score'] = -1
                # Try compressing it if it isn't an image or known binary
                if not check['score']:
                    sniff_type = self.sniff_file_content(request['body'])
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
                            if target_size is not None:
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
                        else:
                            check['score'] = -1
                if check['score'] >= 0:
                    self.gzip_results[request_id] = check
            except Exception:
                logging.exception('Error checking gzip')
        self.gzip_time = monotonic() - start
        self.profile_end('gzip')

    def check_images(self):
        """Check each request to see if images can be compressed better"""
        self.profile_start('images')
        start = monotonic()
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                content_length = None
                if 'response_headers' in request:
                    content_length = self.get_header_value(request['response_headers'], 'Content-Length')
                if content_length is not None:
                    content_length = int(re.search(r'\d+', str(content_length)).group())
                elif 'transfer_size' in request:
                    content_length = request['transfer_size']
                if 'body' in request:
                    sniff_type = self.sniff_file_content(request['body'])
                    if sniff_type in ['jpeg', 'png', 'gif', 'webp', 'avif', 'jxl']:
                        if sniff_type is None:
                            sniff_type = 'Unknown'
                        check = {'score': -1,
                                 'size': content_length,
                                 'target_size': content_length,
                                 'info': {
                                     'detected_type': sniff_type
                                 }}
                        # Use exiftool to extract the image metadata if it is installed
                        try:
                            metadata = json.loads(subprocess.check_output(['exiftool', '-j', '-g', request['body']], encoding='UTF-8'))
                            if metadata is not None:
                                if isinstance(metadata, list) and len(metadata) == 1:
                                    metadata = metadata[0]
                                if 'SourceFile' in metadata:
                                    del metadata['SourceFile']
                                if 'File' in metadata:
                                    for key in ['FileName', 'Directory', 'FileModifyDate', 'FileAccessDate', 'FileInodeChangeDate', 'FilePermissions']:
                                        if key in metadata['File']:
                                            del metadata['File'][key]
                                check['info']['metadata'] = metadata
                        except Exception:
                            pass
                        # Use imagemagick to convert metadata to json
                        try:
                            command = '{0} "{1}[0]" json:-'.format(self.job['image_magick']['convert'], request['body'])
                            subprocess.call(command, shell=True)
                            magick_str = subprocess.check_output(command, shell=True, encoding='UTF-8')
                            try:
                                magick = json.loads(magick_str)
                            except Exception:
                                # Fix issues with imagemagick's json output
                                magick_str = magick_str.replace('}\n{', '},\n{')
                                magick_str = re.sub(r"([\"\w])(\n\s+\")", r"\1,\2", magick_str)
                                magick_str = '[' + magick_str + ']'
                                magick = json.loads(magick_str)
                            if magick and 'image' in magick[0]:
                                image = magick[0]['image']
                                if len(magick) > 1:
                                    image['FrameCount'] = len(magick)
                                remove = ['name', 'artifacts', 'colormap', 'version']
                                for key in remove:
                                    if key in image:
                                        del image[key]
                                check['info']['magick'] = image
                        except Exception:
                            logging.exception('Error extracting image magick')
                        # Extract format-specific data
                        if sniff_type == 'jpeg':
                            if content_length < 1400:
                                check['score'] = 100
                            else:
                                # Compress it as a quality 85 stripped progressive image and compare
                                jpeg_file = request['body'] + '.jpg'
                                command = '{0} -define jpeg:dct-method=fast -strip '\
                                    '-interlace Plane -quality 85 '\
                                    '"{1}" "{2}"'.format(self.job['image_magick']['convert'],
                                                        request['body'], jpeg_file)
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
                            if 'response_body' not in request:
                                request['response_body'] = ''
                                with open(request['body'], 'rb') as f_in:
                                    request['response_body'] = f_in.read()
                            if content_length < 1400:
                                check['score'] = 100
                            else:
                                # spell-checker: disable
                                image_chunks = [b"iCCP", b"tIME", b"gAMA", b"PLTE", b"acTL", b"IHDR", b"cHRM",
                                                b"bKGD", b"tRNS", b"sBIT", b"sRGB", b"pHYs", b"hIST", b"vpAg",
                                                b"oFFs", b"fcTL", b"fdAT", b"IDAT"]
                                # spell-checker: enable
                                body = request['response_body']
                                image_size = len(body)
                                valid = True
                                target_size = 8
                                bytes_remaining = image_size - 8
                                pos = 8
                                while valid and bytes_remaining >= 4:
                                    chunk_len = struct.unpack('>I', body[pos: pos + 4])[0]
                                    pos += 4
                                    if chunk_len + 12 <= bytes_remaining:
                                        chunk_type = body[pos: pos + 4]
                                        pos += 4
                                        if chunk_type in image_chunks:
                                            target_size += chunk_len + 12
                                        pos += chunk_len + 4  # Skip the data and CRC
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
                                check['info']['animated'] = is_animated
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
                        elif sniff_type == 'avif':
                            check['score'] = 100
                        elif sniff_type == 'jxl':
                            check['score'] = 100
                        self.image_results[request_id] = check
            except Exception:
                logging.exception('Error checking images')
        self.image_time = monotonic() - start
        self.profile_end('images')

    def check_progressive(self):
        """Count the number of scan lines in each jpeg"""
        from PIL import Image
        self.profile_start('progressive')
        start = monotonic()
        for request_id in self.requests:
            try:
                request = self.requests[request_id]
                if 'body' in request:
                    sniff_type = self.sniff_file_content(request['body'])
                    if sniff_type == 'jpeg':
                        check = {'size': os.path.getsize(request['body']), 'scan_count': 1}
                        image = Image.open(request['body'])
                        info = dict(image.info)
                        image.close()
                        if 'progression' in info and info['progression']:
                            check['scan_count'] = 0
                            if 'response_body' not in request:
                                request['response_body'] = ''
                                with open(request['body'], 'rb') as f_in:
                                    request['response_body'] = f_in.read()
                            body = request['response_body']
                            content_length = len(request['response_body'])
                            pos = 0
                            try:
                                while pos < content_length:
                                    block = struct.unpack('B', body[pos: pos + 1])[0]
                                    pos += 1
                                    if block != 0xff:
                                        break
                                    block = struct.unpack('B', body[pos: pos + 1])[0]
                                    pos += 1
                                    while block == 0xff:
                                        block = struct.unpack('B', body[pos: pos + 1])[0]
                                        pos += 1
                                    if block == 0x01 or (block >= 0xd0 and block <= 0xd9):
                                        continue
                                    elif block == 0xda:  # Image data
                                        check['scan_count'] += 1
                                        # Seek to the next non-padded 0xff to find the next marker
                                        found = False
                                        while not found and pos < content_length:
                                            value = struct.unpack('B', body[pos: pos + 1])[0]
                                            pos += 1
                                            if value == 0xff:
                                                value = struct.unpack('B', body[pos: pos + 1])[0]
                                                pos += 1
                                                if value != 0x00:
                                                    found = True
                                                    pos -= 2
                                    else:
                                        chunk = body[pos: pos + 2]
                                        block_size = struct.unpack('2B', chunk)
                                        pos += 2
                                        block_size = block_size[0] * 256 + block_size[1] - 2
                                        pos += block_size
                            except Exception:
                                logging.exception('Error scanning JPEG')
                        self.progressive_results[request_id] = check
            except Exception:
                logging.exception('Error checking progressive')
        self.progressive_time = monotonic() - start
        self.profile_end('progressive')

    def check_fonts(self):
        """Check each request to extract metadata about fonts"""
        self.profile_start('fonts')
        start = monotonic()
        try:
            from . import font_metadata
            for request_id in self.requests:
                try:
                    request = self.requests[request_id]
                    if 'body' in request:
                        sniff_type = self.sniff_file_content(request['body'])
                        if sniff_type is not None and sniff_type in ['OTF', 'TTF', 'WOFF', 'WOFF2']:
                            font_info = font_metadata.read_metadata(request['body'])
                            if font_info is not None:
                                self.font_results[request_id] = font_info
                except Exception:
                    pass
        except Exception:
            logging.exception('Error checking fonts')
        self.font_time = monotonic() - start
        self.profile_end('fonts')

    def check_wasm(self):
        """Check each request to extract wasm stats (if wasm-stats is available for the current platform)"""
        self.profile_start('wasm')
        start = monotonic()
        wasm_stats = None
        if platform.system() == "Linux" and not os.uname()[4].startswith('arm') and platform.architecture()[0] == '64bit':
            wasm_stats = os.path.abspath(os.path.join(os.path.dirname(__file__), 'support', 'wasm-stats', 'linux', 'wasm-stats'))
        elif platform.system() == 'Darwin':
            wasm_stats = os.path.abspath(os.path.join(os.path.dirname(__file__), 'support', 'wasm-stats', 'osx', 'wasm-stats'))
        if wasm_stats is not None and os.path.exists(wasm_stats):
            try:
                for request_id in self.requests:
                    try:
                        request = self.requests[request_id]
                        if 'body' in request and 'response_headers' in request:
                            content_type = self.get_header_value(request['response_headers'], 'Content-Type')
                            if content_type == 'application/wasm':
                                stats = json.loads(subprocess.check_output([wasm_stats, request['body']], encoding='UTF-8'))
                                if stats is not None:
                                    self.wasm_results[request_id] = stats
                    except Exception:
                        pass
            except Exception:
                logging.exception('Error checking wasm')
        self.wasm_time = monotonic() - start
        self.profile_end('wasm')

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

    def sniff_content(self, raw_bytes):
        """Check the beginning of the file to see if it is a known image type"""
        content_type = None
        hex_bytes = binascii.hexlify(raw_bytes[:14])
        # spell-checker: disable
        if hex_bytes[0:6] == b'ffd8ff':
            content_type = 'jpeg'
        elif hex_bytes[0:16] == b'89504e470d0a1a0a':
            content_type = 'png'
        elif hex_bytes[0:22] == b'0000002066747970617669' or raw_bytes[4:12] == b'ftypavif':
            content_type = 'avif'
        elif hex_bytes[0:14] == b'0000000c4a584c':
            content_type = 'jxl'
        elif raw_bytes[:6] == b'GIF87a' or raw_bytes[:6] == b'GIF89a':
            content_type = 'gif'
        elif raw_bytes[:4] == b'RIFF' and raw_bytes[8:14] == b'WEBPVP':
            content_type = 'webp'
        elif raw_bytes[:4] == b'OTTO':
            content_type = 'OTF'
        elif raw_bytes[:4] == b'ttcf' or hex_bytes[0:8] == b'00010000':
            content_type = 'TTF'
        elif raw_bytes[:4] == b'wOFF':
            content_type = 'WOFF'
        elif raw_bytes[:4] == b'wOF2':
            content_type = 'WOFF2'
        # spell-checker: enable
        return content_type

    def sniff_file_content(self, image_file):
        """Sniff the content type from a file"""
        content_type = None
        with open(image_file, 'rb') as f_in:
            raw = f_in.read(14)
            content_type = self.sniff_content(raw)
        return content_type

    def profile_start(self, event_name):
        event_name = 'opt.' + event_name
        if self.task is not None and 'profile_data' in self.task:
            with self.task['profile_data']['lock']:
                self.task['profile_data'][event_name] = {'s': round(monotonic() - self.task['profile_data']['start'], 3)}

    def profile_end(self, event_name):
        event_name = 'opt.' + event_name
        if self.task is not None and 'profile_data' in self.task:
            with self.task['profile_data']['lock']:
                if event_name in self.task['profile_data']:
                    self.task['profile_data'][event_name]['e'] = round(monotonic() - self.task['profile_data']['start'], 3)
                    self.task['profile_data'][event_name]['d'] = round(self.task['profile_data'][event_name]['e'] - self.task['profile_data'][event_name]['s'], 3)
