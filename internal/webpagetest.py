# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Main entry point for interfacing with WebPageTest server"""
import base64
from datetime import datetime
import glob
import gzip
import hashlib
import logging
import multiprocessing
import os
import platform
import random
import re
import shutil
import socket
import string
import subprocess
import sys
import threading
import time
import zipfile
import psutil
from internal import os_util

if (sys.version_info >= (3, 0)):
    from time import monotonic
    from urllib.parse import quote_plus # pylint: disable=import-error
    from urllib.parse import urlsplit # pylint: disable=import-error
    GZIP_READ_TEXT = 'rt'
    GZIP_TEXT = 'wt'
else:
    from monotonic import monotonic
    from urllib import quote_plus # pylint: disable=import-error,no-name-in-module
    from urlparse import urlsplit # pylint: disable=import-error
    GZIP_READ_TEXT = 'r'
    GZIP_TEXT = 'w'
try:
    import ujson as json
except BaseException:
    import json
"""
try:
    import http.client as http_client
except ImportError:
    # Python 2
    import httplib as http_client
http_client.HTTPConnection.debuglevel = 1
"""

DEFAULT_JPEG_QUALITY = 30

class WebPageTest(object):
    """Controller for interfacing with the WebPageTest server"""
    # pylint: disable=E0611
    def __init__(self, options, workdir):
        import requests
        self.fetch_queue = multiprocessing.JoinableQueue()
        self.fetch_result_queue = multiprocessing.JoinableQueue()
        self.job = None
        self.raw_job = None
        self.first_failure = None
        self.is_rebooting = False
        self.is_dead = False
        self.health_check_server = None
        self.metadata_blocked = False
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'wptagent'})
        self.extension_session = requests.Session()
        self.extension_session.headers.update({'User-Agent': 'wptagent'})
        self.options = options
        self.last_test_id = None
        self.fps = options.fps
        self.test_run_count = 0
        self.log_formatter = logging.Formatter(fmt="%(asctime)s.%(msecs)03d - %(message)s",
                                               datefmt="%H:%M:%S")
        self.log_handler = None
        # Configurable options
        self.work_servers = []
        self.needs_zip = []
        self.url = ''
        if options.server is not None:
            self.work_servers_str = options.server
            if self.work_servers_str == 'www.webpagetest.org':
                self.work_servers_str = 'http://www.webpagetest.org/'
            self.work_servers = self.work_servers_str.split(',')
            self.url = str(self.work_servers[0])
        self.location = ''
        self.test_locations = []
        if options.location is not None:
            self.test_locations = options.location.split(',')
            self.location = str(self.test_locations[0])
        self.wpthost = None
        self.license_pinged = False
        self.key = options.key
        self.scheduler = options.scheduler
        self.scheduler_salt = options.schedulersalt
        self.scheduler_nodes = []
        if options.schedulernode is not None:
            self.scheduler_nodes = options.schedulernode.split(',')
        self.scheduler_node = None
        self.last_diagnostics = None
        self.time_limit = 120
        self.cpu_scale_multiplier = None
        self.pc_name = os_util.pc_name() if options.name is None else options.name
        self.auth_name = options.username
        self.auth_password = options.password if options.password is not None else ''
        self.validate_server_certificate = options.validcertificate
        self.instance_id = None
        self.zone = None
        self.cpu_pct = None
        # Get the screen resolution if we're in desktop mode
        self.screen_width = None
        self.screen_height = None
        if not self.options.android and not self.options.iOS:
            if self.options.xvfb:
                self.screen_width = 1920
                self.screen_height = 1200
            elif platform.system() == 'Windows':
                try:
                    from win32api import GetSystemMetrics # pylint: disable=import-error
                    self.screen_width = GetSystemMetrics(0)
                    self.screen_height = GetSystemMetrics(1)
                except Exception:
                    logging.exception('Error getting screen resolution')
            elif platform.system() == 'Darwin':
                try:
                    from AppKit import NSScreen # pylint: disable=import-error
                    self.screen_width = int(NSScreen.screens()[0].frame().size.width)
                    self.screen_height = int(NSScreen.screens()[0].frame().size.height)
                except Exception:
                    logging.exception('Error getting screen resolution')
            elif platform.system() == 'Linux':
                out = subprocess.check_output(['xprop','-notype','-len','16','-root','_NET_DESKTOP_GEOMETRY'], universal_newlines=True)
                if out is not None:
                    logging.debug(out)
                    parts = out.split('=', 1)
                    if len(parts) == 2:
                        dimensions = parts[1].split(',', 1)
                        if len(dimensions) == 2:
                            self.screen_width = int(dimensions[0].strip())
                            self.screen_height = int(dimensions[1].strip())
        # Grab the list of configured DNS servers
        self.dns_servers = None
        try:
            from dns import resolver
            dns_resolver = resolver.Resolver()
            self.dns_servers = '-'.join(dns_resolver.nameservers)
        except Exception:
            pass
        # See if we have to load dynamic config options
        if self.options.ec2:
            self.load_from_ec2()
        elif self.options.gce:
            self.load_from_gce()
        self.block_metadata()
        # Set the session authentication options
        if self.auth_name is not None:
            self.session.auth = (self.auth_name, self.auth_password)
        self.session.verify = self.validate_server_certificate
        if options.cert is not None:
            if options.certkey is not None:
                self.session.cert = (options.cert, options.certkey)
            else:
                self.session.cert = options.cert
        # Set up the temporary directories
        self.workdir = os.path.join(workdir, self.pc_name)
        self.persistent_dir = self.workdir + '.data'
        self.profile_dir = os.path.join(self.workdir, 'browser')
        if os.path.isdir(self.workdir):
            try:
                shutil.rmtree(self.workdir)
            except Exception:
                pass
        # If we are running in a git clone, grab the date of the last
        # commit as the version
        self.version = '21.07'
        try:
            directory = os.path.abspath(os.path.dirname(__file__))
            if (sys.version_info >= (3, 0)):
                out = subprocess.check_output('git log -1 --format=%cd --date=raw', shell=True, cwd=directory, encoding='UTF-8')
            else:
                out = subprocess.check_output('git log -1 --format=%cd --date=raw', shell=True, cwd=directory)
            if out is not None:
                matches = re.search(r'^(\d+)', out)
                if matches:
                    timestamp = int(matches.group(1))
                    git_date = datetime.utcfromtimestamp(timestamp)
                    self.version = git_date.strftime('%y%m%d.%H%M%S')
        except Exception:
            pass
        # Load the discovered browser margins
        self.margins = {}
        margins_file = os.path.join(self.persistent_dir, 'margins.json')
        if os.path.isfile(margins_file):
            with open(margins_file, 'r') as f_in:
                self.margins = json.load(f_in)
        # Load any locally-defined custom metrics from {agent root}/custom/metrics/*.js
        self.custom_metrics = {}
        self.load_local_custom_metrics()
    # pylint: enable=E0611

    def load_local_custom_metrics(self):
        metrics_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'custom', 'metrics')
        if (os.path.isdir(metrics_dir)):
            files = glob.glob(metrics_dir + '/*.js')
            for file in files:
                try:
                    with open(file, 'rt') as f:
                        metric_value = f.read()
                        if metric_value:
                            metric_name = os.path.basename(file)[:-3]
                            self.custom_metrics[metric_name] = metric_value
                            logging.debug('Loaded custom metric %s from %s', metric_name, file)
                except Exception:
                    pass

    def benchmark_cpu(self):
        """Benchmark the CPU for mobile emulation"""
        self.cpu_scale_multiplier = 1.0
        if not self.options.android and not self.options.iOS:
            import hashlib
            logging.debug('Starting CPU benchmark')
            hash_val = hashlib.sha256()
            with open(__file__, 'rb') as f_in:
                hash_data = f_in.read(4096)
            start = monotonic()
            # 106k iterations takes ~1 second on the reference machine
            iteration = 0
            while iteration < 106000:
                hash_val.update(hash_data)
                iteration += 1
            elapsed = monotonic() - start
            self.cpu_scale_multiplier = 1.0 / elapsed
            logging.debug('CPU Benchmark elapsed time: %0.3f, multiplier: %0.3f',
                          elapsed, self.cpu_scale_multiplier)

    def get_persistent_dir(self):
        """Return the path to the persistent cache directory"""
        return self.persistent_dir

    def load_from_ec2(self):
        """Load config settings from EC2 user data"""
        import requests
        session = requests.Session()
        proxies = {"http": None, "https": None}
        # The Windows AMI's use static routes which are not copied across regions.
        # This sets them up before we attempt to access the metadata
        if platform.system() == "Windows":
            from .os_util import run_elevated
            directory = os.path.abspath(os.path.dirname(__file__))
            ec2_script = os.path.join(directory, 'support', 'ec2', 'win_routes.ps1')
            run_elevated('powershell.exe', ec2_script)
        # Make sure the route blocking isn't configured on Linux
        if platform.system() == "Linux":
            subprocess.call(['sudo', 'route', 'delete', '169.254.169.254'])
        ok = False
        while not ok:
            try:
                response = session.get('http://169.254.169.254/latest/user-data', timeout=30, proxies=proxies)
                if len(response.text):
                    self.parse_user_data(response.text)
                    ok = True
            except Exception:
                pass
            if not ok:
                time.sleep(10)
        ok = False
        while not ok:
            try:
                response = session.get('http://169.254.169.254/latest/meta-data/instance-id', timeout=30, proxies=proxies)
                if len(response.text):
                    self.instance_id = response.text.strip()
                    ok = True
            except Exception:
                pass
            if not ok:
                time.sleep(10)
        ok = False
        while not ok:
            try:
                response = session.get('http://169.254.169.254/latest/meta-data/placement/availability-zone', timeout=30, proxies=proxies)
                if len(response.text):
                    self.zone = response.text.strip()
                    if not len(self.test_locations):
                        self.location = self.zone[:-1]
                        if platform.system() == "Linux":
                            self.location += '-linux'
                        self.test_locations = [self.location]
                    ok = True
            except Exception:
                pass
            if not ok:
                time.sleep(10)
        # Block access to the metadata server
        if platform.system() == "Linux":
            subprocess.call(['sudo', 'route', 'add', '169.254.169.254', 'gw', '127.0.0.1', 'lo'])
            self.metadata_blocked = True

    def load_from_gce(self):
        """Load config settings from GCE user data"""
        import requests
        session = requests.Session()
        proxies = {"http": None, "https": None}
        ok = False
        while not ok:
            try:
                response = session.get(
                    'http://metadata.google.internal/computeMetadata/v1/instance/attributes/wpt_data',
                    headers={'Metadata-Flavor': 'Google'},
                    timeout=30, proxies=proxies)
                if len(response.text):
                    self.parse_user_data(response.text)
                    ok = True
            except Exception:
                pass
            if not ok:
                time.sleep(10)
        ok = False
        while not ok:
            try:
                response = session.get('http://metadata.google.internal/computeMetadata/v1/instance/id',
                                       headers={'Metadata-Flavor': 'Google'},
                                       timeout=30, proxies=proxies)
                if len(response.text):
                    self.instance_id = response.text.strip()
                    ok = True
            except Exception:
                pass
            if not ok:
                time.sleep(10)
        if not len(self.test_locations):
            ok = False
            while not ok:
                try:
                    response = session.get('http://metadata.google.internal/computeMetadata/v1/instance/zone',
                                           headers={'Metadata-Flavor': 'Google'},
                                           timeout=30, proxies=proxies)
                    if len(response.text):
                        zone = response.text.strip()
                        position = zone.rfind('/')
                        if position > -1:
                            zone = zone[position + 1:]
                        self.zone = zone
                        self.location = 'gce-' + self.zone[:-2]
                        if platform.system() == "Linux":
                            self.location += '-linux'
                        self.test_locations = [self.location]
                        ok = True
                except Exception:
                    pass
                if not ok:
                    time.sleep(10)

    def block_metadata(self):
        """Block access to the metadata service if we are on EC2 or Azure"""
        if not self.metadata_blocked:
            import requests
            needs_block = False
            session = requests.Session()
            proxies = {"http": None, "https": None}
            try:
                response = session.get('http://169.254.169.254/latest/meta-data/identity-credentials/ec2/security-credentials/ec2-instance', timeout=10, proxies=proxies)
                if response.status_code == 200:
                    needs_block = True
                else:
                    response = session.get('http://169.254.169.254/metadata/instance?api-version=2017-04-02', timeout=10, proxies=proxies)
                    if response.status_code == 200:
                        needs_block = True
            except Exception:
                pass
            if needs_block:
                subprocess.call(['sudo', 'route', 'add', '169.254.169.254', 'gw', '127.0.0.1', 'lo'])
                self.metadata_blocked = True

    def parse_user_data(self, user_data):
        """Parse the provided user data and extract the config info"""
        logging.debug("User Data: %s", user_data)
        options = user_data.split()
        for option in options:
            try:
                parts = option.split('=', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    logging.debug('Setting config option "%s" to "%s"', key, value)
                    if key == 'wpt_server':
                        server = ''
                        if re.search(r'^https?://', value):
                            server = value
                            if value.endswith('/'):
                                server += 'work/'
                            else:
                                server += '/work/'
                        else:
                            server = 'http://{0}/work/'.format(value)
                        self.work_servers_str = str(server)
                        self.work_servers = self.work_servers_str.split(',')
                        self.url = str(self.work_servers[0])
                    if key == 'wpt_url':
                        self.work_servers_str = str(value)
                        self.work_servers = self.work_servers_str.split(',')
                        self.url = str(self.work_servers[0])
                    elif key == 'wpt_loc' or key == 'wpt_location':
                        if value is not None:
                            self.test_locations = value.split(',')
                            self.location = str(self.test_locations[0])
                            if key == 'wpt_location':
                                append = []
                                for loc in self.test_locations:
                                    append.append('{0}_wptdriver'.format(loc))
                                if len(append):
                                    self.test_locations.extend(append)
                    elif key == 'wpt_key':
                        self.key = value
                    elif key == 'wpt_timeout':
                        self.time_limit = int(re.search(r'\d+', str(value)).group())
                    elif key == 'wpt_username':
                        self.auth_name = value
                    elif key == 'wpt_password':
                        self.auth_password = value
                    elif key == 'wpt_validcertificate' and value == '1':
                        self.validate_server_certificate = True
                    elif key == 'validcertificate' and value == '1':
                        self.validate_server_certificate = True
                    elif key == 'wpt_scheduler':
                        self.scheduler = value
                    elif key == 'wpt_scheduler_salt':
                        self.scheduler_salt = value
                    elif key == 'wpt_scheduler_node':
                        self.scheduler_nodes = value.split(',')
                    elif key == 'wpt_fps':
                        self.fps = int(re.search(r'\d+', str(value)).group())
                    elif key == 'fps':
                        self.fps = int(re.search(r'\d+', str(value)).group())
            except Exception:
                logging.exception('Error parsing metadata')

    # pylint: disable=E1101
    def get_uptime_minutes(self):
        """Get the system uptime in seconds"""
        boot_time = None
        try:
            boot_time = psutil.boot_time()
        except Exception:
            pass
        if boot_time is None:
            try:
                boot_time = psutil.get_boot_time()
            except Exception:
                pass
        if boot_time is None:
            try:
                boot_time = psutil.BOOT_TIME
            except Exception:
                pass
        uptime = None
        if boot_time is not None and boot_time > 0:
            uptime = int((time.time() - boot_time) / 60)
        if uptime is not None and uptime < 0:
            uptime = 0
        return uptime
    # pylint: enable=E1101

    def reboot(self):
        self.is_rebooting = True
        if platform.system() == 'Windows':
            subprocess.call(['shutdown', '/r', '/f'])
        else:
            subprocess.call(['sudo', 'reboot'])

    def get_cpid(self, node = None):
        """Get a salt-signed header for the scheduler"""
        entity = node if node else self.scheduler_node
        hash_src = entity.upper() + ';' + datetime.now().strftime('%Y%m') + self.scheduler_salt
        hash_string = base64.b64encode(hashlib.sha1(hash_src.encode('ascii')).digest()).decode('ascii')
        cpid_header = 'm;' + entity + ';' + hash_string
        return cpid_header

    def process_job_json(self, test_json):
        """Process the JSON of a test into a job file"""
        if self.cpu_scale_multiplier is None:
            self.benchmark_cpu()
        job = test_json
        self.raw_job = dict(test_json)
        if job is not None:
            try:
                logging.debug("Job: %s", json.dumps(job))
                # set some default options
                job['agent_version'] = self.version
                if 'imageQuality' not in job:
                    job['imageQuality'] = DEFAULT_JPEG_QUALITY
                if 'pngScreenShot' not in job:
                    job['pngScreenShot'] = 0
                if 'fvonly' not in job:
                    job['fvonly'] = not self.options.testrv
                if 'width' not in job:
                    job['width'] = 1366
                if 'height' not in job:
                    job['height'] = 768
                if 'browser_width' in job:
                    job['width'] = job['browser_width']
                if 'browser_height' in job:
                    job['height'] = job['browser_height']
                if 'timeout' not in job:
                    job['timeout'] = self.time_limit
                if 'noscript' not in job:
                    job['noscript'] = 0
                if 'type' not in job:
                    job['type'] = ''
                if job['type'] == 'traceroute':
                    job['fvonly'] = 1
                if 'fps' not in job:
                    job['fps'] = self.fps
                if 'warmup' not in job:
                    job['warmup'] = 0
                if 'wappalyzer' not in job:
                    job['wappalyzer'] = 1
                if 'axe' not in job:
                    job['axe'] = 1
                if 'axe_categories' not in job:
                    job['axe_categories'] = 'wcag2a,wcag2aa'
                if job['type'] == 'lighthouse':
                    job['fvonly'] = 1
                    job['lighthouse'] = 1
                job['keep_lighthouse_trace'] = bool('lighthouseTrace' in job and job['lighthouseTrace'])
                job['keep_lighthouse_screenshots'] = bool(job['lighthouseScreenshots']) if 'lighthouseScreenshots' in job else False
                job['lighthouse_throttle'] = bool('lighthouseThrottle' in job and job['lighthouseThrottle'])
                job['lighthouse_config'] = str(job['lighthouseConfig']) if 'lighthouseConfig' in job else False
                if 'video' not in job:
                    job['video'] = bool('Capture Video' not in job or job['Capture Video'])
                job['keepvideo'] = bool('keepvideo' in job and job['keepvideo'])
                job['dtShaper'] = bool('dtShaper' in job and job['dtShaper'])
                job['disable_video'] = bool(not job['video'] and 'disable_video' in job and job['disable_video'])
                job['atomic'] = bool('atomic' in job and job['atomic'])
                job['interface'] = None
                job['persistent_dir'] = self.persistent_dir
                if 'throttle_cpu' in job:
                    throttle = float(re.search(r'\d+\.?\d*', str(job['throttle_cpu'])).group())
                    if 'bypass_cpu_normalization' not in job or not job['bypass_cpu_normalization']:
                        throttle *= self.cpu_scale_multiplier
                    job['throttle_cpu_requested'] = job['throttle_cpu']
                    job['throttle_cpu'] = throttle
                if 'work_servers' in job and job['work_servers'] != self.work_servers_str:
                        self.work_servers_str = job['work_servers']
                        self.work_servers = self.work_servers_str.split(',')
                        logging.debug("Servers changed to: %s", self.work_servers_str)
                if 'wpthost' in job:
                    self.wpthost = job['wpthost']
                job['started'] = time.time()
                if 'testinfo' in job:
                    job['testinfo']['started'] = job['started']
                # Add the security insights custom metrics locally if requested
                if 'securityInsights' in job:
                    js_directory = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')
                    if 'customMetrics' not in job:
                        job['customMetrics'] = {}
                    if 'jsLibsVulns' not in job['customMetrics']:
                        with open(os.path.join(js_directory, 'jsLibsVulns.js'), 'rt') as f_in:
                            job['customMetrics']['jsLibsVulns'] = f_in.read()
                    if 'securityHeaders' not in job['customMetrics']:
                        with open(os.path.join(js_directory, 'securityHeaders.js'), 'rt') as f_in:
                            job['customMetrics']['securityHeaders'] = f_in.read()
                if 'browser' not in job:
                    job['browser'] = 'Chrome'
                if 'runs' not in job:
                    job['runs'] = 1
                if 'timeline' not in job:
                    job['timeline'] = 1
                if self.options.location is not None:
                    job['location'] = self.options.location
                if self.scheduler_node is not None and 'saas_test_id' in job:
                    job['saas_node_id'] = self.scheduler_node
                # For CLI tests, write out the raw job file
                if self.options.testurl or self.options.testspec or 'saas_test_id' in job:
                    if not os.path.isdir(self.workdir):
                        os.makedirs(self.workdir)
                    job_path = os.path.join(self.workdir, 'job.json')
                    logging.debug('Job Path: {}'.format(job_path))
                    with open(job_path, 'wt') as f_out:
                        json.dump(job, f_out)
                    self.needs_zip.append({'path': job_path, 'name': 'job.json'})
                    if 'testinfo' in job:
                        job['testinfo']['started'] = job['started']

                # Add the non-serializable members
                if self.health_check_server is not None:
                    job['health_check_server'] = self.health_check_server
                # add any locally-defined custom metrics (server versions override locals with the same name)
                if self.custom_metrics:
                    if 'customMetrics' not in job:
                        job['customMetrics'] = {}
                    for name in self.custom_metrics:
                        if name not in job['customMetrics']:
                            job['customMetrics'][name] = self.custom_metrics[name]
            except Exception:
                logging.exception("Error processing job json")
        self.job = job
        return job

    def get_test(self, browsers):
        """Get a job from the server"""
        if self.is_rebooting or self.is_dead or self.options.pubsub:
            return
        import requests
        proxies = {"http": None, "https": None}
        from .os_util import get_free_disk_space
        if len(self.work_servers) == 0 and len(self.scheduler_nodes) == 0:
            return None
        job = None
        self.raw_job = None
        scheduler_nodes = list(self.scheduler_nodes)
        if len(scheduler_nodes) > 0:
            random.shuffle(scheduler_nodes)
            self.scheduler_node = str(scheduler_nodes.pop(0)).strip(', ')
        servers = list(self.work_servers)
        if len(servers) >0 :
            random.shuffle(servers)
            self.url = str(servers.pop(0))
        locations = list(self.test_locations) if len(self.test_locations) > 1 else [self.location]
        if len(locations) > 0:
            random.shuffle(locations)
            location = str(locations.pop(0))
        # Shuffle the list order
        if len(self.test_locations) > 1:
            self.test_locations.append(str(self.test_locations.pop(0)))
        count = 0
        retry = True
        while count < 3 and retry:
            retry = False
            count += 1
            url = self.url + "getwork.php?f=json&shards=1&reboot=1&servers=1&testinfo=1"
            url += "&location=" + quote_plus(location)
            url += "&pc=" + quote_plus(self.pc_name)
            if self.key is not None:
                url += "&key=" + quote_plus(self.key)
            if self.instance_id is not None:
                url += "&ec2=" + quote_plus(self.instance_id)
            if self.zone is not None:
                url += "&ec2zone=" + quote_plus(self.zone)
            if self.options.android:
                url += '&apk=1'
            url += '&version={0}'.format(self.version)
            if self.screen_width is not None:
                url += '&screenwidth={0:d}'.format(self.screen_width)
            if self.screen_height is not None:
                url += '&screenheight={0:d}'.format(self.screen_height)
            if self.dns_servers is not None:
                url += '&dns=' + quote_plus(self.dns_servers)
            free_disk = get_free_disk_space()
            url += '&freedisk={0:0.3f}'.format(free_disk)
            uptime = self.get_uptime_minutes()
            if uptime is not None:
                url += '&upminutes={0:d}'.format(uptime)
            if 'collectversion' in self.options and \
                    self.options.collectversion:
                versions = []
                for name in browsers.keys():
                    if 'version' in browsers[name]:
                        versions.append('{0}:{1}'.format(name, \
                                browsers[name]['version']))
                browser_versions = ','.join(versions)
                url += '&browsers=' + quote_plus(browser_versions)
            try:
                if self.scheduler and self.scheduler_salt and self.scheduler_node:
                    url = self.scheduler + 'hawkscheduleserver/wpt-dequeue.ashx?machine={}'.format(quote_plus(self.pc_name))
                    logging.info("Checking for work for node %s: %s", self.scheduler_node, url)
                    response = self.session.get(url, timeout=10, proxies=proxies, headers={'CPID': self.get_cpid(self.scheduler_node)})
                    response_text = response.text if len(response.text) else None
                else:
                    logging.info("Checking for work: %s", url)
                    response = self.session.get(url, timeout=10, proxies=proxies)
                    response_text = response.text if len(response.text) else None
                if self.options.alive:
                    with open(self.options.alive, 'a'):
                        os.utime(self.options.alive, None)
                if self.health_check_server is not None:
                    self.health_check_server.healthy()
                self.first_failure = None
                if response_text is not None:
                    if response_text == 'Reboot':
                        self.reboot()
                        return None
                    elif response_text.startswith('Servers:') or response_text.startswith('Scheduler:'):
                        for line in response_text.splitlines():
                            line = line.strip()
                            if line.startswith('Servers:'):
                                servers_str = line[8:]
                                if servers_str and servers_str != self.work_servers_str:
                                    self.work_servers_str = servers_str
                                    self.work_servers = self.work_servers_str.split(',')
                                    logging.debug("Servers changed to: %s", self.work_servers_str)
                            elif line.startswith('Scheduler:'):
                                scheduler_parts = line[10:].split(' ')
                                if scheduler_parts and len(scheduler_parts) == 3:
                                    self.scheduler = scheduler_parts[0].strip()
                                    self.scheduler_salt = scheduler_parts[1].strip()
                                    self.scheduler_node = scheduler_parts[2].strip()
                                    self.scheduler_nodes = [self.scheduler_node]
                                    retry = True
                                    logging.debug("Scheduler configured: '%s' Salt: '%s' Node: %s", self.scheduler, self.scheduler_salt, self.scheduler_node)
                    job = self.process_job_json(json.loads(response_text))
                    # Store the raw job info in case we need to re-queue it
                    if job is not None and 'Test ID' in job and 'signature' in job and 'work_server' in job:
                        self.raw_job = {
                            'id': job['Test ID'],
                            'signature': job['signature'],
                            'work_server': job['work_server'],
                            'location': location,
                            'payload': str(response.text)
                        }
                        if 'jobID' in job:
                            self.raw_job['jobID'] = job['jobID']
                # Rotate through the list of locations
                if job is None and len(locations) > 0 and not self.scheduler:
                    location = str(locations.pop(0))
                    count -= 1
                    retry = True
                if job is None and len(scheduler_nodes) > 0 and self.scheduler:
                    self.scheduler_node = str(scheduler_nodes.pop(0)).strip(', ')
                    count -= 1
                    retry = True
            except requests.exceptions.RequestException as err:
                logging.critical("Get Work Error: %s", err.strerror)
                now = monotonic()
                if self.first_failure is None:
                    self.first_failure = now
                # Reboot if we haven't been able to reach the server for 30 minutes
                elapsed = now - self.first_failure
                if elapsed > 1800:
                    self.reboot()
                time.sleep(0.1)
            except Exception:
                pass
            # Rotate through the list of servers
            if not retry and job is None and len(servers) > 0 and not self.scheduler:
                self.url = str(servers.pop(0))
                locations = list(self.test_locations) if len(self.test_locations) > 1 else [self.location]
                random.shuffle(locations)
                location = str(locations.pop(0))
                count -= 1
                retry = True
        return job

    def notify_test_started(self, job):
        """Tell the server that we have started the test. Used when the queueing isn't handled directly by the server responsible for a test"""
        if 'work_server' in job and 'Test ID' in job:
            try:
                url = job['work_server'] + 'started.php?id=' + quote_plus(job['Test ID'])
                proxies = {"http": None, "https": None}
                self.session.get(url, timeout=30, proxies=proxies)
            except Exception:
                logging.exception('Error notifying test start')

    def get_task(self, job):
        """Create a task object for the next test run or return None if the job is done"""
        if self.is_dead:
            return None
        # Do the one-time setup at the beginning of a job
        if 'current_state' not in job:
            if not self.needs_zip:
                self.needs_zip = []
            if 'work_server' in job and 'jobID' in job:
                self.notify_test_started(job)
            self.install_extensions()
        self.report_diagnostics()
        task = None
        if self.log_handler is not None:
            try:
                self.log_handler.close()
                logging.getLogger().removeHandler(self.log_handler)
                self.log_handler = None
            except Exception:
                pass
        if 'current_state' not in job or not job['current_state']['done']:
            if 'run' in job:
                # Sharded test, running one run only
                if 'current_state' not in job:
                    job['current_state'] = {"run": int(re.search(r'\d+', str(job['run'])).group()),
                                            "repeat_view": False,
                                            "done": False}
                elif not job['current_state']['repeat_view'] and \
                        ('fvonly' not in job or not job['fvonly']):
                    job['current_state']['repeat_view'] = True
                else:
                    return task
            elif 'current_state' not in job:
                job['current_state'] = {"run": 1, "repeat_view": False, "done": False}
            elif not job['current_state']['repeat_view'] and \
                    ('fvonly' not in job or not job['fvonly']):
                job['current_state']['repeat_view'] = True
            else:
                if job['warmup'] > 0:
                    job['warmup'] -= 1
                else:
                    job['current_state']['run'] += 1
                job['current_state']['repeat_view'] = False
            if job['current_state']['run'] <= job['runs']:
                if 'Test ID' in job:
                    test_id = job['Test ID']
                else:
                    test_id = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(20))
                run = job['current_state']['run']
                profile_dir = '{0}.{1}.{2:d}'.format(self.profile_dir, test_id, run)
                task = {'id': test_id,
                        'run': run,
                        'cached': 1 if job['current_state']['repeat_view'] else 0,
                        'done': False,
                        'profile': profile_dir,
                        'error': None,
                        'log_data': True,
                        'activity_time': 3,
                        'combine_steps': False,
                        'video_directories': [],
                        'page_data': {'tester': self.pc_name, 'start_epoch': time.time()},
                        'navigated': False,
                        'page_result': None,
                        'script_step_count': 1}
                # Increase the activity timeout for high-latency tests
                if 'latency' in job:
                    try:
                        factor = int(min(job['latency'] / 100, 4))
                        if factor > 1:
                            task['activity_time'] *= factor
                    except Exception:
                        pass
                # Set up the task configuration options
                task['port'] = 9222 + (self.test_run_count % 500)
                task['task_prefix'] = "{0:d}".format(run)
                if task['cached']:
                    task['task_prefix'] += "_Cached"
                task['prefix'] = task['task_prefix']
                short_id = "{0}.{1}.{2}".format(task['id'], run, task['cached'])
                task['dir'] = os.path.join(self.workdir, short_id)
                if 'test_shared_dir' not in job:
                    job['test_shared_dir'] = os.path.join(self.workdir, task['id'])
                task['task_video_prefix'] = 'video_{0:d}'.format(run)
                if task['cached']:
                    task['task_video_prefix'] += "_cached"
                task['video_subdirectory'] = task['task_video_prefix']
                if os.path.isdir(task['dir']):
                    shutil.rmtree(task['dir'])
                os.makedirs(task['dir'])
                if not os.path.isdir(job['test_shared_dir']):
                    os.makedirs(job['test_shared_dir'])
                if not os.path.isdir(profile_dir):
                    os.makedirs(profile_dir)
                if job['current_state']['run'] == job['runs'] or 'run' in job:
                    if job['current_state']['repeat_view']:
                        job['current_state']['done'] = True
                        task['done'] = True
                    elif 'fvonly' in job and job['fvonly']:
                        job['current_state']['done'] = True
                        task['done'] = True
                if 'debug' in job and job['debug']:
                    task['debug_log'] = os.path.join(task['dir'], task['prefix'] + '_debug.log')
                    try:
                        self.log_handler = logging.FileHandler(task['debug_log'])
                        self.log_handler.setFormatter(self.log_formatter)
                        logging.getLogger().addHandler(self.log_handler)
                    except Exception:
                        pass
                if 'keepua' not in job or not job['keepua']:
                    task['AppendUA'] = 'PTST'
                    if 'UAModifier' in job:
                        task['AppendUA'] = job['UAModifier']
                    task['AppendUA'] += '/{0}'.format(self.version)
                if 'AppendUA' in job:
                    if 'AppendUA' in task:
                        task['AppendUA'] += ' ' + job['AppendUA']
                    else:
                        task['AppendUA'] = job['AppendUA']
                if 'AppendUA' in task:
                    task['AppendUA'] = task['AppendUA'].replace('%TESTID%', test_id)\
                                                       .replace('%RUN%', str(run))\
                                                       .replace('%CACHED%', str(task['cached']))\
                                                       .replace('%VERSION%', self.version)
                task['block'] = []
                if 'block' in job:
                    block_list = job['block'].split()
                    for block in block_list:
                        block = block.strip()
                        if len(block):
                            task['block'].append(block)
                if 'blockDomains' in job:
                    if 'host_rules' not in task:
                        task['host_rules'] = []
                    if 'block_domains' not in task:
                        task['block_domains'] = []
                    if 'dns_override' not in task:
                        task['dns_override'] = []
                    domains = re.split('[, ]', job['blockDomains'])
                    for domain in domains:
                        domain = domain.strip()
                        if len(domain) and domain.find('"') == -1:
                            task['block_domains'].append(domain)
                            task['host_rules'].append('"MAP {0} 127.0.0.1"'.format(domain))
                            if re.match(r'^[a-zA-Z0-9\-\.]+$', domain):
                                task['dns_override'].append([domain, "0.0.0.0"])
                # Load the crypto mining block list
                crypto_list = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'support', 'adblock', 'nocoin', 'hosts.txt')
                if os.path.exists(crypto_list):
                    with open(crypto_list, 'rt') as f_in:
                        if 'dns_override' not in task:
                            task['dns_override'] = []
                        for line in f_in:
                            if line.startswith('0.0.0.0'):
                                domain = line[8:].strip()
                                task['dns_override'].append([domain, "0.0.0.0"])
                self.build_script(job, task)
                task['width'] = job['width']
                task['height'] = job['height']
                if 'mobile' in job and job['mobile']:
                    if 'browser' in job and job['browser'] in self.margins:
                        task['width'] = \
                            job['width'] + max(self.margins[job['browser']]['width'], 0)
                        task['height'] = \
                            job['height'] + max(self.margins[job['browser']]['height'], 0)
                    else:
                        task['width'] = job['width'] + 20
                        task['height'] = job['height'] + 120
                if 'time' in job:
                    task['minimumTestSeconds'] = job['time']
                task['time_limit'] = job['timeout']
                task['test_time_limit'] = task['time_limit'] * task['script_step_count']
                task['stop_at_onload'] = bool('web10' in job and job['web10'])
                task['run_start_time'] = monotonic()
                if 'profile_data' in job:
                    task['profile_data'] = {
                        'lock': threading.Lock(),
                        'start': monotonic(),
                        'test':{
                            'id': task['id'],
                            'run': task['run'],
                            'cached': task['cached'],
                            's': 0}}
                # Keep the full resolution video frames if the browser window is smaller than 600px
                if 'thumbsize' not in job and (task['width'] < 600 or task['height'] < 600):
                    job['fullSizeVideo'] = 1
                # Pass-through the SaaS fields
                if 'saas_test_id' in job:
                    task['page_data']['saas_test_id'] = job['saas_test_id']
                    if 'saas_node_id' in job:
                        task['page_data']['saas_node_id'] = job['saas_node_id']
                    if 'saas_report_window_start' in job:
                        task['page_data']['saas_report_window_start'] = job['saas_report_window_start']
                    if 'saas_report_window_end' in job:
                        task['page_data']['saas_report_window_end'] = job['saas_report_window_end']
                    if 'saas_device_type_id' in job:
                        task['page_data']['saas_device_type_id'] = job['saas_device_type_id']
                    else:
                        task['page_data']['saas_device_type_id'] = 0
                self.test_run_count += 1
        if task is None and self.job is not None:
            self.upload_test_result()
        if 'reboot' in job and job['reboot']:
            self.reboot()
        return task

    def running_another_test(self, task):
        """Increment the port for Chrome and the run count"""
        task['port'] = 9222 + (self.test_run_count % 500)
        self.test_run_count += 1

    def build_script(self, job, task):
        """Build the actual script that will be used for testing"""
        task['script'] = []
        record_count = 0
        # Add script commands for any static options that need them
        if 'script' in job:
            lines = job['script'].splitlines()
            for line in lines:
                parts = line.split("\t", 2)
                if parts is not None and len(parts):
                    keep = True
                    record = False
                    command = parts[0].lower().strip()
                    target = parts[1].strip() if len(parts) > 1 else None
                    value = parts[2].strip() if len(parts) > 2 else None
                    andwait = command.find('andwait')
                    if andwait > -1:
                        command = command[:andwait]
                        record = True
                    # go through the known commands
                    if command == 'navigate':
                        if target is not None and target[:4] != 'http':
                            target = 'http://' + target
                        job['url'] = target
                        record = True
                    elif command == 'addheader' or command == 'setheader':
                        if target is not None and len(target):
                            separator = target.find(':')
                            if separator > 0:
                                name = target[:separator].strip()
                                header_value = target[separator + 1:].strip()
                                if 'headers' not in task:
                                    task['headers'] = {}
                                task['headers'][name] = header_value
                    elif command == 'overridehost':
                        if target and value:
                            if 'overrideHosts' not in task:
                                task['overrideHosts'] = {}
                            task['overrideHosts'][target] = value
                    elif command == 'setcookie' and target is not None and value is not None:
                        try:
                            url = target
                            cookie = value
                            pos = cookie.find(';')
                            if pos > 0:
                                cookie = cookie[:pos]
                            pos = cookie.find('=')
                            if pos > 0:
                                cookie_name = cookie[:pos].strip()
                                cookie_value = cookie[pos + 1:].strip()
                                if len(cookie_name) and len(cookie_value) and len(url):
                                    if 'cookies' not in task:
                                        task['cookies'] = []
                                    task['cookies'].append({'url': url,
                                                            'name': cookie_name,
                                                            'value': cookie_value})
                        except Exception:
                            logging.exception('Error setting cookie')
                    # commands that get pre-processed
                    elif command == 'setuseragent' and target is not None:
                        job['uastring'] = target
                    elif command == 'setbrowsersize':
                        keep = False
                        if target is not None and value is not None:
                            width = int(re.search(r'\d+', str(target)).group())
                            height = int(re.search(r'\d+', str(value)).group())
                            dpr = float(job['dpr']) if 'dpr' in job else 1.0
                            if width > 0 and height > 0 and width < 10000 and height < 10000:
                                job['width'] = int(float(width) / dpr)
                                job['height'] = int(float(height) / dpr)
                    elif command == 'setviewportsize':
                        keep = False
                        if target is not None and value is not None:
                            width = int(re.search(r'\d+', str(target)).group())
                            height = int(re.search(r'\d+', str(value)).group())
                            if width > 0 and height > 0 and width < 10000 and height < 10000:
                                job['width'] = width
                                job['height'] = height
                                # Adjust the viewport for non-mobile tests
                                if 'mobile' not in job or not job['mobile']:
                                    if 'browser' in job and job['browser'] in self.margins:
                                        job['width'] += \
                                            max(self.margins[job['browser']]['width'], 0)
                                        job['height'] += \
                                            max(self.margins[job['browser']]['height'], 0)
                                    else:
                                        job['adjust_viewport'] = True
                    elif command == 'setdevicescalefactor' and target is not None:
                        keep = False
                        job['dpr'] = target
                    elif command == 'settimeout':
                        keep = False
                        if target is not None:
                            time_limit = int(re.search(r'\d+', str(target)).group())
                            if time_limit > 0 and time_limit < 1200:
                                job['timeout'] = time_limit
                    elif command == 'blockdomains':
                        keep = False
                        if target is not None:
                            if 'block_domains' not in task:
                                task['block_domains'] = []
                            if 'host_rules' not in task:
                                task['host_rules'] = []
                            if 'dns_override' not in task:
                                task['dns_override'] = []
                            domains = re.split('[, ]', target)
                            for domain in domains:
                                domain = domain.strip()
                                if len(domain) and domain.find('"') == -1:
                                    task['block_domains'].append(domain)
                                    task['host_rules'].append('"MAP {0} 127.0.0.1"'.format(domain))
                                    if re.match(r'^[a-zA-Z0-9\-\.]+$', domain):
                                        task['dns_override'].append([domain, "127.0.0.1"])
                    elif command == 'blockdomainsexcept':
                        keep = False
                        if target is not None:
                            if 'block_domains_except' not in task:
                                task['block_domains_except'] = []
                            if 'host_rules' not in task:
                                task['host_rules'] = []
                            domains = target.split()
                            for domain in domains:
                                domain = domain.strip()
                                if len(domain) and domain.find('"') == -1:
                                    task['block_domains_except'].append(domain)
                                    task['host_rules'].append(
                                        '"MAP * 127.0.0.1, EXCLUDE {0}"'.format(domain))
                    elif command == 'block':
                        keep = False
                        if target is not None:
                            block_list = target.split()
                            for block in block_list:
                                block = block.strip()
                                if len(block):
                                    task['block'].append(block)
                    elif command == 'setdns':
                        keep = False
                        if target is not None and value is not None and len(target) and len(value):
                            if target.find('"') == -1 and value.find('"') == -1:
                                if 'dns_override' not in task:
                                    task['dns_override'] = []
                                if 'host_rules' not in task:
                                    task['host_rules'] = []
                                task['host_rules'].append('"MAP {0} {1}"'.format(target, value))
                                if re.match(r'^\d+\.\d+\.\d+\.\d+$', value) and \
                                        re.match(r'^[a-zA-Z0-9\-\.]+$', target):
                                    task['dns_override'].append([target, value])
                    elif command == 'setdnsname':
                        # Resolve the IP and treat it like a setdns command
                        keep = False
                        if target is not None and value is not None and len(target) and len(value):
                            addr = None
                            try:
                                result = socket.getaddrinfo(value, 80)
                                if result and len(result) > 0:
                                    for entry in result:
                                        if entry and len(entry) >= 5:
                                            sockaddr = entry[4]
                                            if sockaddr and len(sockaddr) >= 1:
                                                addr = sockaddr[0]
                                                break
                            except Exception:
                                logging.exception('Error resolving DNS for %s', value)
                            if addr is not None and target.find('"') == -1:
                                if 'dns_override' not in task:
                                    task['dns_override'] = []
                                if 'host_rules' not in task:
                                    task['host_rules'] = []
                                task['host_rules'].append('"MAP {0} {1}"'.format(target, addr))
                                if re.match(r'^\d+\.\d+\.\d+\.\d+$', addr) and \
                                        re.match(r'^[a-zA-Z0-9\-\.]+$', target):
                                    task['dns_override'].append([target, addr])
                    # Commands that get translated into exec commands
                    elif command in ['click', 'selectvalue', 'sendclick', 'setinnerhtml',
                                     'setinnertext', 'setvalue', 'submitform']:
                        if target is not None:
                            # convert the selector into a querySelector
                            separator = target.find('=')
                            if separator == -1:
                                separator = target.find("'")
                            if separator >= 0:
                                attribute = target[:separator]
                                attr_value = target[separator + 1:]
                                script = "document.querySelector('[{0}=\"{1}\"]')".format(
                                    attribute, attr_value)
                                if command in ['click', 'sendclick']:
                                    script += '.click();'
                                elif command == 'submitform' and attr_value is not None:
                                    script += '.submit();'
                                    record = True
                                elif command in ['setvalue', 'selectvalue'] and value is not None:
                                    script += '.value="{0}";'.format(value.replace('"', '\\"'))
                                elif command == 'setinnertext' and value is not None:
                                    script += '.innerText="{0}";'.format(value.replace('"', '\\"'))
                                elif command == 'setinnerhtml' and value is not None:
                                    script += '.innerHTML="{0}";'.format(value.replace('"', '\\"'))
                                command = 'exec'
                                target = script
                                value = None
                    if keep:
                        if record:
                            record_count += 1
                        task['script'].append({'command': command,
                                               'target': target,
                                               'value': value,
                                               'record': record})
        elif 'url' in job:
            if job['url'][:4] != 'http':
                job['url'] = 'http://' + job['url']
            record_count += 1
            task['script'].append({'command': 'navigate', 'target': job['url'], 'record': True})
        # Remove any spurious commands from the end of the script
        pos = len(task['script']) - 1
        while pos > 0:
            if task['script'][pos]['record']:
                break
            task['script'].pop(pos)
            pos -= 1
        task['script_step_count'] = max(record_count, 1)
        logging.debug(task['script'])

    def update_browser_viewport(self, task):
        """Update the browser border size based on the measured viewport"""
        if 'actual_viewport' in task and 'width' in task and 'height' in task and \
                self.job is not None and 'browser' in self.job:
            browser = self.job['browser']
            width = max(task['width'] - task['actual_viewport']['width'], 0)
            height = max(task['height'] - task['actual_viewport']['height'], 0)
            if browser not in self.margins or self.margins[browser]['width'] != width or \
                    self.margins[browser]['height'] != height:
                self.margins[browser] = {"width": width, "height": height}
                if not os.path.isdir(self.persistent_dir):
                    os.makedirs(self.persistent_dir)
                margins_file = os.path.join(self.persistent_dir, 'margins.json')
                with open(margins_file, 'w') as f_out:
                    json.dump(self.margins, f_out)

    def body_fetch_thread(self):
        """background thread to fetch bodies"""
        import requests
        session = requests.session()
        proxies = {"http": None, "https": None}
        try:
            while not self.is_dead:
                task = self.fetch_queue.get(5)
                if task is None:
                    break
                try:
                    url = task['url']
                    dest = task['file']
                    headers = {}
                    if isinstance(task['headers'], list):
                        for header in task['headers']:
                            separator = header.find(':', 2)
                            if separator >= 0:
                                header_name = header[:separator].strip()
                                value = header[separator + 1:].strip()
                                if header_name.lower() not in ["accept-encoding"] and \
                                        not header_name.startswith(':'):
                                    headers[header_name] = value
                    elif isinstance(task['headers'], dict):
                        for header_name in task['headers']:
                            value = task['headers'][header_name]
                            if header_name.lower() not in ["accept-encoding"] and \
                                    not header_name.startswith(':'):
                                headers[header_name] = value
                    logging.debug('Downloading %s to %s', url, dest)
                    response = session.get(url, headers=headers, stream=True,
                                           timeout=30, proxies=proxies)
                    if response.status_code == 200:
                        with open(dest, 'wb') as f_out:
                            for chunk in response.iter_content(chunk_size=4096):
                                f_out.write(chunk)
                        self.fetch_result_queue.put(task)
                except Exception:
                    pass
                self.fetch_queue.task_done()
        except Exception:
            pass
        self.fetch_result_queue.put(None)

    def get_bodies(self, task):
        """Fetch any bodies that are missing if response bodies were requested"""
        if self.is_dead:
            return
        self.profile_start(task, 'wpt.get_bodies')
        all_bodies = False
        html_body = False
        if 'bodies' in self.job and self.job['bodies']:
            all_bodies = True
        if 'htmlbody' in self.job and self.job['htmlbody']:
            html_body = True
        if not all_bodies and not html_body:
            return
        try:
            path_base = os.path.join(task['dir'], task['prefix'])
            path = os.path.join(task['dir'], 'bodies')
            netlog_path = os.path.join(task['dir'], 'netlog_bodies')
            requests = []
            devtools_file = os.path.join(task['dir'], task['prefix'] + '_devtools_requests.json.gz')
            with gzip.open(devtools_file, GZIP_READ_TEXT) as f_in:
                requests = json.load(f_in)
            count = 0
            bodies_zip = path_base + '_bodies.zip'
            if requests and 'requests' in requests:
                # See what bodies are already in the zip file
                body_index = 0
                bodies = []
                try:
                    with zipfile.ZipFile(bodies_zip, 'r') as zip_file:
                        files = zip_file.namelist()
                    for filename in files:
                        matches = re.match(r'^(\d\d\d)-(.*)-body.txt$', filename)
                        if matches:
                            index = int(matches.group(1))
                            request_id = str(matches.group(2))
                            if index > body_index:
                                body_index = index
                            bodies.append(request_id)
                except Exception:
                    logging.exception('Error matching requests to bodies')
                for request in requests['requests']:
                    if 'full_url' in request and \
                            request['full_url'].startswith('http') and \
                            'responseCode' in request \
                            and request['responseCode'] == 200 and \
                            request['full_url'].find('ocsp') == -1 and\
                            request['full_url'].find('.woff') == -1 and\
                            request['full_url'].find('.ttf') == -1 and\
                            'contentType' in request:
                        content_type = request['contentType'].lower()
                        need_body = False
                        if all_bodies:
                            if content_type.startswith('text/html') or \
                                    content_type.find('javascript') >= 0 or \
                                    content_type.find('json') >= 0:
                                need_body = True
                        elif html_body and content_type.startswith('text/html'):
                            need_body = True
                            html_body = False
                        if need_body:
                            body_id = str(request['id'])
                            if 'raw_id' in request:
                                body_id = str(request['raw_id'])
                            if body_id not in bodies:
                                count += 1
                                body_file_path = os.path.join(path, str(body_id))
                                netlog_file_path = None
                                if 'netlog_id' in request:
                                    netlog_file_path = os.path.join(netlog_path, str(request['netlog_id']))
                                headers = None
                                if 'headers' in request and 'request' in request['headers']:
                                    headers = request['headers']['request']
                                task = {'url': request['full_url'],
                                        'file': body_file_path,
                                        'id': body_id,
                                        'headers': headers}
                                if os.path.isfile(body_file_path) or os.path.isfile(netlog_file_path):
                                    if netlog_file_path is not None and os.path.isfile(netlog_file_path):
                                        task['netlog_file'] = netlog_file_path
                                    self.fetch_result_queue.put(task)
                                else:
                                    self.fetch_queue.put(task)
            if count:
                if not os.path.isdir(path):
                    os.makedirs(path)
                logging.debug("Fetching bodies for %d requests", count)
                threads = []
                thread_count = min(count, 10)
                for _ in range(thread_count):
                    self.fetch_queue.put(None)
                for _ in range(thread_count):
                    thread = threading.Thread(target=self.body_fetch_thread)
                    thread.daemon = True
                    thread.start()
                    threads.append(thread)
                for thread in threads:
                    thread.join(timeout=120)
                # Build a list of files to add to the zip archive
                bodies = []
                try:
                    while True:
                        task = self.fetch_result_queue.get(5)
                        if task is None:
                            thread_count -= 1
                            self.fetch_result_queue.task_done()
                            if thread_count == 0:
                                break
                        else:
                            file_path = task['netlog_file'] if 'netlog_file' in task else task['file']
                            if os.path.isfile(file_path):
                                # Only append if the data can be read as utf-8
                                try:
                                    with open(file_path, 'r', encoding='utf-8') as f_in:
                                        f_in.read()
                                    body_index += 1
                                    file_name = '{0:03d}-{1}-body.txt'.format(body_index, task['id'])
                                    bodies.append({'name': file_name, 'file': file_path})
                                    logging.debug('Added response body for %s', task['url'])
                                except Exception:
                                    logging.exception('Non-text body for %s', task['url'])
                            self.fetch_result_queue.task_done()
                except Exception:
                    pass
                # Add the files
                if bodies:
                    with zipfile.ZipFile(bodies_zip, 'a', zipfile.ZIP_DEFLATED) as zip_file:
                        for body in bodies:
                            zip_file.write(body['file'], body['name'])
        except Exception:
            logging.exception('Error backfilling bodies')
        self.profile_end(task, 'wpt.get_bodies')

    def upload_test_result(self):
        """Upload the full result if the test is not being sharded"""
        if self.is_dead:
            return
        if self.job is not None and 'run' not in self.job:
            # If we are writing the test result directly to Google cloud storage, generate the relevant testinfo
            if 'gcs_test_archive' in self.job:
                self.generate_test_info()
            # Write out the testinfo ini and json files if they are part of the job
            if 'testinfo_ini' in self.job:
                from datetime import datetime
                self.job['testinfo_ini'] = self.job['testinfo_ini'].replace('[test]', '[test]\r\ncompleteTime={}'.format(datetime.now().strftime("%m/%d/%y %H:%M:%S")))
                ini_path = os.path.join(self.workdir, 'testinfo.ini')
                with open(ini_path, 'wt') as f_out:
                    f_out.write(self.job['testinfo_ini'])
                self.needs_zip.append({'path': ini_path, 'name': 'testinfo.ini'})
            if 'testinfo' in self.job:
                self.job['testinfo']['completed'] = time.time()
                if 'test_runs' not in self.job['testinfo']:
                    self.job['testinfo']['test_runs'] = {}
                if 'runs' in self.job['testinfo']:
                    max_steps = 0
                    for run in range(self.job['testinfo']['runs']):
                        run_num = run + 1
                        # Count the number of steps in the test data
                        step_count = 0
                        if self.needs_zip:
                            for zipitem in self.needs_zip:
                                matches = re.match(r'^(\d+)_(\d+)_', zipitem['name'])
                                if matches and run_num == int(matches.group(1)):
                                    step = int(matches.group(2))
                                    if step > step_count:
                                        step_count = step
                        run_info = {'done': True}
                        if step_count > 0:
                            run_info['steps'] = step_count
                            if step_count > max_steps:
                                max_steps = step_count
                        self.job['testinfo']['test_runs'][run_num] = run_info
                    self.job['testinfo']['steps'] = max_steps
                json_path = os.path.join(self.workdir, 'testinfo.json')
                with open(json_path, 'wt') as f_out:
                    json.dump(self.job['testinfo'], f_out)
                self.needs_zip.append({'path': json_path, 'name': 'testinfo.json'})

            # Zip the files
            zip_path = None
            if len(self.needs_zip):
                zip_path = os.path.join(self.workdir, "result.zip")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zip_file:
                    for zipitem in self.needs_zip:
                        logging.debug('Storing %s (%d bytes)', zipitem['name'], os.path.getsize(zipitem['path']))
                        zip_file.write(zipitem['path'], zipitem['name'])
                        try:
                            os.remove(zipitem['path'])
                        except Exception:
                            pass
            
            # If we are writing the results directly to GCS, don't post to workdone
            if zip_path is not None and 'Test ID' in self.job and \
                    'gcs_test_archive' in self.job and \
                    'bucket' in self.job['gcs_test_archive'] and \
                    'path' in self.job['gcs_test_archive']:
                try:
                    from google.cloud import storage
                    client = storage.Client()
                    bucket = client.get_bucket(self.job['gcs_test_archive']['bucket'])
                    gcs_path = os.path.join(self.job['gcs_test_archive']['path'], self.job['Test ID'] + '.zip')
                    blob = bucket.blob(gcs_path)
                    blob.upload_from_filename(filename=zip_path)
                    logging.debug('Uploaded test to gs://%s/%s', self.job['gcs_test_archive']['bucket'], gcs_path)
                except Exception:
                    logging.exception('Error uploading result to Cloud Storage')
            else:
                # Send the result to WebPageTest
                data = {'location': self.location,
                        'pc': self.pc_name,
                        'testinfo': '1',
                        'done': '1'}
                if 'Test ID' in self.job:
                    data['id'] = self.job['Test ID']
                if self.key is not None:
                    data['key'] = self.key
                if self.instance_id is not None:
                    data['ec2'] = self.instance_id
                if self.zone is not None:
                    data['ec2zone'] = self.zone
                if self.cpu_pct is not None:
                    data['cpu'] = '{0:0.2f}'.format(self.cpu_pct)
                if 'error' in self.job:
                    data['error'] = self.job['error']
                uploaded = False
                if 'work_server' in self.job:
                    uploaded = self.post_data(self.job['work_server'] + "workdone.php", data, zip_path, 'result.zip')
                if not uploaded:
                    self.post_data(self.url + "workdone.php", data, zip_path, 'result.zip')
            
            # See if the job needs to be posted to a retry pubsub queue
            if self.options.pubsub:
                from google.cloud import pubsub_v1
                if 'pubsub_retry_queue' in self.job and 'success' in self.job and not self.job['success']:
                    try:
                        from concurrent import futures
                        logging.debug('Sending test to retry queue: %s', self.job['pubsub_retry_queue'])
                        publisher = pubsub_v1.PublisherClient()
                        job_str = json.dumps(self.raw_job)
                        publisher_future = publisher.publish(self.job['pubsub_retry_queue'], job_str.encode())
                        futures.wait([publisher_future], return_when=futures.ALL_COMPLETED)
                    except Exception:
                        logging.exception('Error sending job to pubsub retry queue')
                elif 'pubsub_completed_queue' in self.job and self.job.get('success'):
                    try:
                        from concurrent import futures
                        logging.debug('Sending test to completed queue: %s', self.job['pubsub_completed_queue'])
                        publisher = pubsub_v1.PublisherClient()
                        if 'results' in self.job:
                            self.raw_job['results'] = self.job['results']
                        job_str = json.dumps(self.raw_job)
                        publisher_future = publisher.publish(self.job['pubsub_completed_queue'], job_str.encode())
                        futures.wait([publisher_future], return_when=futures.ALL_COMPLETED)
                    except Exception:
                        logging.exception('Error sending job to pubsub completed queue')
        self.raw_job = None
        self.needs_zip = []
        # Clean up the work directory
        if os.path.isdir(self.workdir):
            try:
                shutil.rmtree(self.workdir)
            except Exception:
                pass
        self.scheduler_job_done()
        #self.license_ping()

    def scheduler_job_done(self):
        """Signal to the scheduler that the test is complete"""
        if self.job is not None and 'jobID' in self.job and self.scheduler and self.scheduler_salt and self.scheduler_node and not self.is_dead:
            try:
                proxies = {"http": None, "https": None}
                url = self.scheduler + 'hawkscheduleserver/wpt-test-update.ashx'
                payload = '{"test":"' + self.job['jobID'] +'","update":0}'
                self.session.post(url, headers={'CPID': self.get_cpid(self.scheduler_node), 'Content-Type': 'application/json'}, data=payload, proxies=proxies, timeout=30)
            except Exception:
                logging.exception("Error reporting job done to scheduler")

    def collect_crux_data(self, task):
        """Collect CrUX data for the URL that was tested"""
        if self.job is not None and 'url' in self.job and 'crux_api_key' in self.job:
            form_factor = 'DESKTOP'
            if self.options.iOS or self.options.android:
                form_factor = 'PHONE'
            if 'mobile' in self.job and self.job['mobile']:
                form_factor = 'PHONE'
            if 'browser' in self.job:
                if self.job['browser'].startswith('iPhone') or self.job['browser'].startswith('iPod'):
                    form_factor = 'PHONE'
            try:
                proxies = {"http": None, "https": None}
                url = 'https://chromeuxreport.googleapis.com/v1/records:queryRecord?key=' + self.job['crux_api_key']
                test_url = self.job['url']
                if not test_url.startswith('http'):
                    test_url = 'http://' + test_url
                req = {
                    'url': test_url,
                    'formFactor': form_factor
                }
                payload = json.dumps(req)
                logging.debug(payload)
                response = self.session.post(url, headers={'Content-Type': 'application/json'}, data=payload, proxies=proxies, timeout=30)
                if response:
                    crux_data = response.text
                    if crux_data and len(crux_data):
                        logging.debug(crux_data)
                        path = os.path.join(task['dir'], 'crux.json.gz')
                        with gzip.open(path, GZIP_TEXT, 7) as outfile:
                            outfile.write(crux_data)
            except Exception:
                logging.exception("Error fetching CrUX data")

    def upload_task_result(self, task):
        """Upload the result of an individual test run if it is being sharded"""
        if self.is_dead:
            return
        logging.info('Uploading result')
        self.profile_start(task, 'wpt.upload')
        self.cpu_pct = None
        self.update_browser_viewport(task)
        if task['run'] == 1 and not task['cached']:
            self.collect_crux_data(task)
        # Post-process the given test run
        try:
            from internal.process_test import ProcessTest
            ProcessTest(self.options, self.job, task)
        except Exception:
            logging.exception('Error post-processing test')
        # Stop logging to the file
        if self.log_handler is not None:
            try:
                self.log_handler.close()
                logging.getLogger().removeHandler(self.log_handler)
                self.log_handler = None
            except Exception:
                pass
        if 'debug_log' in task and os.path.isfile(task['debug_log']):
            debug_out = task['debug_log'] + '.gz'
            with open(task['debug_log'], 'rb') as f_in:
                with gzip.open(debug_out, 'wb', 7) as f_out:
                    shutil.copyfileobj(f_in, f_out)
            try:
                os.remove(task['debug_log'])
            except Exception:
                pass
        if self.job['warmup'] > 0:
            logging.debug('Discarding warmup run')
        else:
            # Continue with the upload
            if 'page_data' in task and 'fullyLoadedCPUpct' in task['page_data']:
                self.cpu_pct = task['page_data']['fullyLoadedCPUpct']
            data = {'id': task['id'],
                    'location': self.location,
                    'run': str(task['run']),
                    'cached': str(task['cached']),
                    'testinfo': '1',
                    'pc': self.pc_name}
            if self.key is not None:
                data['key'] = self.key
            if self.instance_id is not None:
                data['ec2'] = self.instance_id
            if self.zone is not None:
                data['ec2zone'] = self.zone
            if 'run' in self.job:
                self.needs_zip = []
            zip_path = None
            if os.path.isdir(task['dir']):
                # upload any video images
                if bool(self.job['video']) and len(task['video_directories']):
                    for video_subdirectory in task['video_directories']:
                        video_dir = os.path.join(task['dir'], video_subdirectory)
                        if os.path.isdir(video_dir):
                            for filename in os.listdir(video_dir):
                                filepath = os.path.join(video_dir, filename)
                                if os.path.isfile(filepath):
                                    name = video_subdirectory + '/' + filename
                                    self.needs_zip.append({'path': filepath, 'name': name})
                # Upload the separate files
                for filename in os.listdir(task['dir']):
                    filepath = os.path.join(task['dir'], filename)
                    if os.path.isfile(filepath):
                        # Delete any video files that may have squeaked by
                        if not self.job['keepvideo'] and filename[-4:] == '.mp4' and \
                                filename.find('rendered_video') == -1:
                            try:
                                os.remove(filepath)
                            except Exception:
                                pass
                        else:
                            self.needs_zip.append({'path': filepath, 'name': filename})
                # Zip the files
                if len(self.needs_zip) and 'run' in self.job:
                    zip_path = os.path.join(task['dir'], "result.zip")
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zip_file:
                        for zipitem in self.needs_zip:
                            logging.debug('Storing %s (%d bytes)', zipitem['name'], os.path.getsize(zipitem['path']))
                            zip_file.write(zipitem['path'], zipitem['name'])
                            try:
                                os.remove(zipitem['path'])
                            except Exception:
                                pass
                    self.needs_zip = []
            # Post the workdone event for the task (with the zip attached)
            if 'run' in self.job:
                if task['done']:
                    data['done'] = '1'
                if task['error'] is not None:
                    data['error'] = task['error']
                if self.cpu_pct is not None:
                    data['cpu'] = '{0:0.2f}'.format(self.cpu_pct)
                uploaded = False
                if 'work_server' in self.job:
                    uploaded = self.post_data(self.job['work_server'] + "workdone.php", data, zip_path, 'result.zip')
                if not uploaded:
                    self.post_data(self.url + "workdone.php", data, zip_path, 'result.zip')
            else:
                # Keep track of test-level errors for reporting
                if task['error'] is not None:
                    self.job['error'] = task['error']
        # Clean up so we don't leave directories lying around
        if os.path.isdir(task['dir']) and 'run' in self.job:
            try:
                shutil.rmtree(task['dir'])
            except Exception:
                pass
        self.profile_end(task, 'wpt.upload')
        if 'profile_data' in task:
            try:
                self.profile_end(task, 'test')
                del task['profile_data']['start']
                del task['profile_data']['lock']
                raw_data = json.dumps(task['profile_data'])
                logging.debug("%s", raw_data)
                self.session.post(self.job['profile_data'], raw_data)
            except Exception:
                logging.exception('Error uploading profile data')
            del task['profile_data']

    def post_data(self, url, data, file_path=None, filename=None):
        """Send a multi-part post"""
        if self.is_dead:
            return False
        ret = True
        # pass the data fields as query params and any files as post data
        url += "?"
        for key in data:
            if data[key] != None:
                url += key + '=' + quote_plus(data[key]) + '&'
        logging.debug(url)
        response = None
        try:
            if file_path is not None and os.path.isfile(file_path):
                logging.debug('Uploading filename : %d bytes', os.path.getsize(file_path))
                response = self.session.post(url,
                                  files={'file': (filename, open(file_path, 'rb'))},
                                  timeout=600)
            else:
                response = self.session.post(url, timeout=600)
        except Exception:
            logging.exception("Upload Exception")
            ret = False
        if ret and response is not None:
            self.last_test_id = response.text
        return ret

    def license_ping(self):
        """Ping the license server"""
        if not self.license_pinged and not self.is_dead:
            self.license_pinged = True
            parts = urlsplit(self.url)
            data = {
                'loc': self.location,
                'server': parts.netloc
            }
            if self.wpthost:
                data['wpthost'] = self.wpthost
            self.post_data('https://license.webpagetest.org/', data)

    def profile_start(self, task, event_name):
        if task is not None and 'profile_data' in task:
            with task['profile_data']['lock']:
                task['profile_data'][event_name] = {'s': round(monotonic() - task['profile_data']['start'], 3)}

    def profile_end(self, task, event_name):
        if task is not None and 'profile_data' in task:
            with task['profile_data']['lock']:
                if event_name in task['profile_data']:
                    task['profile_data'][event_name]['e'] = round(monotonic() - task['profile_data']['start'], 3)
                    task['profile_data'][event_name]['d'] = round(task['profile_data'][event_name]['e'] - task['profile_data'][event_name]['s'], 3)

    def report_diagnostics(self):
        """Send a periodic diagnostics report"""
        if self.is_dead:
            return
        # Don't report more often than once per minute
        now = monotonic()
        if self.last_diagnostics and now < self.last_diagnostics + 60:
            return
        import psutil
        self.last_diagnostics = now
        cpu = self.cpu_pct if self.cpu_pct else psutil.cpu_percent(interval=1)
        # Ping the scheduler diagnostics endpoint
        if self.scheduler and self.scheduler_salt and len(self.scheduler_nodes) > 0:
            for node in self.scheduler_nodes:
                try:
                    import json as json_native
                    disk = psutil.disk_usage(__file__)
                    mem = psutil.virtual_memory()
                    ver = platform.uname()
                    os = '{0} {1}'.format(ver[0], ver[2])
                    cpu = min(max(int(round(cpu)), 0), 100)
                    info = {
                        'Machine': self.pc_name,
                        'Version': self.version,
                        'Instance': self.instance_id if self.instance_id else '',
                        'Cpu': cpu,
                        'Memcap': mem.total,
                        'Memused': mem.total - mem.available,
                        'Diskcap': disk.total,
                        'Diskused': disk.used,
                        'Os': os
                    }
                    payload = json_native.dumps(info, separators=(',', ':'))
                    logging.debug(payload)
                    proxies = {"http": None, "https": None}
                    url = self.scheduler + 'hawkscheduleserver/wpt-diagnostics.ashx'
                    response = self.session.post(url, headers={'CPID': self.get_cpid(node), 'Content-Type': 'application/json'}, data=payload, proxies=proxies, timeout=30)
                    logging.debug(response.headers)
                except Exception:
                    logging.exception('Error reporting diagnostics')
        # Ping the WPT servers if there are multiple (a single doesn't need a separate ping)
        if len(self.work_servers) and len(self.test_locations):
            try:
                from .os_util import get_free_disk_space
                proxies = {"http": None, "https": None}
                for server in self.work_servers:
                    for location in self.test_locations:
                        url = server + 'ping.php?'
                        url += "location=" + quote_plus(location)
                        url += "&pc=" + quote_plus(self.pc_name)
                        url += "&cpu={0:0.2f}".format(cpu)
                        if self.key is not None:
                            url += "&key=" + quote_plus(self.key)
                        if self.instance_id is not None:
                            url += "&ec2=" + quote_plus(self.instance_id)
                        if self.zone is not None:
                            url += "&ec2zone=" + quote_plus(self.zone)
                        if self.options.android:
                            url += '&apk=1'
                        url += '&version={0}'.format(self.version)
                        if self.screen_width is not None:
                            url += '&screenwidth={0:d}'.format(self.screen_width)
                        if self.screen_height is not None:
                            url += '&screenheight={0:d}'.format(self.screen_height)
                        if self.dns_servers is not None:
                            url += '&dns=' + quote_plus(self.dns_servers)
                        free_disk = get_free_disk_space()
                        url += '&freedisk={0:0.3f}'.format(free_disk)
                        uptime = self.get_uptime_minutes()
                        if uptime is not None:
                            url += '&upminutes={0:d}'.format(uptime)
                        if self.job is not None and 'Test ID' in self.job:
                            url += '&test=' + quote_plus(self.job['Test ID'])
                        try:
                            self.session.get(url, timeout=5, proxies=proxies)
                        except Exception:
                            pass
            except Exception:
                logging.exception('Error reporting diagnostics')

    def generate_test_info(self):
        """Generate the testinfo ini and json files needed for a test"""
        if 'testinfo' not in self.job:
            self.job['testinfo'] = dict(self.job)
        test = self.job['testinfo']
        test['id'] = self.job['Test ID']
        test['completed'] = time.time()
        if 'started' not in test:
            test['started'] = test['completed']
        if 'Capture Video' in test and test['Capture Video']:
            test['video'] = 1
        if 'pngScreenShot' in test and test['pngScreenShot']:
            test['pngss'] = 1
        if 'imageQuality' in test and test['imageQuality']:
            test['iq'] = test['imageQuality']
        if 'clearRV' in test and test['clearRV']:
            test['clear_rv'] = 1
        test['published'] = 1

        if 'locationText' not in test:
            test['locationText'] = 'WebPageTest Test Location'
        if 'location' not in test:
            test['location'] = 'TestLocation'

        # Generate the ini file string
        ini = "[test]\r\n"
        for key in ['fvonly', 'timeout', 'runs', 'id', 'sensitive', 'connections', 'notify',
                    'disable_video', 'uid', 'owner', 'type', 'connectivity', 'bwIn', 'bwOut',
                    'latency', 'plr', 'video']:
            if key in test:
                ini += "{}={}\r\n".format(key, test[key])
        ini += "{}={}\r\n".format('location', test['locationText'])
        ini += "{}={}\r\n".format('loc', test['location'])
        if 'login' in test and test['login']:
            ini += "authenticated=1\r\n"
        if 'script' in test and test['script']:
            ini += "script=1\r\n"
        self.job['testinfo_ini'] = ini

    def shutdown(self):
        """Agent is dying.  Re-queue the test if possible and if we have one"""
        if not self.is_dead:
            self.is_dead = True
            # requeue the raw test through the original server
            if self.raw_job is not None and 'work_server' in self.raw_job:
                url = self.raw_job['work_server'] + 'requeue.php?id=' + quote_plus(self.raw_job['id'])
                url += '&sig=' + quote_plus(self.raw_job['signature'])
                url += '&location=' + quote_plus(self.raw_job['location'])
                if self.scheduler_node is not None:
                    url += '&node=' + quote_plus(self.scheduler_node)
                if 'jobID' in self.raw_job:
                    url += '&jobID=' + quote_plus(self.raw_job['jobID'])
                proxies = {"http": None, "https": None}
                self.session.post(url, headers={'Content-Type': 'text/plain'}, data=self.raw_job['payload'], timeout=30, proxies=proxies)
                self.scheduler_job_done()

    def install_extensions(self):
        """Download and cache the requested extensions from the Chrome web store"""
        if self.job is not None and 'extensions' in self.job:
            now = time.time()
            cache_time = 604800 # Default to a one-week extension cache
            if 'extensions_cache_time' in self.job:
                try:
                    cache_time = int(self.job['extensions_cache_time'])
                except Exception:
                    logging.exception('Error setting extension cache time')
            expired = now - cache_time
            extensions_dir = os.path.join(self.persistent_dir, 'extensions')
            if not os.path.exists(extensions_dir):
                try:
                    os.makedirs(extensions_dir, exist_ok=True)
                except Exception:
                    pass
            extensions = self.job['extensions'].split(',')
            for extension in extensions:
                extension = extension.strip()
                if extension.isalnum():
                    extension_dir = os.path.join(extensions_dir, extension)
                    needs_update = True
                    if os.path.exists(extension_dir) and os.path.getmtime(extension_dir) > expired:
                        needs_update = False
                    if needs_update:
                        logging.debug('Updating extension: %s', extension)
                        self.download_extension(extension, extension_dir)

    def download_extension(self, id, dest_dir):
        """Download the given extension ID to the dest directory"""
        try:
            url = 'https://clients2.google.com/service/update2/crx?response=redirect&acceptformat=crx2,crx3'
            url += '&prod=chromium&prodchannel=unknown&prodversion=100.0.4896.127&lang=en-US'
            url += '&x=id%3D' + id + '%26installsource%3Dondemand%26uc'
            if platform.system() == 'Linux':
                url += '&os=linux&arch=x64&os_arch=x86_64&nacl_arch=x86-64'
            temp_file = dest_dir + '.zip'
            if os.path.exists(temp_file):
                os.unlink(temp_file)
            proxies = {"http": None, "https": None}
            ok = True
            with open(temp_file, 'wb') as f:
                try:
                    response = self.extension_session.get(url, timeout=600, allow_redirects=True, proxies=proxies)
                    for chunk in response.iter_content(chunk_size=512 * 1024):
                        if chunk:
                            f.write(chunk)
                except Exception:
                    logging.exception('Error downloading extension from %s', url)
                    ok = False
            if ok and os.path.exists(temp_file) and os.path.getsize(temp_file):
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir)
                os.makedirs(dest_dir)
                with zipfile.ZipFile(temp_file, 'r') as zip_file:
                    zip_file.extractall(dest_dir)
        except Exception:
            logging.exception('Error downloading extension %s', id)
