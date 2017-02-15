# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for interfacing with WebPageTest server"""
import json
import logging
import os
import platform
import shutil
import urllib
import zipfile

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
            shutil.rmtree(self.workdir)
        self.profile_dir = os.path.join(self.workdir, 'browser')
        os.makedirs(self.profile_dir)

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
                task = {'id': job['Test ID'],
                        'run': job['current_state']['run'],
                        'cached': 1 if job['current_state']['repeat_view'] else 0,
                        'done': False,
                        'profile': self.profile_dir}
                # Set up the task configuration options
                task['width'] = 1024
                task['height'] = 768
                task['port'] = 9222
                task['prefix'] = "{0}_".format(task['run'])
                if task['cached']:
                    task['prefix'] += "Cached_"
                short_id = "{0}.{1}.{2}".format(task['id'], task['run'], task['cached'])
                task['dir'] = os.path.join(self.workdir, short_id)
                if os.path.isdir(task['dir']):
                    shutil.rmtree(task['dir'])
                os.makedirs(task['dir'])
                if job['current_state']['run'] == job['runs']:
                    if job['current_state']['repeat_view']:
                        job['current_state']['done'] = True
                        task['done'] = True
                    elif 'fvonly' in job and job['fvonly']:
                        job['current_state']['done'] = True
                        task['done'] = True
        return task

    def upload_task_result(self, task):
        """Upload the result of an individual test run"""
        data = {'id': task['id'],
                'location': self.location,
                'key': self.key,
                'run': str(task['run']),
                'cached': str(task['cached'])}
        needs_zip = []
        zip_path = None
        if os.path.isdir(task['dir']):
            # Upload the separate large files (> 100KB)
            for filename in os.listdir(task['dir']):
                filepath = os.path.join(task['dir'], filename)
                if os.path.isfile(filepath):
                    if os.path.getsize(filepath) > 100000:
                        if self.post_data(self.url + "resultimage.php", data, filepath):
                            os.remove(filepath)
                        else:
                            needs_zip.append(filepath)
                    else:
                        needs_zip.append(filepath)
            # Zip the remaining files
            if len(needs_zip):
                zip_path = os.path.join(task['dir'], "result.zip")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for filepath in needs_zip:
                        zip_file.write(filepath, os.path.basename(filepath))
                        os.remove(filepath)
        # Post the workdone event for the task (with the zip attached)
        if task['done']:
            data['done'] = '1'
        self.post_data(self.url + "workdone.php", data, zip_path)
        # Clean up so we don't leave directories lying around
        if os.path.isdir(task['dir']):
            shutil.rmtree(task['dir'])

    def post_data(self, url, data, file_path):
        """Send a multi-part post"""
        import requests
        ret = True
        # pass the data fields as query params and any files as post data
        url += "?"
        for key in data:
            url += key + '=' + urllib.quote_plus(data[key]) + '&'
        try:
            upload_file = None
            if file_path is not None and os.path.isfile(file_path):
                upload_file = open(file_path)
                requests.post(url,
                              files={'file':(os.path.basename(file_path), upload_file)},
                              timeout=300)
                upload_file.close()
            else:
                requests.post(url)
        except requests.exceptions.RequestException as err:
            logging.critical("Upload: %s", err.strerror)
            ret = False
        except IOError as err:
            logging.error("Upload Error: %s", err.strerror)
            ret = False
        return ret
