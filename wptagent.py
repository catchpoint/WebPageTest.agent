#!/usr/bin/env python
# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""WebPageTest cross-platform agent"""
import logging
import os
import signal
import time
from browsers.controller import Browsers
from webpagetest.wpt import WebPageTest

class WPTAgent(object):
    """Main agent workflow"""
    def __init__(self, options):
        self.must_exit = False
        self.options = options
        self.browsers = Browsers(options)
        self.wpt = WebPageTest(options, os.path.join(os.path.dirname(__file__), "work"))
        self.current_test = None
        signal.signal(signal.SIGINT, self.signal_handler)


    def run_testing(self):
        """Main testing flow"""
        while not self.must_exit:
            if self.browsers.is_ready():
                self.current_test = self.wpt.get_test()
                if self.current_test is not None:
                    task = self.wpt.get_task(self.current_test)
                    while task is not None:
                        #TODO: Run the actual test
                        outfile = os.path.join(task['dir'], task['prefix'] + 'hello.txt')
                        with open(outfile, 'wb') as out:
                            out.write("Hello World")
                        # - Prepare the browser
                        # - Run the test (connected to dev tools)
                        # - Close the browser
                        # - Process the trace/devtools data
                        self.wpt.upload_task_result(task)
                        task = self.wpt.get_task(self.current_test)
            if self.current_test is not None:
                self.current_test = None
            else:
                self.sleep(15)


    def signal_handler(self, *_):
        """Ctrl+C handler"""
        if self.must_exit:
            exit(1)
        if self.current_test is None:
            print "Exiting..."
        else:
            print "Will exit after test completes.  Hit Ctrl+C again to exit immediately"
        self.must_exit = True


    def sleep(self, seconds):
        """Sleep wrapped in an exception handler to properly deal with Ctrl+C"""
        try:
            time.sleep(seconds)
        except IOError:
            pass


def main():
    """Startup and initialization"""
    import argparse
    parser = argparse.ArgumentParser(description='WebPageTest Agent.', prog='wpt-agent')
    parser.add_argument('-v', '--verbose', action='count',
                        help="Increase verbosity (specify multiple times for more)."
                        " -vvvv for full debug output.")
    parser.add_argument('--server', required=True,
                        help="URL for WebPageTest work (i.e. http://www.webpagetest.org/work/).")
    parser.add_argument('--location', required=True,
                        help="Location ID (as configured in locations.ini on the server).")
    parser.add_argument('--key', help="Location key (optional).")
    parser.add_argument('--chrome', help="Path to Chrome executable (if configured).")
    parser.add_argument('--canary', help="Path to Chrome canary executable (if configured).")
    parser.add_argument('--name', help="Agent name (for the work directory).")
    options, _ = parser.parse_known_args()

    # Set up logging
    log_level = logging.CRITICAL
    if options.verbose == 1:
        log_level = logging.ERROR
    elif options.verbose == 2:
        log_level = logging.WARNING
    elif options.verbose == 3:
        log_level = logging.INFO
    elif options.verbose >= 4:
        log_level = logging.DEBUG
    logging.basicConfig(level=log_level, format="%(asctime)s.%(msecs)03d - %(message)s",
                        datefmt="%H:%M:%S")

    if not options.chrome and not options.canary:
        parser.error("No browser specified (chrome or canary).")

    #Create a work directory relative to where we are running
    agent = WPTAgent(options)
    print "Running agent, hit Ctrl+C to exit"
    agent.run_testing()
    print "Done"


if __name__ == '__main__':
    main()
