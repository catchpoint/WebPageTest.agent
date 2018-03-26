# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for browsers"""
import os
import ujson as json

class BaseBrowser(object):
    """Browser base"""
    def __init__(self):
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")

    def wappalyzer_script(self, response_headers):
        """Build the wappalyzer script to run in-browser"""
        script = None
        try:
            with open(os.path.join(self.support_path, 'Wappalyzer', 'script.js')) as f_in:
                script = f_in.read()
            if script is not None:
                wappalyzer = None
                with open(os.path.join(self.support_path, 'Wappalyzer', 'wappalyzer.js')) as f_in:
                    wappalyzer = f_in.read()
                if wappalyzer is not None:
                    json_data = None
                    with open(os.path.join(self.support_path, 'Wappalyzer', 'apps.json')) as f_in:
                        json_data = f_in.read()
                    if json is not None:
                        # Format the headers as a dictionary of lists
                        headers = {}
                        if response_headers is not None:
                            if isinstance(response_headers, dict):
                                for key in response_headers:
                                    values = []
                                    entry = response_headers[key]
                                    if isinstance(entry, list):
                                        values = entry
                                    elif isinstance(entry, (str, unicode)):
                                        entries = entry.split('\n')
                                        for value in entries:
                                            values.append(value.strip())
                                    if values:
                                        headers[key.lower()] = values
                            elif isinstance(response_headers, list):
                                for pair in response_headers:
                                    if isinstance(pair, (str, unicode)):
                                        parts = pair.split(':', 1)
                                        key = parts[0].strip(' :\n\t').lower()
                                        value = parts[1].strip(' :\n\t')
                                        if key not in headers:
                                            headers[key] = []
                                        headers[key].append(value)
                        script = script.replace('%WAPPALYZER%', wappalyzer)
                        script = script.replace('%JSON%', json_data)
                        script = script.replace('%RESPONSE_HEADERS%', json.dumps(headers))
        except Exception:
            pass
        return script

    def wappalyzer_result_script(self):
        """Script to poll for the wappalyzer result"""
        return 'document.getElementById("wptagentWappalyzer").innerText;'
