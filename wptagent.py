#!/usr/bin/env python
# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""WebPageTest cross-platform agent"""
import atexit
import logging
import os
import platform
import signal
import subprocess
import sys
import threading
import time
import traceback

class WPTAgent(object):
    """Main agent workflow"""
    def __init__(self, options, browsers):
        from internal.browsers import Browsers
        from internal.webpagetest import WebPageTest
        from internal.traffic_shaping import TrafficShaper
        self.must_exit = False
        self.options = options
        self.browsers = Browsers(options, browsers)
        self.root_path = os.path.abspath(os.path.dirname(__file__))
        self.support_path = os.path.join(os.path.join(self.root_path, "internal"), "support")
        self.wpt = WebPageTest(options, os.path.join(self.root_path, "work"))
        self.shaper = TrafficShaper()
        self.job = None
        self.task = None
        atexit.register(self.cleanup)
        signal.signal(signal.SIGINT, self.signal_handler)

    def run_testing(self):
        """Main testing flow"""
        while not self.must_exit:
            try:
                if self.browsers.is_ready():
                    self.job = self.wpt.get_test()
                    if self.job is not None:
                        self.task = self.wpt.get_task(self.job)
                        while self.task is not None:
                            # - Prepare the browser
                            browser = self.browsers.get_browser(self.job['browser'], self.job)
                            if browser is not None:
                                browser.prepare(self.task)
                                browser.launch(self.task)
                                if self.shaper.configure(self.job):
                                    # Run the actual test
                                    browser.wait_for_idle()
                                    browser.run_task(self.task)
                                else:
                                    self.task.error = "Error configuring traffic-shaping"
                                self.shaper.reset()
                                browser.stop()
                            else:
                                err = "Invalid browser - {0}".format(self.job['browser'])
                                logging.critical(err)
                                self.task['error'] = err
                            # Post-process the results before uploading
                            trace_thread = threading.Thread(target=self.process_trace)
                            trace_thread.start()
                            self.process_video()
                            trace_thread.join()
                            self.wpt.upload_task_result(self.task)
                            # Set up for the next run
                            self.task = self.wpt.get_task(self.job)
                if self.job is not None:
                    self.job = None
                else:
                    self.sleep(5)
            except BaseException as err:
                logging.critical("Unhandled exception: %s", err.__str__)
                traceback.print_exc(file=sys.stdout)

    def process_video(self):
        """Post process the video"""
        from internal.video_processing import VideoProcessing
        video = VideoProcessing(self.job, self.task)
        video.process()

    def process_trace(self):
        """Post-process the trace file"""
        path_base = os.path.join(self.task['dir'], self.task['prefix'])
        trace_file = path_base + 'trace.json.gz'
        if os.path.isfile(trace_file):
            user_timing = path_base + 'user_timing.json.gz'
            cpu_slices = path_base + 'timeline_cpu.json.gz'
            script_timing = path_base + 'script_timing.json.gz'
            feature_usage = path_base + 'feature_usage.json.gz'
            interactive = path_base + 'interactive.json.gz'
            v8_stats = path_base + 'v8stats.json.gz'
            trace_parser = os.path.join(self.support_path, "trace-parser.py")
            subprocess.call(['python', trace_parser, '-t', trace_file, '-u', user_timing,
                             '-c', cpu_slices, '-j', script_timing, '-f', feature_usage,
                             '-i', interactive, '-s', v8_stats])

    def signal_handler(self, *_):
        """Ctrl+C handler"""
        if self.must_exit:
            exit(1)
        if self.job is None:
            print "Exiting..."
        else:
            print "Will exit after test completes.  Hit Ctrl+C again to exit immediately"
        self.must_exit = True

    def cleanup(self):
        """Do any cleanup that needs to be run regardless of how we exit."""
        self.shaper.remove()

    def sleep(self, seconds):
        """Sleep wrapped in an exception handler to properly deal with Ctrl+C"""
        try:
            time.sleep(seconds)
        except IOError:
            pass

    def startup(self):
        """Validate that all of the external dependencies are installed"""
        ret = True
        try:
            import monotonic as _
        except ImportError:
            print "Missing monotonic module. Please run 'pip install monotonic'"
            ret = False

        try:
            import requests as _
        except ImportError:
            print "Missing requests module. Please run 'pip install requests'"
            ret = False

        try:
            import websocket as _
        except ImportError:
            print "Missing websocket module. Please run 'pip install websocket-client'"
            ret = False

        try:
            import ujson as _
        except ImportError:
            print "Missing ujson parser. Please run 'pip install ujson'"
            ret = False

        try:
            from PIL import Image as _
        except ImportError:
            print "Missing PIL modile. Please run 'pip install pillow'"
            ret = False

        if subprocess.call(['python', '--version']):
            print "Make sure python 2.7 is available in the path."
            ret = False

        if subprocess.call('convert -version', shell=True):
            print "Missing convert utility. Please install ImageMagick " \
                  "and make sure it is in the path."
            ret = False

        # Windows-specific imports
        if platform.system() == "Windows":
            try:
                import win32api as _
                import win32process as _
            except ImportError:
                print "Missing pywin32 module. Please run 'python -m pip install pypiwin32'"
                ret = False

        if not self.shaper.install():
            print "Error configuring traffic shaping, make sure it is installed."
            ret = False

        return ret


def parse_ini(ini):
    """Parse an ini file and convert it to a dictionary"""
    import ConfigParser
    ret = None
    if os.path.isfile(ini):
        parser = ConfigParser.SafeConfigParser()
        parser.read(ini)
        ret = {}
        for section in parser.sections():
            ret[section] = {}
            for item in parser.items(section):
                ret[section][item[0]] = item[1]
        if not ret:
            ret = None
    return ret


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

    # Make sure we are running python 2.7.11 or newer (required for Windows 8.1)
    if sys.version_info[0] != 2 or \
            sys.version_info[1] != 7 or \
            sys.version_info[2] < 11:
        print "Requires python 2.7 (2.7.11 or later)"
        exit(1)

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

    browsers = parse_ini(os.path.join(os.path.dirname(__file__), "browsers.ini"))
    if browsers is None:
        print "No browsers configured. Check that browsers.ini is present and correct."
        exit(1)

    agent = WPTAgent(options, browsers)
    if agent.startup():
        #Create a work directory relative to where we are running
        print "Running agent, hit Ctrl+C to exit"
        agent.run_testing()
        print "Done"


if __name__ == '__main__':
    main()
