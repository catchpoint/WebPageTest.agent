# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for interfacing with WebPageTest server"""
import logging
import os
import platform
import shutil
import urllib
import zipfile
import ujson as json

DEFAULT_JPEG_QUALITY = 30

class WebPageTest(object):
    """Controller for interfacing with the WebPageTest server"""
    def __init__(self, options, workdir):
        self.options = options
        self.url = options.server
        self.location = options.location
        self.key = options.key
        if options.name is None:
            self.pc_name = platform.uname()[1]
        else:
            self.pc_name = options.name
        self.workdir = os.path.join(workdir, self.pc_name)
        if os.path.isdir(self.workdir):
            try:
                shutil.rmtree(self.workdir)
            except BaseException as _:
                pass
        self.profile_dir = os.path.join(self.workdir, 'browser')

    def get_test(self):
        """Get a job from the server"""
        import requests
        job = None
        url = self.url + "getwork.php?f=json"
        url += "&location=" + urllib.quote_plus(self.location)
        url += "&pc=" + urllib.quote_plus(self.pc_name)
        if self.key is not None:
            url += "&key=" + self.key
        logging.info("Checking for work: %s", url)
        try:
            response = requests.get(url, timeout=30)
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
                if 'Test ID' not in job or 'browser' not in job or 'runs' not in job:
                    job = None
        except requests.exceptions.RequestException as err:
            logging.critical("Get Work Error: %s", err.strerror)
        return job

    def get_task(self, job):
        """Create a task object for the next test run or return None if the job is done"""
        task = None
        if 'current_state' not in job or not job['current_state']['done']:
            if 'current_state' not in job:
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
                        'time_limit': 120,
                        'error': None,
                        'video_directories': []}
                # Set up the task configuration options
                task['width'] = 1024
                task['height'] = 768
                if 'width' in job and 'height' in job:
                    if 'mobile' in job and job['mobile']:
                        task['width'] = job['width'] + 20
                        task['height'] = job['height'] + 120
                    else:
                        task['width'] = job['width']
                        task['height'] = job['height']
                task['port'] = 9222
                task['task_prefix'] = "{0:d}_".format(run)
                if task['cached']:
                    task['task_prefix'] += "Cached_"
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
                if job['current_state']['run'] == job['runs']:
                    if job['current_state']['repeat_view']:
                        job['current_state']['done'] = True
                        task['done'] = True
                    elif 'fvonly' in job and job['fvonly']:
                        job['current_state']['done'] = True
                        task['done'] = True
                self.build_script(job, task)
        if task is None and os.path.isdir(self.workdir):
            try:
                shutil.rmtree(self.workdir)
            except BaseException as _:
                pass
        return task

    def build_script(self, job, task):
        """Build the actual script that will be used for testing"""
        if 'script' in job:
            task['script'] = []
            lines = job['script'].splitlines()
            for line in lines:
                parts = line.split("\t", 2)
                if parts is not None and len(parts):
                    valid = True
                    record = False
                    command = parts[0].lower().strip()
                    target = parts[1] if len(parts) > 1 else None
                    value = parts[2] if len(parts) > 2 else None
                    andwait = command.find('andwait')
                    if andwait > -1:
                        command = command[:andwait]
                        record = True
                    # go through the known commands
                    if command == 'navigate':
                        if target[:4] != 'http':
                            target = 'http://' + target
                        record = True
                    elif command == 'exec':
                        pass
                    else:
                        valid = False
                    if valid:
                        task['script'].append({'command': command,
                                               'target': target,
                                               'value': value,
                                               'record': record})
        elif 'url' in job:
            if job['url'][:4] != 'http':
                job['url'] = 'http://' + job['url']
            task['script'] = [{'command': 'navigate', 'target': job['url'], 'record': True}]
        logging.debug(task['script'])

    def upload_task_result(self, task):
        """Upload the result of an individual test run"""
        logging.info('Uploading result')
        data = {'id': task['id'],
                'location': self.location,
                'key': self.key,
                'run': str(task['run']),
                'cached': str(task['cached'])}
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
                                    logging.debug('Uploading %s', filename)
                                    if self.post_data(self.url + "resultimage.php", data,
                                                      filepath, task['prefix'] + filename):
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
                        logging.debug('Uploading %s', filename)
                        if self.post_data(self.url + "resultimage.php", data, filepath, filename):
                            os.remove(filepath)
                        else:
                            needs_zip.append({'path': filepath, 'name': filename})
                    else:
                        needs_zip.append({'path': filepath, 'name': filename})
            # Zip the remaining files
            if len(needs_zip):
                zip_path = os.path.join(task['dir'], "result.zip")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zip_file:
                    for zipitem in needs_zip:
                        logging.debug('Storing %s', zipitem['name'])
                        zip_file.write(zipitem['path'], zipitem['name'])
                        os.remove(zipitem['path'])
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
            except BaseException as _:
                pass
        if task['done'] and os.path.isdir(self.workdir):
            try:
                shutil.rmtree(self.workdir)
            except BaseException as _:
                pass

    def post_data(self, url, data, file_path, filename):
        """Send a multi-part post"""
        import requests
        ret = True
        # pass the data fields as query params and any files as post data
        url += "?"
        for key in data:
            url += key + '=' + urllib.quote_plus(data[key]) + '&'
        logging.debug(url)
        try:
            if file_path is not None and os.path.isfile(file_path):
                requests.post(url,
                              files={'file':(filename, open(file_path, 'rb'))},
                              timeout=300)
            else:
                requests.post(url)
        except requests.exceptions.RequestException as err:
            logging.critical("Upload: %s", err.strerror)
            ret = False
        except IOError as err:
            logging.error("Upload Error: %s", err.strerror)
            ret = False
        return ret
