# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for interfacing with WebPageTest server"""
from datetime import datetime
import gzip
import logging
import os
import platform
import re
import shutil
import subprocess
import time
import urllib
import zipfile
import monotonic
import ujson as json

DEFAULT_JPEG_QUALITY = 30

class WebPageTest(object):
    """Controller for interfacing with the WebPageTest server"""
    # pylint: disable=E0611
    def __init__(self, options, workdir):
        import psutil
        import requests
        self.job = None
        self.session = requests.Session()
        self.options = options
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
            for interface in interfaces:
                iface = interfaces[interface]
                for addr in iface:
                    match = re.search(r'^00:50:56:00:([\da-fA-F]+):([\da-fA-F]+)$', addr.address)
                    if match:
                        server = match.group(1).strip('0')
                        machine = match.group(2)
                        hostname = 'VM{0}-{1}'.format(server, machine)
        self.pc_name = hostname if options.name is None else options.name
        self.auth_name = options.username
        self.auth_password = options.password if options.password is not None else ''
        self.validate_server_certificate = False
        self.instance_id = None
        self.zone = None
        # Get the screen resolution if we're in desktop mode
        self.screen_width = None
        self.screen_height = None
        if not self.options.android:
            if self.options.xvfb:
                self.screen_width = 1920
                self.screen_height = 1200
            elif platform.system() == 'Windows':
                from win32api import GetSystemMetrics
                self.screen_width = GetSystemMetrics(0)
                self.screen_height = GetSystemMetrics(1)
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
        self.version = None
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
    # pylint: enable=E0611

    def benchmark_cpu(self):
        """Benchmark the CPU for mobile emulation"""
        self.cpu_scale_multiplier = 1.0
        if not self.options.android:
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
        try:
            response = requests.get('http://169.254.169.254/latest/user-data', timeout=30)
            if len(response.text):
                self.parse_user_data(response.text)
        except Exception:
            pass
        try:
            response = requests.get('http://169.254.169.254/latest/meta-data/instance-id',
                                    timeout=30)
            if len(response.text):
                self.instance_id = response.text.strip()
        except Exception:
            pass
        try:
            response = requests.get(
                'http://169.254.169.254/latest/meta-data/placement/availability-zone',
                timeout=30)
            if len(response.text):
                self.zone = response.text.strip()
        except Exception:
            pass

    def load_from_gce(self):
        """Load config settings from GCE user data"""
        import requests
        try:
            response = requests.get(
                'http://169.254.169.254/computeMetadata/v1/instance/attributes/wpt_data',
                headers={'Metadata-Flavor':'Google'},
                timeout=30)
            if len(response.text):
                self.parse_user_data(response.text)
        except Exception:
            pass
        try:
            response = requests.get('http://169.254.169.254/computeMetadata/v1/instance/id',
                                    headers={'Metadata-Flavor':'Google'},
                                    timeout=30)
            if len(response.text):
                self.instance_id = response.text.strip()
        except Exception:
            pass

    def parse_user_data(self, user_data):
        """Parse the provided user data and extract the config info"""
        options = user_data.split()
        for option in options:
            try:
                parts = option.split('=', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    logging.debug('Setting config option "%s" to "%s"', key, value)
                    if key == 'wpt_server':
                        self.url = 'http://{0}/work/'.format(value)
                    if key == 'wpt_url':
                        self.url = value
                    elif key == 'wpt_loc' or key == 'wpt_location':
                        if value is not None:
                            self.test_locations = value.split(',')
                            self.location = str(self.test_locations[0])
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
            except Exception:
                pass


    def get_test(self):
        """Get a job from the server"""
        import requests
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
            url = self.url + "getwork.php?f=json&shards=1"
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
            if self.version is not None:
                url += '&version={0}'.format(self.version)
            if self.screen_width is not None:
                url += '&screenwidth={0:d}'.format(self.screen_width)
            if self.screen_height is not None:
                url += '&screenheight={0:d}'.format(self.screen_height)
            free_disk = get_free_disk_space()
            url += '&freedisk={0:0.3f}'.format(free_disk)
            logging.info("Checking for work: %s", url)
            try:
                response = self.session.get(url, timeout=30)
                if len(response.text):
                    job = response.json()
                    logging.debug("Job: %s", json.dumps(job))
                    # set some default options
                    if 'iq' not in job:
                        job['iq'] = DEFAULT_JPEG_QUALITY
                    if 'pngss' not in job:
                        job['pngss'] = 0
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
                    if job['type'] == 'lighthouse':
                        job['fvonly'] = 1
                        job['lighthouse'] = 1
                    job['keep_lighthouse_trace'] = \
                            bool('lighthouseTrace' in job and job['lighthouseTrace'])
                    job['video'] = bool('Capture Video' in job and job['Capture Video'])
                    job['keepvideo'] = bool('keepvideo' in job and job['keepvideo'])
                    job['interface'] = None
                    job['persistent_dir'] = self.persistent_dir
                    if 'throttle_cpu' in job:
                        throttle = float(re.search(r'\d+\.?\d*', str(job['throttle_cpu'])).group())
                        throttle *= self.cpu_scale_multiplier
                        job['throttle_cpu'] = throttle
                if job is None and len(locations) > 0:
                    location = str(locations.pop(0))
                    retry = True
            except requests.exceptions.RequestException as err:
                logging.critical("Get Work Error: %s", err.strerror)
                retry = True
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
                        'page_data': {}}
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
                task['block'] = []
                if 'block' in job:
                    block_list = job['block'].split()
                    for block in block_list:
                        block = block.strip()
                        if len(block):
                            task['block'].append(block)
                self.build_script(job, task)
                task['width'] = job['width']
                task['height'] = job['height']
                if 'mobile' in job and job['mobile']:
                    if 'browser' in job and job['browser'] in self.margins:
                        task['width'] = job['width'] + self.margins[job['browser']]['width']
                        task['height'] = job['height'] + self.margins[job['browser']]['height']
                    else:
                        task['width'] = job['width'] + 20
                        task['height'] = job['height'] + 120
                task['time_limit'] = job['timeout']
                task['stop_at_onload'] = bool('web10' in job and job['web10'])
                self.test_run_count += 1
        if task is None and os.path.isdir(self.workdir):
            try:
                shutil.rmtree(self.workdir)
            except Exception:
                pass
        return task

    def build_script(self, job, task):
        """Build the actual script that will be used for testing"""
        task['script'] = []
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
                        record = True
                    # commands that get pre-processed
                    elif command == 'setbrowsersize' or command == 'setviewportsize':
                        keep = False
                        if target is not None and value is not None:
                            width = int(re.search(r'\d+', str(target)).group())
                            height = int(re.search(r'\d+', str(value)).group())
                            if width > 0 and height > 0 and width < 10000 and height < 10000:
                                job['width'] = width
                                job['height'] = height
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
                            if 'host_rules' not in task:
                                task['host_rules'] = []
                            domains = target.split()
                            for domain in domains:
                                domain = domain.strip()
                                if len(domain) and domain.find('"') == -1:
                                    task['host_rules'].append('"MAP {0} 127.0.0.1"'.format(domain))
                    elif command == 'blockdomainsexcept':
                        keep = False
                        if target is not None:
                            if 'host_rules' not in task:
                                task['host_rules'] = []
                            domains = target.split()
                            for domain in domains:
                                domain = domain.strip()
                                if len(domain) and domain.find('"') == -1:
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
                                if 'host_rules' not in task:
                                    task['host_rules'] = []
                                task['host_rules'].append('"MAP {0} {1}"'.format(target, value))

                    elif command == 'addheader' or command == 'setheader':
                        keep = False
                        if target is not None:
                            if 'headers' not in job:
                                job['headers'] = {}
                            separator = target.find(':')
                            if separator > 0:
                                name = target[:separator].strip()
                                value = target[separator + 1:].strip()
                                job['headers'][name] = value
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
                                script = "document.querySelector('[{0}=\"{1}\"]')".format(\
                                        attribute, attr_value)
                                if command in ['click', 'sendclick']:
                                    script += '.click();'
                                elif command == 'submitform' and value is not None:
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
                        task['script'].append({'command': command,
                                               'target': target,
                                               'value': value,
                                               'record': record})
        elif 'url' in job:
            if job['url'][:4] != 'http':
                job['url'] = 'http://' + job['url']
            task['script'].append({'command': 'navigate', 'target': job['url'], 'record': True})
        logging.debug(task['script'])

    def update_browser_viewport(self, task):
        """Update the browser border size based on the measured viewport"""
        if 'actual_viewport' in task and 'width' in task and 'height' in task and \
                self.job is not None and 'browser' in self.job:
            browser = self.job['browser']
            width = task['width'] - task['actual_viewport']['width']
            height = task['height'] - task['actual_viewport']['height']
            if browser not in self.margins or self.margins[browser]['width'] != width or \
                    self.margins[browser]['height'] != height:
                self.margins[browser] = {"width": width, "height": height}
                if not os.path.isdir(self.persistent_dir):
                    os.makedirs(self.persistent_dir)
                margins_file = os.path.join(self.persistent_dir, 'margins.json')
                with open(margins_file, 'wb') as f_out:
                    json.dump(self.margins, f_out)

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
        # Write out the accumulated page_data
        if task['page_data']:
            if 'browser' in self.job:
                task['page_data']['browser_name'] = self.job['browser']
            if 'fullyLoadedCPUpct' in task['page_data']:
                cpu_pct = task['page_data']['fullyLoadedCPUpct']
            if 'step_name' in task:
                task['page_data']['eventName'] = task['step_name']
            path = os.path.join(task['dir'], task['prefix'] + '_page_data.json.gz')
            json_page_data = json.dumps(task['page_data'])
            logging.debug('Page Data: %s', json_page_data)
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json_page_data)
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
            if len(task['video_directories']):
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
        import requests
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
                                  files={'file':(filename, open(file_path, 'rb'))},
                                  timeout=300,)
            else:
                self.session.post(url)
        except requests.exceptions.RequestException as err:
            logging.critical("Upload: %s", err.strerror)
            ret = False
        except IOError as err:
            logging.error("Upload Error: %s", err.strerror)
            ret = False
        except Exception:
            logging.error("Upload Exception")
            ret = False
        return ret
