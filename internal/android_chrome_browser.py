# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Chrome browser on Android"""
import logging
import os
import subprocess

class AndroidChromeBrowser(object):
    """Chrome browser on Android"""
    def __init__(self, adb):
        self.adb = adb
