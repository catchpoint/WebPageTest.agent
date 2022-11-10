# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Base class support for browsers"""
import logging
import os
import platform
import shlex
from time import monotonic

class BaseBrowser(object):
    """Browser base"""
    def __init__(self):
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")
        self.task = None
        self.must_exit = False

    def execute_js(self, script):
        """Stub to be overridden"""
        return None
    
    def alert_size(self,_alert_config, _task_dir, _prefix):
        '''File alerting function to be overridden by browser class'''
        return None

    def profile_start(self, event_name):
        if self.task is not None and 'profile_data' in self.task:
            with self.task['profile_data']['lock']:
                self.task['profile_data'][event_name] = {'s': round(monotonic() - self.task['profile_data']['start'], 3)}

    def profile_end(self, event_name):
        if self.task is not None and 'profile_data' in self.task:
            with self.task['profile_data']['lock']:
                if event_name in self.task['profile_data']:
                    self.task['profile_data'][event_name]['e'] = round(monotonic() - self.task['profile_data']['start'], 3)
                    self.task['profile_data'][event_name]['d'] = round(self.task['profile_data'][event_name]['e'] - self.task['profile_data'][event_name]['s'], 3)

    def shutdown(self):
        """Agent is dying, close as much as possible gracefully"""
        self.must_exit = True

    def sanitize_shell_args(self, args):
        """Sanitize a list of arguments that will be used in a shell subprocess"""
        try:
            if platform.system() in ["Linux", "Darwin"]:
                args = [shlex.quote(arg) for arg in args]
        except Exception:
            logging.exception('Error sanitizing shell args')

    def sanitize_shell_string(self, shell_string):
        """Sanitize a string of arguments that will be used in a shell subprocess"""
        sanitized_string = ''
        try:
            if platform.system() in ["Linux", "Darwin"]:
                args = shlex.split(shell_string)
                args = [shlex.quote(arg) for arg in args]
                sanitized_string = ' '.join(args)
            else:
                sanitized_string = shell_string
        except Exception:
            logging.exception('Error sanitizing shell string')
        return sanitized_string