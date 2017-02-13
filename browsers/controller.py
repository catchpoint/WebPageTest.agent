# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Main entry point for controlling browsers"""
import logging
import os

class Browsers(object):
    """Controller for handling several browsers"""
    def __init__(self, options):
        self.options = options

    def is_ready(self):
        """Check to see if the configured browsers are ready to go"""
        ready = True
        if self.options.chrome is not None:
            logging.debug("Checking %s", self.options.chrome)
            if not os.path.isfile(self.options.chrome):
                logging.critical("Chrome executable is missing: %s", self.options.chrome)
                ready = False
        if self.options.canary is not None:
            logging.debug("Checking %s", self.options.canary)
            if not os.path.isfile(self.options.canary):
                logging.critical("Chrome canary executable is missing: %s", self.options.canary)
                ready = False
        return ready
