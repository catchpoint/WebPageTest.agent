# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Base class support for browsers"""
import os


class BaseBrowser(object):
    """Browser base"""
    def __init__(self):
        self.support_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "support")

    def execute_js(self, script):
        """Stub to be overridden"""
        return None
