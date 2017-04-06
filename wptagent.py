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
import time
import traceback

class WPTAgent(object):
    """Main agent workflow"""
    def __init__(self, options, browsers):
        from internal.browsers import Browsers
        from internal.webpagetest import WebPageTest
        from internal.traffic_shaping import TrafficShaper
        from internal.adb import Adb
        self.must_exit = False
        self.options = options
        self.adb = Adb(self.options) if self.options.android else None
        self.browsers = Browsers(options, browsers, self.adb)
        self.root_path = os.path.abspath(os.path.dirname(__file__))
        self.wpt = WebPageTest(options, os.path.join(self.root_path, "work"))
        self.shaper = TrafficShaper(options)
        self.job = None
        self.task = None
        self.xvfb = None
        atexit.register(self.cleanup)
        signal.signal(signal.SIGINT, self.signal_handler)

    def run_testing(self):
        """Main testing flow"""
        import monotonic
        start_time = monotonic.monotonic()
        browser = None
        while not self.must_exit:
            try:
                if self.browsers.is_ready():
                    self.job = self.wpt.get_test()
                    if self.job is not None:
                        self.task = self.wpt.get_task(self.job)
                        while self.task is not None:
                            start = monotonic.monotonic()
                            try:
                                self.task['running_lighthouse'] = False
                                if self.job['type'] != 'lighthouse':
                                    self.run_single_test()
                                if self.task['run'] == 1 and not self.task['cached'] and \
                                        'lighthouse' in self.job and self.job['lighthouse']:
                                    self.task['running_lighthouse'] = True
                                    self.run_single_test()
                                elapsed = monotonic.monotonic() - start
                                logging.debug('Test run time: %0.3f sec', elapsed)
                            except Exception as err:
                                msg = ''
                                if err is not None and err.__str__() is not None:
                                    msg = err.__str__()
                                self.task['error'] = 'Unhandled exception running test: '\
                                    '{0}'.format(msg)
                                logging.critical("Unhandled exception running test: %s", msg)
                                traceback.print_exc(file=sys.stdout)
                            self.wpt.upload_task_result(self.task)
                            # Set up for the next run
                            self.task = self.wpt.get_task(self.job)
                if self.job is not None:
                    self.job = None
                else:
                    self.sleep(5)
            except Exception as err:
                msg = ''
                if err is not None and err.__str__() is not None:
                    msg = err.__str__()
                if self.task is not None:
                    self.task['error'] = 'Unhandled exception preparing test: '\
                        '{0}'.format(msg)
                logging.critical("Unhandled exception: %s", msg)
                traceback.print_exc(file=sys.stdout)
                if browser is not None:
                    browser.on_stop_recording(None)
                    browser = None
            if self.options.exit > 0:
                run_time = (monotonic.monotonic() - start_time) / 60.0
                if run_time > self.options.exit:
                    break

    def run_single_test(self):
        """Run a single test run"""
        browser = self.browsers.get_browser(self.job['browser'], self.job)
        if browser is not None:
            browser.prepare(self.job, self.task)
            browser.launch(self.job, self.task)
            if self.shaper.configure(self.job):
                try:
                    if self.task['running_lighthouse']:
                        browser.run_lighthouse_test(self.task)
                    else:
                        browser.run_task(self.task)
                except Exception as err:
                    msg = ''
                    if err is not None and err.__str__() is not None:
                        msg = err.__str__()
                    self.task['error'] = 'Unhandled exception in test run: '\
                        '{0}'.format(msg)
                    logging.critical("Unhandled exception in test run: %s", msg)
                    traceback.print_exc(file=sys.stdout)
            else:
                self.task['error'] = "Error configuring traffic-shaping"
            self.shaper.reset()
            browser.stop(self.job, self.task)
        else:
            err = "Invalid browser - {0}".format(self.job['browser'])
            logging.critical(err)
            self.task['error'] = err
        # Delete the browser profile if needed
        if self.task['cached'] or self.job['fvonly']:
            browser.clear_profile(self.task)
        browser = None

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
        logging.debug('Cleaning up')
        self.shaper.remove()
        if self.xvfb is not None:
            self.xvfb.stop()

    def sleep(self, seconds):
        """Sleep wrapped in an exception handler to properly deal with Ctrl+C"""
        try:
            time.sleep(seconds)
        except IOError:
            pass

    def wait_for_idle(self, timeout=30):
        """Wait for the system to go idle"""
        import monotonic
        import psutil
        logging.debug("Waiting for Idle...")
        cpu_count = psutil.cpu_count()
        if cpu_count > 0:
            target_pct = 20. / float(cpu_count)
            idle_start = None
            end_time = monotonic.monotonic() + timeout
            idle = False
            while not idle and monotonic.monotonic() < end_time:
                check_start = monotonic.monotonic()
                pct = psutil.cpu_percent(interval=1)
                if pct <= target_pct:
                    if idle_start is None:
                        idle_start = check_start
                    if monotonic.monotonic() - idle_start > 2:
                        idle = True
                else:
                    idle_start = None

    def startup(self):
        """Validate that all of the external dependencies are installed"""
        ret = True

        try:
            import dns.resolver as _
        except ImportError:
            print "Missing dns module. Please run 'pip install dnspython'"
            ret = False

        try:
            import monotonic as _
        except ImportError:
            print "Missing monotonic module. Please run 'pip install monotonic'"
            ret = False

        try:
            from PIL import Image as _
        except ImportError:
            print "Missing PIL module. Please run 'pip install pillow'"
            ret = False

        try:
            import psutil as _
        except ImportError:
            print "Missing psutil module. Please run 'pip install psutil'"
            ret = False

        try:
            import requests as _
        except ImportError:
            print "Missing requests module. Please run 'pip install requests'"
            ret = False

        try:
            import ujson as _
        except ImportError:
            print "Missing ujson parser. Please run 'pip install ujson'"
            ret = False

        try:
            subprocess.check_output(['python', '--version'])
        except Exception:
            print "Make sure python 2.7 is available in the path."
            ret = False

        try:
            subprocess.check_output('convert -version', shell=True)
        except Exception:
            print "Missing convert utility. Please install ImageMagick " \
                  "and make sure it is in the path."
            ret = False

        try:
            subprocess.check_output('mogrify -version', shell=True)
        except Exception:
            print "Missing mogrify utility. Please install ImageMagick " \
                  "and make sure it is in the path."
            ret = False

        if self.options.xvfb:
            try:
                from xvfbwrapper import Xvfb
                self.xvfb = Xvfb(width=1920, height=1200, colordepth=24)
                self.xvfb.start()
            except ImportError:
                print "Missing xvfbwrapper module. Please run 'pip install xvfbwrapper'"
                ret = False

        # Windows-specific imports
        if platform.system() == "Windows":
            try:
                import win32api as _
                import win32process as _
            except ImportError:
                print "Missing pywin32 module. Please run 'python -m pip install pypiwin32'"
                ret = False

        if not self.options.android:
            self.wait_for_idle(300)
        self.shaper.remove()
        if not self.shaper.install():
            print "Error configuring traffic shaping, make sure it is installed."
            ret = False

        if self.adb is not None:
            if not self.adb.start():
                print "Error configuring adb. Make sure it is installed and in the path."
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

def find_browsers():
    """Find the various known-browsers in case they are not explicitly configured"""
    browsers = parse_ini(os.path.join(os.path.dirname(__file__), "browsers.ini"))
    if browsers is None:
        browsers = {}
    plat = platform.system()
    if plat == "Windows":
        local_appdata = os.getenv('LOCALAPPDATA')
        program_files = os.getenv('ProgramFiles')
        program_files_x86 = os.getenv('ProgramFiles(x86)')
        if program_files is not None and 'Chrome' not in browsers:
            chrome_path = os.path.join(program_files, 'Google', 'Chrome',
                                       'Application', 'chrome.exe')
            if os.path.isfile(chrome_path):
                browsers['Chrome'] = {'exe': chrome_path}
        if program_files_x86 is not None and 'Chrome' not in browsers:
            chrome_path = os.path.join(program_files_x86, 'Google', 'Chrome',
                                       'Application', 'chrome.exe')
            if os.path.isfile(chrome_path):
                browsers['Chrome'] = {'exe': chrome_path}
        if local_appdata is not None and 'Chrome' not in browsers:
            chrome_path = os.path.join(local_appdata, 'Google', 'Chrome',
                                       'Application', 'chrome.exe')
            if os.path.isfile(chrome_path):
                browsers['Chrome'] = {'exe': chrome_path}
        if local_appdata is not None and 'Canary' not in browsers:
            canary_path = os.path.join(local_appdata, 'Google', 'Chrome SxS',
                                       'Application', 'chrome.exe')
            if os.path.isfile(canary_path):
                browsers['Canary'] = {'exe': canary_path}
        # Firefox browsers
        if program_files_x86 is not None and 'Firefox' not in browsers:
            firefox_path = os.path.join(program_files_x86, 'Mozilla Firefox',
                                        'firefox.exe')
            if os.path.isfile(firefox_path):
                browsers['Firefox'] = {'exe': firefox_path, 'type': 'Firefox'}
        if program_files_x86 is not None and 'Firefox Nightly' not in browsers:
            firefox_path = os.path.join(program_files_x86, 'Nightly', 'firefox.exe')
            if os.path.isfile(firefox_path):
                browsers['Firefox Nightly'] = {'exe': firefox_path, 'type': 'Firefox'}
    elif plat == "Linux":
        chrome_path = '/opt/google/chrome/chrome'
        if 'Chrome' not in browsers and os.path.isfile(chrome_path):
            browsers['Chrome'] = {'exe': chrome_path}
        beta_path = '/opt/google/chrome-beta/chrome'
        if 'Chrome Beta' not in browsers and os.path.isfile(beta_path):
            browsers['Chrome Beta'] = {'exe': beta_path}
        # google-chrome-unstable is the closest thing to Canary for Linux
        canary_path = '/opt/google/chrome-unstable/chrome'
        if os.path.isfile(canary_path):
            if 'Chrome Dev' not in browsers:
                browsers['Chrome Dev'] = {'exe': canary_path}
            if 'Chrome Canary' not in browsers:
                browsers['Chrome Canary'] = {'exe': canary_path}
            if 'Canary' not in browsers:
                browsers['Canary'] = {'exe': canary_path}
    elif plat == "Darwin":
        pass
    return browsers

def main():
    """Startup and initialization"""
    import argparse
    parser = argparse.ArgumentParser(description='WebPageTest Agent.', prog='wpt-agent')
    # Basic agent config
    parser.add_argument('-v', '--verbose', action='count',
                        help="Increase verbosity (specify multiple times for more)."
                        " -vvvv for full debug output.")
    parser.add_argument('--name', help="Agent name (for the work directory).")
    parser.add_argument('--exit', type=int, default=0,
                        help='Exit after the specified number of minutes.\n'\
                        '    Useful for running in a shell script that does some maintenence\n'\
                        '    or updates periodically (like hourly).')
    parser.add_argument('--xvfb', action='store_true', default=False,
                        help="Use an xvfb virtual display (Linux only).")
    parser.add_argument('--dockerized', action='store_true', default=False,
                        help="Agent is running in a docker container.")
    parser.add_argument('--ec2', action='store_true', default=False,
                        help="Load config settings from EC2 user data.")
    parser.add_argument('--gce', action='store_true', default=False,
                        help="Load config settings from GCE user data.")

    # Server/location configuration
    parser.add_argument('--server',
                        help="URL for WebPageTest work (i.e. http://www.webpagetest.org/work/).")
    parser.add_argument('--location',
                        help="Location ID (as configured in locations.ini on the server).")
    parser.add_argument('--key', help="Location key (optional).")

    # Traffic-shaping options (defaults to host-based)
    parser.add_argument('--shaper', help='Override default traffic shaper. '\
                        'Current supported values are:\n'\
                        '    none - Disable traffic-shaping (i.e. when root is not available)\n.'\
                        '    netem,<interface> - Use NetEm for bridging rndis traffic '\
                        '(specify outbound interface).  i.e. --shaper netem,eth0\n'\
                        '    remote,<server>,<down pipe>,<up pipe> - Connect to the remote server '\
                        'over ssh and use pre-configured dummynet pipes (ssh keys for root user '\
                        'should be pre-authorized).')

    # Android options
    parser.add_argument('--android', action='store_true', default=False,
                        help="Run tests on an attached android device.")
    parser.add_argument('--device',
                        help="Device ID (only needed if more than one android device attached).")
    parser.add_argument('--rndis',
                        help="Enable reverse-tethering over rndis.  Valid options are:\n"\
                        "    dhcp: Configure interface for DHCP\n"\
                        "    <ip>/<network>,<gateway>,<dns1>,<dns2>: Static Address.  \n"\
                        "        i.e. 192.168.0.8/24,192.168.0.1,8.8.8.8,8.8.4.4")

    # Options for authenticating the agent with the server
    parser.add_argument('--username',
                        help="User name if using HTTP Basic auth with WebPageTest server.")
    parser.add_argument('--password',
                        help="Password if using HTTP Basic auth with WebPageTest server.")
    parser.add_argument('--cert', help="Client certificate if using certificates to "\
                        "authenticate the WebPageTest server connection.")
    parser.add_argument('--certkey', help="Client-side private key (if not embedded in the cert).")
    options, _ = parser.parse_known_args()

    # Make sure we are running python 2.7.11 or newer (required for Windows 8.1)
    if platform.system() == "Windows":
        if sys.version_info[0] != 2 or \
                sys.version_info[1] != 7 or \
                sys.version_info[2] < 11:
            print "Requires python 2.7.11 (2.7.11 or later)"
            exit(1)
    elif sys.version_info[0] != 2 or sys.version_info[1] != 7:
        print "Requires python 2.7"
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

    browsers = None
    if not options.android:
        browsers = find_browsers()
        if len(browsers) == 0:
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
