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
import ujson as json

DEFAULT_JPEG_QUALITY = 30

class WebPageTest(object):
    """Controller for interfacing with the WebPageTest server"""
    def __init__(self, options, workdir):
        import requests
        self.session = requests.Session()
        self.options = options
        # Configurable options
        self.url = options.server
        self.location = options.location
        self.key = options.key
        self.time_limit = 120
        self.pc_name = platform.uname()[1] if options.name is None else options.name
        self.auth_name = options.username
        self.auth_password = options.password if options.password is not None else ''
        self.validate_server_certificate = False
        self.instance_id = None
        self.zone = None
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
                        self.location = value
                    elif key == 'wpt_key':
                        self.key = value
                    elif key == 'wpt_timeout':
                        self.time_limit = int(value)
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
        job = None
        url = self.url + "getwork.php?f=json&shards=1"
        url += "&location=" + urllib.quote_plus(self.location)
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
        free_disk = get_free_disk_space()
        url += '&freedisk={0:0.3f}'.format(free_disk)
        logging.info("Checking for work: %s", url)
        count = 0
        retry = True
        while count < 3 and retry:
            retry = False
            count += 1
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
                    job['video'] = bool('Capture Video' in job and job['Capture Video'])
                    job['interface'] = None
                    job['persistent_dir'] = self.persistent_dir
            except requests.exceptions.RequestException as err:
                logging.critical("Get Work Error: %s", err.strerror)
                retry = True
                time.sleep(0.1)
            except Exception:
                pass
        return job

    def get_task(self, job):
        """Create a task object for the next test run or return None if the job is done"""
        task = None
        if 'current_state' not in job or not job['current_state']['done']:
            if 'run' in job:
                # Sharded test, running one run only
                if 'current_state' not in job:
                    job['current_state'] = {"run": int(job['run']), "repeat_view": False,
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
                task['port'] = 9222
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
                if 'width' in job and 'height' in job:
                    if 'mobile' in job and job['mobile']:
                        task['width'] = job['width'] + 20
                        task['height'] = job['height'] + 120
                    else:
                        task['width'] = job['width']
                        task['height'] = job['height']
                task['time_limit'] = job['timeout']
                task['stop_at_onload'] = bool('web10' in job and job['web10'])
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
                            width = int(target)
                            height = int(value)
                            if width > 0 and height > 0 and width < 10000 and height < 10000:
                                job['width'] = width
                                job['height'] = height
                    elif command == 'setdevicescalefactor' and target is not None:
                        keep = False
                        job['dpr'] = target
                    elif command == 'settimeout':
                        keep = False
                        if target is not None:
                            time_limit = int(target)
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

    def upload_task_result(self, task):
        """Upload the result of an individual test run"""
        logging.info('Uploading result')
        # Write out the accumulated page_data
        if task['page_data']:
            if 'step_name' in task:
                task['page_data']['eventName'] = task['step_name']
            path = os.path.join(task['dir'], task['prefix'] + '_page_data.json.gz')
            with gzip.open(path, 'wb', 7) as outfile:
                outfile.write(json.dumps(task['page_data']))
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
                    if os.path.getsize(filepath) > 100000:
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
