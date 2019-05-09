# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for interfacing with WebPageTest server"""
from datetime import datetime
import gzip
import logging
import multiprocessing
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib
import zipfile
import psutil
import monotonic
import ujson as json

DEFAULT_JPEG_QUALITY = 30

class WebPageTest(object):
    """Controller for interfacing with the WebPageTest server"""
    # pylint: disable=E0611
    def __init__(self, options, workdir):
        import requests
        self.fetch_queue = multiprocessing.JoinableQueue()
        self.fetch_result_queue = multiprocessing.JoinableQueue()
        self.job = None
        self.first_failure = None
        self.session = requests.Session()
        self.options = options
        self.fps = options.fps
        self.test_run_count = 0
        self.log_formatter = logging.Formatter(fmt="%(asctime)s.%(msecs)03d - %(message)s",
                                               datefmt="%H:%M:%S")
        self.log_handler = None
        # Configurable options
        self.url = options.server
        self.location = ''
        self.test_locations = []
        if options.location is not None:
            self.test_locations = options.location.split(',')
            self.location = str(self.test_locations[0])
        self.key = options.key
        self.time_limit = 120
        self.cpu_scale_multiplier = None
        # get the hostname or build one automatically if we are on a vmware system
        # (specific MAC address range)
        hostname = platform.uname()[1]
        interfaces = psutil.net_if_addrs()
        if interfaces is not None:
            logging.debug('Interfaces:')
            logging.debug(interfaces)
            for interface in interfaces:
                iface = interfaces[interface]
                for addr in iface:
                    match = re.search(r'^00[\-:]50[\-:]56[\-:]00[\-:]'
                                      r'([\da-fA-F]+)[\-:]([\da-fA-F]+)$', addr.address)
                    if match:
                        server = match.group(1)
                        machine = match.group(2)
                        hostname = 'VM{0}-{1}'.format(server, machine)
        self.pc_name = hostname if options.name is None else options.name
        self.auth_name = options.username
        self.auth_password = options.password if options.password is not None else ''
        self.validate_server_certificate = options.validcertificate
        self.instance_id = None
        self.zone = None
        # Get the screen resolution if we're in desktop mode
        self.screen_width = None
        self.screen_height = None
        if not self.options.android and not self.options.iOS:
            if self.options.xvfb:
                self.screen_width = 1920
                self.screen_height = 1200
            elif platform.system() == 'Windows':
                try:
                    from win32api import GetSystemMetrics
                    self.screen_width = GetSystemMetrics(0)
                    self.screen_height = GetSystemMetrics(1)
                except Exception:
                    pass
            elif platform.system() == 'Darwin':
                try:
                    from AppKit import NSScreen
                    self.screen_width = int(NSScreen.screens()[0].frame().size.width)
                    self.screen_height = int(NSScreen.screens()[0].frame().size.height)
                except Exception:
                    pass
        # See if we have to load dynamic config options
        if self.options.ec2:
            self.load_from_ec2()
        elif self.options.gce:
            self.load_from_gce()
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
        self.version = '19.04'
        try:
            directory = os.path.abspath(os.path.dirname(__file__))
            out = subprocess.check_output('git log -1 --format=%cd --date=raw',
                                          shell=True, cwd=directory)
            if out is not None:
                matches = re.search(r'^(\d+)', out)
                if matches:
                    timestamp = int(matches.group(1))
                    git_date = datetime.utcfromtimestamp(timestamp)
                    self.version = git_date.strftime('%y%m%d.%H%m%S')
        except Exception:
            pass
        # Load the discovered browser margins
        self.margins = {}
        margins_file = os.path.join(self.persistent_dir, 'margins.json')
        if os.path.isfile(margins_file):
            with open(margins_file, 'rb') as f_in:
                self.margins = json.load(f_in)
        # Override the public webpagetest server automatically
        if self.url is not None and self.url.find('www.webpagetest.org') >= 0:
            self.url = 'http://agent.webpagetest.org/work/'
    # pylint: enable=E0611

    def benchmark_cpu(self):
        """Benchmark the CPU for mobile emulation"""
        self.cpu_scale_multiplier = 1.0
        if not self.options.android and not self.options.iOS:
            import hashlib
            logging.debug('Starting CPU benchmark')
            hash_val = hashlib.sha256()
            with open(__file__, 'rb') as f_in:
                hash_data = f_in.read(4096)
            start = monotonic.monotonic()
            # 106k iterations takes ~1 second on the reference machine
            for _ in xrange(106000):
                hash_val.update(hash_data)
            elapsed = monotonic.monotonic() - start
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
                response = session.get('http://169.254.169.254/latest/user-data',
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
                response = session.get('http://169.254.169.254/latest/meta-data/instance-id',
                                       timeout=30, proxies=proxies)
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
                response = session.get(
                    'http://169.254.169.254/latest/meta-data/placement/availability-zone',
                    timeout=30, proxies=proxies)
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

    def load_from_gce(self):
        """Load config settings from GCE user data"""
        import requests
        session = requests.Session()
        proxies = {"http": None, "https": None}
        ok = False
        while not ok:
            try:
                response = session.get(
                    'http://169.254.169.254/computeMetadata/v1/instance/attributes/wpt_data',
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
                response = session.get('http://169.254.169.254/computeMetadata/v1/instance/id',
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
                        if re.search(r'^https?://', value):
                            self.url = value
                            if value.endswith('/'):
                                self.url += 'work/'
                            else:
                                self.url += '/work/'
                        else:
                            self.url = 'http://{0}/work/'.format(value)
                    if key == 'wpt_url':
                        self.url = value
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
                    elif key == 'wpt_fps':
                        self.fps = int(re.search(r'\d+', str(value)).group())
                    elif key == 'fps':
                        self.fps = int(re.search(r'\d+', str(value)).group())
            except Exception:
                pass

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
        if platform.system() == 'Windows':
            subprocess.call(['shutdown', '/r', '/f'])
        else:
            subprocess.call(['sudo', 'reboot'])

    def get_test(self):
        """Get a job from the server"""
        import requests
        proxies = {"http": None, "https": None}
        from .os_util import get_free_disk_space
        if self.cpu_scale_multiplier is None:
            self.benchmark_cpu()
        if self.url is None:
            return None
        job = None
        locations = list(self.test_locations) if len(self.test_locations) > 1 else [self.location]
        location = str(locations.pop(0))
        # Shuffle the list order
        if len(self.test_locations) > 1:
            self.test_locations.append(str(self.test_locations.pop(0)))
        count = 0
        retry = True
        while count < 3 and retry:
            retry = False
            count += 1
            url = self.url + "getwork.php?f=json&shards=1&reboot=1"
            url += "&location=" + urllib.quote_plus(location)
            url += "&pc=" + urllib.quote_plus(self.pc_name)
            if self.key is not None:
                url += "&key=" + urllib.quote_plus(self.key)
            if self.instance_id is not None:
                url += "&ec2=" + urllib.quote_plus(self.instance_id)
            if self.zone is not None:
                url += "&ec2zone=" + urllib.quote_plus(self.zone)
            if self.options.android:
                url += '&apk=1'
            url += '&version={0}'.format(self.version)
            if self.screen_width is not None:
                url += '&screenwidth={0:d}'.format(self.screen_width)
            if self.screen_height is not None:
                url += '&screenheight={0:d}'.format(self.screen_height)
            free_disk = get_free_disk_space()
            url += '&freedisk={0:0.3f}'.format(free_disk)
            uptime = self.get_uptime_minutes()
            if uptime is not None:
                url += '&upminutes={0:d}'.format(uptime)
            logging.info("Checking for work: %s", url)
            try:
                response = self.session.get(url, timeout=30, proxies=proxies)
                if self.options.alive:
                    with open(self.options.alive, 'a'):
                        os.utime(self.options.alive, None)
                self.first_failure = None
                if len(response.text):
                    if response.text == 'Reboot':
                        self.reboot()
                        return None
                    job = response.json()
                    logging.debug("Job: %s", json.dumps(job))
                    # set some default options
                    job['agent_version'] = self.version
                    if 'imageQuality' not in job:
                        job['imageQuality'] = DEFAULT_JPEG_QUALITY
                    if 'pngScreenShot' not in job:
                        job['pngScreenShot'] = 0
                    if 'fvonly' not in job:
                        job['fvonly'] = 0
                    if 'width' not in job:
                        job['width'] = 1024
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
                    if 'Test ID' not in job or 'browser' not in job or 'runs' not in job:
                        job = None
                    if 'type' not in job:
                        job['type'] = ''
                    if job['type'] == 'traceroute':
                        job['fvonly'] = 1
                    if 'fps' not in job:
                        job['fps'] = self.fps
                    if 'warmup' not in job:
                        job['warmup'] = 0
                    if job['type'] == 'lighthouse':
                        job['fvonly'] = 1
                        job['lighthouse'] = 1
                    job['keep_lighthouse_trace'] = \
                        bool('lighthouseTrace' in job and job['lighthouseTrace'])
                    job['lighthouse_throttle'] = \
                        bool('lighthouseThrottle' in job and job['lighthouseThrottle'])
                    job['video'] = bool('Capture Video' in job and job['Capture Video'])
                    job['keepvideo'] = bool('keepvideo' in job and job['keepvideo'])
                    job['disable_video'] = bool(not job['video'] and
                                                'disable_video' in job and
                                                job['disable_video'])
                    job['interface'] = None
                    job['persistent_dir'] = self.persistent_dir
                    if 'throttle_cpu' in job:
                        throttle = float(re.search(r'\d+\.?\d*', str(job['throttle_cpu'])).group())
                        throttle *= self.cpu_scale_multiplier
                        job['throttle_cpu_requested'] = job['throttle_cpu']
                        job['throttle_cpu'] = throttle
                if job is None and len(locations) > 0:
                    location = str(locations.pop(0))
                    retry = True
            except requests.exceptions.RequestException as err:
                logging.critical("Get Work Error: %s", err.strerror)
                retry = True
                now = monotonic.monotonic()
                if self.first_failure is None:
                    self.first_failure = now
                # Reboot if we haven't been able to reach the server for 30 minutes
                elapsed = now - self.first_failure
                if elapsed > 1800:
                    self.reboot()
                time.sleep(0.1)
            except Exception:
                pass
        self.job = job
        return job

    def get_task(self, job):
        """Create a task object for the next test run or return None if the job is done"""
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
                test_id = job['Test ID']
                run = job['current_state']['run']
                profile_dir = '{0}.{1}.{2:d}'.format(self.profile_dir, test_id, run)
                task = {'id': test_id,
                        'run': run,
                        'cached': 1 if job['current_state']['repeat_view'] else 0,
                        'done': False,
                        'profile': profile_dir,
                        'error': None,
                        'log_data': True,
                        'activity_time': 2,
                        'combine_steps': False,
                        'video_directories': [],
                        'page_data': {},
                        'navigated': False,
                        'page_result': None,
                        'script_step_count': 1}
                # Set up the task configuration options
                task['port'] = 9222 + (self.test_run_count % 500)
                task['task_prefix'] = "{0:d}".format(run)
                if task['cached']:
                    task['task_prefix'] += "_Cached"
                task['prefix'] = task['task_prefix']
                short_id = "{0}.{1}.{2}".format(task['id'], run, task['cached'])
                task['dir'] = os.path.join(self.workdir, short_id)
                task['task_video_prefix'] = 'video_{0:d}'.format(run)
                if task['cached']:
                    task['task_video_prefix'] += "_cached"
                task['video_subdirectory'] = task['task_video_prefix']
                if os.path.isdir(task['dir']):
                    shutil.rmtree(task['dir'])
                os.makedirs(task['dir'])
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
                    domains = re.split('[, ]', job['blockDomains'])
                    for domain in domains:
                        domain = domain.strip()
                        if len(domain) and domain.find('"') == -1:
                            task['block_domains'].append(domain)
                            task['host_rules'].append('"MAP {0} 127.0.0.1"'.format(domain))
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
                task['time_limit'] = job['timeout']
                task['test_time_limit'] = task['time_limit'] * task['script_step_count']
                task['stop_at_onload'] = bool('web10' in job and job['web10'])
                task['run_start_time'] = monotonic.monotonic()
                # Keep the full resolution video frames if the browser window is smaller than 600px
                if 'thumbsize' not in job and (task['width'] < 600 or task['height'] < 600):
                    job['fullSizeVideo'] = 1
                self.test_run_count += 1
        if task is None and os.path.isdir(self.workdir):
            try:
                shutil.rmtree(self.workdir)
            except Exception:
                pass
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
                            domains = re.split('[, ]', target)
                            for domain in domains:
                                domain = domain.strip()
                                if len(domain) and domain.find('"') == -1:
                                    task['block_domains'].append(domain)
                                    task['host_rules'].append('"MAP {0} 127.0.0.1"'.format(domain))
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
                                pass
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
                with open(margins_file, 'wb') as f_out:
                    json.dump(self.margins, f_out)

    def body_fetch_thread(self):
        """background thread to fetch bodies"""
        import requests
        session = requests.session()
        proxies = {"http": None, "https": None}
        try:
            while True:
                task = self.fetch_queue.get_nowait()
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

    def get_bodies(self, task):
        """Fetch any bodies that are missing if response bodies were requested"""
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
            requests = []
            devtools_file = os.path.join(task['dir'], task['prefix'] + '_devtools_requests.json.gz')
            with gzip.open(devtools_file, 'rb') as f_in:
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
                    pass
                for request in requests['requests']:
                    if 'full_url' in request and \
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
                                headers = None
                                if 'headers' in request and 'request' in request['headers']:
                                    headers = request['headers']['request']
                                task = {'url': request['full_url'],
                                        'file': body_file_path,
                                        'id': body_id,
                                        'headers': headers}
                                if os.path.isfile(body_file_path):
                                    self.fetch_result_queue.put(task)
                                else:
                                    self.fetch_queue.put(task)
            if count:
                if not os.path.isdir(path):
                    os.makedirs(path)
                logging.debug("Fetching bodies for %d requests", count)
                threads = []
                thread_count = min(count, 10)
                for _ in xrange(thread_count):
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
                        task = self.fetch_result_queue.get_nowait()
                        if os.path.isfile(task['file']):
                            # check to see if it is text or utf-8 data
                            try:
                                data = ''
                                with open(task['file'], 'rb') as f_in:
                                    data = f_in.read()
                                json.loads('"' + data.replace('"', '\\"') + '"')
                                body_index += 1
                                file_name = '{0:03d}-{1}-body.txt'.format(body_index, task['id'])
                                bodies.append({'name': file_name, 'file': task['file']})
                            except Exception:
                                pass
                        self.fetch_result_queue.task_done()
                except Exception:
                    pass
                # Add the files
                if bodies:
                    with zipfile.ZipFile(bodies_zip, 'a', zipfile.ZIP_DEFLATED) as zip_file:
                        for body in bodies:
                            zip_file.write(body['file'], body['name'])
        except Exception:
            pass

    def upload_task_result(self, task):
        """Upload the result of an individual test run"""
        logging.info('Uploading result')
        cpu_pct = None
        self.update_browser_viewport(task)
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
            if 'page_data' in task and 'fullyLoadedCPUpct' in task['page_data']:
                cpu_pct = task['page_data']['fullyLoadedCPUpct']
            data = {'id': task['id'],
                    'location': self.location,
                    'run': str(task['run']),
                    'cached': str(task['cached']),
                    'pc': self.pc_name}
            if self.key is not None:
                data['key'] = self.key
            if self.instance_id is not None:
                data['ec2'] = self.instance_id
            if self.zone is not None:
                data['ec2zone'] = self.zone
            needs_zip = []
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
                                    if os.path.getsize(filepath) > 100000:
                                        logging.debug('Uploading %s (%d bytes)', filename,
                                                    os.path.getsize(filepath))
                                        if self.post_data(self.url + "resultimage.php", data,
                                                        filepath, task['prefix'] + '_' + filename):
                                            os.remove(filepath)
                                        else:
                                            needs_zip.append({'path': filepath, 'name': name})
                                    else:
                                        needs_zip.append({'path': filepath, 'name': name})
                # Upload the separate large files (> 100KB)
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
                        elif os.path.getsize(filepath) > 100000:
                            logging.debug('Uploading %s (%d bytes)', filename,
                                        os.path.getsize(filepath))
                            if self.post_data(self.url + "resultimage.php", data, filepath, filename):
                                try:
                                    os.remove(filepath)
                                except Exception:
                                    pass
                            else:
                                needs_zip.append({'path': filepath, 'name': filename})
                        else:
                            needs_zip.append({'path': filepath, 'name': filename})
                # Zip the remaining files
                if len(needs_zip):
                    zip_path = os.path.join(task['dir'], "result.zip")
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zip_file:
                        for zipitem in needs_zip:
                            logging.debug('Storing %s (%d bytes)', zipitem['name'],
                                        os.path.getsize(zipitem['path']))
                            zip_file.write(zipitem['path'], zipitem['name'])
                            try:
                                os.remove(zipitem['path'])
                            except Exception:
                                pass
            # Post the workdone event for the task (with the zip attached)
            if task['done']:
                data['done'] = '1'
            if task['error'] is not None:
                data['error'] = task['error']
            if cpu_pct is not None:
                data['cpu'] = '{0:0.2f}'.format(cpu_pct)
            logging.debug('Uploading result zip')
            self.post_data(self.url + "workdone.php", data, zip_path, 'result.zip')
        # Clean up so we don't leave directories lying around
        if os.path.isdir(task['dir']):
            try:
                shutil.rmtree(task['dir'])
            except Exception:
                pass
        if task['done'] and os.path.isdir(self.workdir):
            try:
                shutil.rmtree(self.workdir)
            except Exception:
                pass

    def post_data(self, url, data, file_path, filename):
        """Send a multi-part post"""
        ret = True
        # pass the data fields as query params and any files as post data
        url += "?"
        for key in data:
            if data[key] != None:
                url += key + '=' + urllib.quote_plus(data[key]) + '&'
        logging.debug(url)
        try:
            if file_path is not None and os.path.isfile(file_path):
                self.session.post(url,
                                  files={'file': (filename, open(file_path, 'rb'))},
                                  timeout=300,)
            else:
                self.session.post(url)
        except Exception:
            logging.exception("Upload Exception")
            ret = False
        return ret
