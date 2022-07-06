# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Microsoft Internet Explorer testing (based on the Edge support)"""
import logging
import os
import platform
from .microsoft_edge import Edge
from .os_util import run_elevated


class InternetExplorer(Edge):
    """Microsoft Edge"""
    def __init__(self, path, options, job):
        Edge.__init__(self, path, options, job)
        self.supports_interactive = False
        self.start_page = 'http://127.0.0.1:8888/orange.html'

    def get_driver(self, task):
        """Get the webdriver instance"""
        from selenium import webdriver
        path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                            'support', 'IE')
        reg_file = os.path.join(path, 'keys.reg')
        if os.path.isfile(reg_file):
            run_elevated('reg', 'IMPORT "{0}"'.format(reg_file))
        if platform.machine().endswith('64'):
            path = os.path.join(path, 'amd64', 'IEDriverServer.exe')
        else:
            path = os.path.join(path, 'x86', 'IEDriverServer.exe')
        capabilities = webdriver.DesiredCapabilities.INTERNETEXPLORER.copy()
        capabilities['ie.enableFullPageScreenshot'] = False
        if not task['cached']:
            capabilities['ie.ensureCleanSession'] = True
        driver = webdriver.Ie(executable_path=path, capabilities=capabilities)
        return driver

    def prepare(self, job, task):
        Edge.prepare(self, job, task)
        try:
            import _winreg # pylint: disable=import-error
            reg_path = 'Software\\Microsoft\\Windows\\CurrentVersion\\' \
                       'Internet Settings\\5.0\\User Agent\\Post Platform'
            key = _winreg.CreateKey(_winreg.HKEY_CURRENT_USER, reg_path)
            # Delete any string modifiers currently in the registry
            values = []
            try:
                index = 0
                while True and index < 10000:
                    value = _winreg.EnumValue(key, index)
                    values.append(value[0])
                    index += 1
            except Exception:
                logging.exception('Error processing registry')
            for value in values:
                _winreg.DeleteValue(key, value)
            if 'AppendUA' in task and len(task['AppendUA']):
                _winreg.SetValueEx(key, task['AppendUA'], 0,
                                   _winreg.REG_SZ, 'IEAK')
        except Exception:
            logging.exception('Error writing registry key')

    def kill(self):
        """Kill any running instances"""
        processes = ['iexplore.exe', 'smartscreen.exe', 'dllhost.exe']
        for exe in processes:
            try:
                run_elevated('taskkill', '/F /T /IM {0}'.format(exe))
            except Exception:
                pass

    def clear_cache(self):
        """Clear the browser cache"""
        pass
