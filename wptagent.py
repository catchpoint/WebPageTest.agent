#!/usr/bin/env python
# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""WebPageTest cross-platform agent"""
import atexit
import logging
import logging.handlers
import os
import platform
import gzip
import re
import signal
import subprocess
import sys
import time
import traceback
if (sys.version_info >= (3, 0)):
    GZIP_TEXT = 'wt'
else:
    GZIP_TEXT = 'w'
try:
    import ujson as json
except BaseException:
    import json

class WPTAgent(object):
    """Main agent workflow"""
    def __init__(self, options, browsers):
        from internal.browsers import Browsers
        from internal.webpagetest import WebPageTest
        from internal.traffic_shaping import TrafficShaper
        from internal.adb import Adb
        from internal.ios_device import iOSDevice
        self.must_exit = False
        self.needs_shutdown = False
        self.options = options
        self.capture_display = None
        self.health_check_server = None
        self.message_server = None
        self.job = None
        self.task = None
        self.xvfb = None
        self.root_path = os.path.abspath(os.path.dirname(__file__))
        self.wpt = WebPageTest(options, os.path.join(self.root_path, "work"))
        self.persistent_work_dir = self.wpt.get_persistent_dir()
        self.adb = Adb(self.options, self.persistent_work_dir) if self.options.android else None
        self.ios = iOSDevice(self.options.device) if self.options.iOS else None
        self.browsers = Browsers(options, browsers, self.adb, self.ios)
        self.browser = None
        self.shaper = TrafficShaper(options, self.root_path)
        self.pubsub_message = None
        # Install the signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        if sys.platform != "win32":
            signal.signal(signal.SIGHUP, self.signal_handler)
        atexit.register(self.cleanup)
        self.image_magick = {'convert': 'convert', 'compare': 'compare', 'mogrify': 'mogrify'}
        if platform.system() == "Windows":
            paths = [os.getenv('ProgramFiles'), os.getenv('ProgramFiles(x86)')]
            for path in paths:
                if path is not None and os.path.isdir(path):
                    dirs = sorted(os.listdir(path), reverse=True)
                    for subdir in dirs:
                        if subdir.lower().startswith('imagemagick'):
                            convert = os.path.join(path, subdir, 'convert.exe')
                            compare = os.path.join(path, subdir, 'compare.exe')
                            mogrify = os.path.join(path, subdir, 'mogrify.exe')
                            if os.path.isfile(convert) and \
                                    os.path.isfile(compare) and \
                                    os.path.isfile(mogrify):
                                if convert.find(' ') >= 0:
                                    convert = '"{0}"'.format(convert)
                                if compare.find(' ') >= 0:
                                    compare = '"{0}"'.format(compare)
                                if mogrify.find(' ') >= 0:
                                    mogrify = '"{0}"'.format(mogrify)
                                self.image_magick['convert'] = convert
                                self.image_magick['compare'] = compare
                                self.image_magick['mogrify'] = mogrify
                                break

        if 'alertsize' in options and options.alertsize == True:
            import configparser
            self.alert_config = configparser.ConfigParser()
            self.alert_config.read('internal/config/alert_config.ini')

    def run_testing(self):
        """Main testing flow"""
        if (sys.version_info >= (3, 0)):
            from time import monotonic
        else:
            from monotonic import monotonic
        start_time = monotonic()
        browser = None
        done = False
        exit_file = os.path.join(self.root_path, 'exit')
        shutdown_file = os.path.join(self.root_path, 'shutdown')
        self.message_server = None
        if not self.options.android and not self.options.iOS:
            from internal.message_server import MessageServer
            self.message_server = MessageServer()
            self.message_server.start()
            if not self.message_server.is_ok():
                logging.error("Unable to start the local message server")
                return
        if self.options.healthcheckport:
            from internal.health_check_server import HealthCheckServer
            self.health_check_server = HealthCheckServer(self.options.healthcheckport)
            self.health_check_server.start()
            if not self.health_check_server.is_ok():
                logging.error("Unable to start the health check server")
                return
            self.wpt.health_check_server = self.health_check_server
    
        # If we are using a pubsub scription, start the listening thread
        subscriber = None
        streaming_pull_future = None
        if self.options.pubsub:
            try:
                from google.cloud import pubsub_v1
                subscriber = pubsub_v1.SubscriberClient()
                subscription_path = self.options.pubsub
                flow_control = pubsub_v1.types.FlowControl(max_messages=1)
                streaming_pull_future = subscriber.subscribe(subscription_path, callback=self.pubsub_callback, flow_control=flow_control, await_callbacks_on_shutdown=True)
                logging.debug("Listening for messages on %s..", subscription_path)                
            except Exception:
                logging.exception('Error starting pubsub subscription')

        while not self.must_exit and not done:
            try:
                self.alive()
                if os.path.isfile(exit_file):
                    try:
                        os.remove(exit_file)
                    except Exception:
                        pass
                    self.must_exit = True
                    break
                elif os.path.isfile(shutdown_file):
                    try:
                        os.remove(exit_file)
                    except Exception:
                        pass
                    self.must_exit = True
                    self.needs_shutdown = True
                    break
                if self.message_server is not None and self.options.exit > 0 and not self.message_server.is_ok():
                    logging.error("Message server not responding, exiting")
                    break
                if self.health_check_server is not None and self.options.exit > 0 and not self.health_check_server.is_ok():
                    logging.error("Health check server not responding, exiting")
                    break
                if self.options.pubsub:
                    self.sleep(self.options.polling)
                else:
                    if self.browsers.is_ready():
                        if self.options.testurl or self.options.testspec:
                            done = True
                            try:
                                test_json = {}
                                if self.options.testspec:
                                    with open(self.options.testspec, 'rt') as f_in:
                                        test_json = json.load(f_in)
                                if self.options.testurl:
                                    test_json['url'] = self.options.testurl
                                if self.options.browser:
                                    test_json['browser'] = self.options.browser
                                if 'runs' not in test_json:
                                    test_json['runs'] = self.options.testruns
                                if 'fvonly' not in test_json:
                                    test_json['fvonly'] = not self.options.testrv
                                self.job = self.wpt.process_job_json(test_json)
                            except Exception:
                                logging.exception('Error processing test options')
                        else:
                            self.job = self.wpt.get_test(self.browsers.browsers)
                        self.run_job()
                    elif self.options.exit > 0 and self.browsers.should_exit():
                        self.must_exit = True
                    if self.job is not None:
                        self.job = None
                    elif not done and not self.must_exit:
                        self.sleep(self.options.polling)
            except Exception as err:
                msg = ''
                if err is not None and err.__str__() is not None:
                    msg = err.__str__()
                if self.task is not None:
                    self.task['error'] = 'Unhandled exception preparing test: '\
                        '{0}'.format(msg)
                logging.exception("Unhandled exception: %s", msg)
                traceback.print_exc(file=sys.stdout)
                if browser is not None:
                    browser.on_stop_capture(None)
                    browser.on_stop_recording(None)
                    browser = None
            if self.options.exit > 0:
                run_time = (monotonic() - start_time) / 60.0
                if run_time > self.options.exit:
                    done = True
            # Exit if adb is having issues (will cause a reboot after several tries)
            if self.adb is not None and self.adb.needs_exit:
                done = True
        # Shut down pubsub
        if streaming_pull_future:
            try:
                streaming_pull_future.cancel()
                streaming_pull_future.result(timeout=300)
            except:
                pass
        self.cleanup()
        if self.needs_shutdown:
            if platform.system() == "Linux":
                subprocess.call(['sudo', 'poweroff'])

    def pubsub_callback(self, message):
        """Pubsub callback for jobs"""
        logging.debug('Received pubsub job')
        self.pubsub_message = message
        try:
            test_json = json.loads(message.data.decode('utf-8'))
            # Don't re-run the same test if it already exists in gcs
            exists = False
            if 'gcs_har_upload' in test_json and \
                    'bucket' in test_json['gcs_har_upload'] and \
                    'path' in test_json['gcs_har_upload']:
                try:
                    from google.cloud import storage
                    client = storage.Client()
                    bucket = client.get_bucket(test_json['gcs_har_upload']['bucket'])
                    gcs_path = os.path.join(test_json['gcs_har_upload']['path'], test_json['Test ID'] + '.har.gz')
                    blob = bucket.blob(gcs_path)
                    exists = blob.exists()
                except Exception:
                    logging.exception('Error checking for HAR in Cloud Storage')
            if not exists:
                self.job = self.wpt.process_job_json(test_json)
                self.run_job()
        except Exception:
            logging.exception('Error processing pubsub job')
        self.pubsub_message = None
        message.ack()

    def run_job(self):
        """Run a single job from start to end"""
        try:
            if (sys.version_info >= (3, 0)):
                from time import monotonic
            else:
                from monotonic import monotonic
            if self.job is not None:
                self.job['image_magick'] = self.image_magick
                self.job['message_server'] = self.message_server
                self.job['capture_display'] = self.capture_display
                self.job['shaper'] = self.shaper
                self.task = self.wpt.get_task(self.job)
                while self.task is not None:
                    start = monotonic()
                    try:
                        self.task['running_lighthouse'] = False
                        if self.job['type'] != 'lighthouse':
                            self.run_single_test()
                            self.wpt.get_bodies(self.task)
                        if self.task['run'] == 1 and not self.task['cached'] and \
                                self.job['warmup'] <= 0 and \
                                self.task['error'] is None and \
                                'lighthouse' in self.job and self.job['lighthouse']:
                            if 'page_result' not in self.task or \
                                    self.task['page_result'] is None or \
                                    self.task['page_result'] == 0 or \
                                    self.task['page_result'] == 99999:
                                self.task['running_lighthouse'] = True
                                self.wpt.running_another_test(self.task)
                                self.run_single_test()
                        elapsed = monotonic() - start
                        logging.debug('Test run time: %0.3f sec', elapsed)
                    except Exception as err:
                        msg = ''
                        if err is not None and err.__str__() is not None:
                            msg = err.__str__()
                        self.task['error'] = 'Unhandled exception running test: '\
                            '{0}'.format(msg)
                        logging.exception("Unhandled exception running test: %s", msg)
                        traceback.print_exc(file=sys.stdout)
                    self.wpt.upload_task_result(self.task)
                    # Set up for the next run
                    self.task = self.wpt.get_task(self.job)
                self.output_test_result()
        except Exception:
            logging.exception('Error running job')

    def output_test_result(self):
        """Dump the result of a CLI test to stdout"""
        if self.options.testout is not None:
            test_id = self.wpt.last_test_id
            if self.options.testout == 'id':
                print("{}".format(test_id))
            elif self.options.testout == 'url' and self.options.server is not None:
                print("{0}result/{1}/".format(self.options.server[:-5], test_id))

    def run_single_test(self):
        """Run a single test run"""
        if self.health_check_server is not None:
            self.health_check_server.healthy()
        try:
            if self.pubsub_message is not None:
                self.pubsub_message.modify_ack_deadline(600)
        except Exception:
            logging.exception('Error extending pubsub ack deadline')
        self.alive()
        self.browser = self.browsers.get_browser(self.job['browser'], self.job)
        if self.browser is not None:
            self.browser.prepare(self.job, self.task)
            self.browser.launch(self.job, self.task)
            try:
                if self.task['running_lighthouse']:
                    self.task['lighthouse_log'] = 'Lighthouse testing is not supported with this browser.'
                    try:
                        self.browser.run_lighthouse_test(self.task)
                    except Exception:
                        logging.exception('Error running lighthouse test')
                    if self.task['lighthouse_log']:
                        try:
                            log_file = os.path.join(self.task['dir'], 'lighthouse.log.gz')
                            with gzip.open(log_file, GZIP_TEXT, 7) as f_out:
                                f_out.write(self.task['lighthouse_log'])
                        except Exception:
                            logging.exception('Error compressing lighthouse log')
                else:
                    self.browser.run_task(self.task)
                    # Alerts on large files in the results folder
                    if 'alertsize' in self.options and self.options.alertsize:
                        self.browser.alert_size(self.alert_config,self.task['dir'], self.task['task_prefix'])
            except Exception as err:
                msg = ''
                if err is not None and err.__str__() is not None:
                    msg = err.__str__()
                self.task['error'] = 'Unhandled exception in test run: '\
                    '{0}'.format(msg)
                logging.exception("Unhandled exception in test run: %s", msg)
                traceback.print_exc(file=sys.stdout)
            self.browser.stop(self.job, self.task)
            # Delete the browser profile if needed
            if self.task['cached'] or self.job['fvonly']:
                self.browser.clear_profile(self.task)
        else:
            err = "Invalid browser - {0}".format(self.job['browser'])
            logging.critical(err)
            self.task['error'] = err
        self.browser = None

    def signal_handler(self, signum, frame):
        """Ctrl+C handler"""
        try:
            if not self.must_exit:
                logging.info("Exiting...")
                self.must_exit = True
                if self.wpt is not None:
                    self.wpt.shutdown()
                if self.browser is not None:
                    self.browser.shutdown()
            else:
                logging.info("Waiting for graceful exit...")
        except Exception as e:
            logging.exception("Error in signal handler")

    def cleanup(self):
        """Do any cleanup that needs to be run regardless of how we exit"""
        logging.debug('Cleaning up')
        if self.wpt:
            self.wpt.shutdown()
        if self.browser:
            self.browser.shutdown()
        self.shaper.remove()
        if self.xvfb is not None:
            self.xvfb.stop()
        if self.adb is not None:
            self.adb.stop()
        if self.ios is not None:
            self.ios.disconnect()

    def sleep(self, seconds):
        """Sleep wrapped in an exception handler to properly deal with Ctrl+C"""
        try:
            time.sleep(seconds)
        except IOError:
            pass

    def wait_for_idle(self, timeout=30):
        """Wait for the system to go idle for at least 2 seconds"""
        if self.options.noidle:
            return
        if (sys.version_info >= (3, 0)):
            from time import monotonic
        else:
            from monotonic import monotonic
        import psutil
        logging.debug("Waiting for Idle...")
        cpu_count = psutil.cpu_count()
        if cpu_count > 0:
            target_pct = max(50. / float(cpu_count), 10.)
            idle_start = None
            end_time = monotonic() + timeout
            last_update = monotonic()
            idle = False
            while not idle and monotonic() < end_time:
                self.alive()
                check_start = monotonic()
                pct = psutil.cpu_percent(interval=0.5)
                if pct <= target_pct:
                    if idle_start is None:
                        idle_start = check_start
                    if monotonic() - idle_start > 2:
                        idle = True
                else:
                    idle_start = None
                if not idle and monotonic() - last_update > 1:
                    last_update = monotonic()
                    logging.debug("CPU Utilization: %0.1f%% (%d CPU's, %0.1f%% target)", pct, cpu_count, target_pct)

    def alive(self):
        """Touch a watchdog file indicating we are still alive"""
        if self.options.alive:
            with open(self.options.alive, 'a'):
                os.utime(self.options.alive, None)

    def requires(self, module, module_name=None):
        """Try importing a module and installing it if it isn't available"""
        ret = False
        if module_name is None:
            module_name = module
        try:
            __import__(module)
            ret = True
        except ImportError:
            pass
        if not ret and sys.version_info < (3, 0):
            from internal.os_util import run_elevated
            logging.debug('Trying to install %s...', module_name)
            subprocess.call([sys.executable, '-m', 'pip', 'uninstall', '-y', module_name])
            run_elevated(sys.executable, '-m pip uninstall -y {0}'.format(module_name))
            subprocess.call([sys.executable, '-m', 'pip', 'install', module_name])
            run_elevated(sys.executable, '-m pip install {0}'.format(module_name))
            try:
                __import__(module)
                ret = True
            except ImportError:
                pass
        if not ret:
            if (sys.version_info >= (3, 0)):
                logging.error("Missing {0} module. Please run 'pip3 install {1}'".format(module, module_name))
            else:
                logging.error("Missing {0} module. Please run 'pip install {1}'".format(module, module_name))
        return ret

    def startup(self, detected_browsers):
        """Validate that all of the external dependencies are installed"""
        ret = True

        # default /tmp/wptagent as an alive file on Linux
        if self.options.alive is None:
            if platform.system() == "Linux":
                self.options.alive = '/tmp/wptagent'
            else:
                self.options.alive = os.path.join(os.path.dirname(__file__), 'wptagent.alive')
        self.alive()
        ret = self.requires('dns', 'dnspython') and ret
        ret = self.requires('monotonic') and ret
        ret = self.requires('PIL', 'pillow') and ret
        ret = self.requires('psutil') and ret
        ret = self.requires('requests') and ret
        if platform.system() == 'Darwin':
            ret = self.requires('AppKit', 'PyObjC') and ret
        if not self.options.android and not self.options.iOS:
            ret = self.requires('tornado') and ret
            if 'Firefox' in detected_browsers:
                ret = self.requires('selenium')
        # Windows-specific imports
        if platform.system() == "Windows":
            ret = self.requires('win32api', 'pywin32') and ret

        if detected_browsers is not None and 'Safari' in detected_browsers and not self.options.iOS:
            # if running for safari
            ret = self.requires('selenium')

        # Optional imports
        self.requires('fontTools', 'fonttools')

        # Try patching ws4py with a faster lib
        try:
            self.requires('wsaccel')
            import wsaccel
            wsaccel.patch_ws4py()
        except Exception:
            logging.debug('wsaccel not installed, Chrome debug interface will be slower than it could be')

        try:
            subprocess.check_output([sys.executable, '--version'])
        except Exception:
            logging.critical("Unable to start python.")
            ret = False

        try:
            subprocess.check_output('{0} -version'.format(self.image_magick['convert']), shell=True)
        except Exception:
            logging.critical("Missing convert utility. Please install ImageMagick and make sure it is in the path.")
            ret = False

        try:
            subprocess.check_output('{0} -version'.format(self.image_magick['mogrify']), shell=True)
        except Exception:
            logging.critical("Missing mogrify utility. Please install ImageMagick and make sure it is in the path.")
            ret = False

        if platform.system() == "Linux":
            try:
                subprocess.check_output(['traceroute', '--version'])
            except Exception:
                logging.debug("Traceroute is missing, installing...")
                subprocess.call(['sudo', 'apt', '-yq', 'install', 'traceroute'])

        if not self.options.android and not self.options.iOS and 'Firefox' in detected_browsers:
            try:
                subprocess.check_output(['geckodriver', '-V'])
            except Exception:
                logging.debug("geckodriver is missing, installing...")
                subprocess.call(['sudo', 'apt', '-yq', 'install', 'firefox-geckodriver'])

        # If we are on Linux and there is no display, enable xvfb by default
        if platform.system() == "Linux" and not self.options.android and \
                not self.options.iOS and 'DISPLAY' not in os.environ:
            self.options.xvfb = True

        if self.options.xvfb:
            ret = self.requires('xvfbwrapper') and ret
            if ret:
                from xvfbwrapper import Xvfb
                self.xvfb = Xvfb(width=1920, height=1200, colordepth=24)
                self.xvfb.start()

        # Figure out which display to capture from
        if not self.options.android and not self.options.iOS:
            if platform.system() == "Linux" and 'DISPLAY' in os.environ:
                logging.debug('Display: %s', os.environ['DISPLAY'])
                self.capture_display = os.environ['DISPLAY']
            elif platform.system() == "Darwin":
                proc = subprocess.Popen('ffmpeg -f avfoundation -list_devices true -i ""',
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
                _, err = proc.communicate()
                for line in err.splitlines():
                    matches = re.search(r'\[(\d+)\] Capture screen', line.decode('utf-8'))
                    if matches:
                        self.capture_display = matches.group(1)
                        break
            elif platform.system() == "Windows":
                self.capture_display = 'desktop'
            if self.capture_display is None:
                logging.critical('No capture display available')
                ret = False

        # Fix Lighthouse install permissions
        if platform.system() != "Windows":
            from internal.os_util import run_elevated
            run_elevated('chmod', '-R 777 ~/.config/configstore/')
            try:
                import getpass
                run_elevated('chown', '-R {0}:{0} ~/.config'.format(getpass.getuser()))
            except Exception:
                pass

        # Check for Node 14+
        if self.get_node_version() < 14.0:
            if platform.system() == "Linux":
                # This only works on debian-based systems
                logging.debug('Updating Node.js to 14.x')
                subprocess.call('sudo apt -y install curl dirmngr apt-transport-https lsb-release ca-certificates', shell=True)
                subprocess.call('curl -fsSL https://deb.nodesource.com/setup_14.x | sudo -E bash -', shell=True)
                subprocess.call(['sudo', 'apt-get', 'install', '-y', 'nodejs'])
            if self.get_node_version() < 12.0:
                logging.warning("Node.js 12 or newer is required for Lighthouse testing")

        # Check the iOS install
        if self.ios is not None:
            ret = self.requires('usbmuxwrapper') and ret
            ret = self.ios.check_install() and ret

        if not self.options.android and not self.options.iOS:
            self.wait_for_idle(300)
        if self.adb is not None:
            if not self.adb.start():
                logging.critical("Error configuring adb. Make sure it is installed and in the path.")
                ret = False
        self.shaper.remove()
        if not self.shaper.install():
            if platform.system() == "Windows":
                logging.critical("Error configuring traffic shaping, make sure secure boot is disabled.")
            else:
                logging.critical("Error configuring traffic shaping, make sure it is installed.")
            ret = False

        # Update the Windows root certs
        if platform.system() == "Windows":
            self.update_windows_certificates()

        return ret

    def get_node_version(self):
        """Get the installed version of Node.js"""
        version = 0
        try:
            if (sys.version_info >= (3, 0)):
                stdout = subprocess.check_output(['node', '--version'], encoding='UTF-8')
            else:
                stdout = subprocess.check_output(['node', '--version'])
            matches = re.match(r'^v(\d+\.\d+)', stdout)
            if matches:
                version = float(matches.group(1))
        except Exception:
            pass
        return version

    def update_windows_certificates(self):
        """ Update the root Windows certificates"""
        try:
            cert_file = os.path.join(self.persistent_work_dir, 'root_certs.sst')
            if not os.path.isdir(self.persistent_work_dir):
                os.makedirs(self.persistent_work_dir)
            needs_update = True
            if os.path.isfile(cert_file):
                days = (time.time() - os.path.getmtime(cert_file)) / 86400
                if days < 5:
                    needs_update = False
            if needs_update:
                logging.debug("Updating Windows root certificates...")
                if os.path.isfile(cert_file):
                    os.unlink(cert_file)
                from internal.os_util import run_elevated
                run_elevated('certutil.exe', '-generateSSTFromWU "{0}"'.format(cert_file))
                if os.path.isfile(cert_file):
                    run_elevated('certutil.exe', '-addstore -f Root "{0}"'.format(cert_file))
        except Exception:
            pass


def parse_ini(ini):
    """Parse an ini file and convert it to a dictionary"""
    ret = None
    if os.path.isfile(ini):
        parser = None
        try:
            import ConfigParser
            parser = ConfigParser.SafeConfigParser()
        except BaseException:
            import configparser
            parser = configparser.ConfigParser()
        parser.read(ini)
        ret = {}
        for section in parser.sections():
            ret[section] = {}
            for item in parser.items(section):
                ret[section][item[0]] = item[1]
        if not ret:
            ret = None
    return ret


def get_windows_build():
    """Get the current Windows build number from the registry"""
    key = 'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion'
    val = 'CurrentBuild'
    output = os.popen('REG QUERY "{0}" /V "{1}"'.format(key, val)).read()
    return int(output.strip().split(' ')[-1])


def find_browsers(options):
    """Find the various known-browsers in case they are not explicitly configured"""
    browsers = parse_ini(os.path.join(os.path.dirname(__file__), "browsers.ini"))
    if browsers is None:
        browsers = {}
    plat = platform.system()
    if plat == "Windows":
        local_appdata = os.getenv('LOCALAPPDATA')
        program_files = str(os.getenv('ProgramFiles'))
        program_files_x86 = str(os.getenv('ProgramFiles(x86)'))
        # Allow 32-bit python to detect 64-bit browser installs
        if program_files == program_files_x86 and program_files.find(' (x86)') >= 0:
            program_files = program_files.replace(' (x86)', '')
        # Chrome
        paths = [program_files, program_files_x86, local_appdata]
        channels = ['Chrome', 'Chrome Beta', 'Chrome Dev']
        for channel in channels:
            for path in paths:
                if path is not None and channel not in browsers:
                    chrome_path = os.path.join(path, 'Google', channel,
                                               'Application', 'chrome.exe')
                    if os.path.isfile(chrome_path):
                        browsers[channel] = {'exe': chrome_path}
        if local_appdata is not None and 'Canary' not in browsers:
            canary_path = os.path.join(local_appdata, 'Google', 'Chrome SxS',
                                       'Application', 'chrome.exe')
            if os.path.isfile(canary_path):
                browsers['Canary'] = {'exe': canary_path}
                browsers['Chrome Canary'] = {'exe': canary_path}
        # Fall back to Chrome dev for Canary if Canary isn't available but dev is
        if 'Chrome Dev' in browsers and 'Canary' not in browsers:
            browsers['Chrome Canary'] = dict(browsers['Chrome Dev'])
            browsers['Canary'] = dict(browsers['Chrome Dev'])
        # Opera (same engine as Chrome)
        paths = [program_files, program_files_x86]
        channels = ['Opera', 'Opera beta', 'Opera developer']
        for channel in channels:
            for path in paths:
                if path is not None and channel not in browsers:
                    opera_path = os.path.join(path, channel, 'launcher.exe')
                    if os.path.isfile(opera_path):
                        browsers[channel] = {'exe': opera_path, 'other_exes': ['opera.exe']}
        # Firefox browsers
        paths = [program_files, program_files_x86]
        for path in paths:
            if path is not None and 'Firefox' not in browsers:
                firefox_path = os.path.join(path, 'Mozilla Firefox', 'firefox.exe')
                if os.path.isfile(firefox_path):
                    browsers['Firefox'] = {'exe': firefox_path, 'type': 'Firefox'}
            if path is not None and 'Firefox' not in browsers:
                firefox_path = os.path.join(path, 'Firefox', 'firefox.exe')
                if os.path.isfile(firefox_path):
                    browsers['Firefox'] = {'exe': firefox_path, 'type': 'Firefox'}
            if path is not None and 'Firefox ESR' not in browsers:
                firefox_path = os.path.join(path, 'Mozilla Firefox ESR', 'firefox.exe')
                if os.path.isfile(firefox_path):
                    browsers['Firefox ESR'] = {'exe': firefox_path, 'type': 'Firefox'}
            if path is not None and 'Firefox Beta' not in browsers:
                firefox_path = os.path.join(path, 'Mozilla Firefox Beta', 'firefox.exe')
                if os.path.isfile(firefox_path):
                    browsers['Firefox Beta'] = {'exe': firefox_path, 'type': 'Firefox'}
            if path is not None and 'Firefox Beta' not in browsers:
                firefox_path = os.path.join(path, 'Firefox Beta', 'firefox.exe')
                if os.path.isfile(firefox_path):
                    browsers['Firefox Beta'] = {'exe': firefox_path, 'type': 'Firefox'}
            if path is not None and 'Firefox Dev' not in browsers:
                firefox_path = os.path.join(path, 'Mozilla Firefox Dev', 'firefox.exe')
                if os.path.isfile(firefox_path):
                    browsers['Firefox Dev'] = {'exe': firefox_path, 'type': 'Firefox'}
            if path is not None and 'Firefox Dev' not in browsers:
                firefox_path = os.path.join(path, 'Firefox Dev', 'firefox.exe')
                if os.path.isfile(firefox_path):
                    browsers['Firefox Dev'] = {'exe': firefox_path, 'type': 'Firefox'}
            if path is not None and 'Firefox Nightly' not in browsers:
                firefox_path = os.path.join(path, 'Nightly', 'firefox.exe')
                if os.path.isfile(firefox_path):
                    browsers['Firefox Nightly'] = {'exe': firefox_path,
                                                   'type': 'Firefox',
                                                   'log_level': 5}
        # Microsoft Edge (Legacy)
        edge = None
        try:
            build = get_windows_build()
            if build >= 10240:
                edge_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'internal',
                                        'support', 'edge', 'current', 'MicrosoftWebDriver.exe')
                if not os.path.isfile(edge_exe):
                    if build > 17134:
                        edge_exe = os.path.join(os.environ['windir'], 'System32', 'MicrosoftWebDriver.exe')
                    else:
                        if build >= 17000:
                            edge_version = 17
                        elif build >= 16000:
                            edge_version = 16
                        elif build >= 15000:
                            edge_version = 15
                        elif build >= 14000:
                            edge_version = 14
                        elif build >= 10586:
                            edge_version = 13
                        else:
                            edge_version = 12
                        edge_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'internal',
                                                'support', 'edge', str(edge_version),
                                                'MicrosoftWebDriver.exe')
                if os.path.isfile(edge_exe):
                    edge = {'exe': edge_exe}
        except Exception:
            logging.exception('Error getting windows build, skipping check for legacy Edge')
        if edge is not None:
            edge['type'] = 'Edge'
            if 'Microsoft Edge (EdgeHTML)' not in browsers:
                browsers['Microsoft Edge (EdgeHTML)'] = dict(edge)
            if 'Microsoft Edge' not in browsers:
                browsers['Microsoft Edge'] = dict(edge)
            if 'Edge' not in browsers:
                browsers['Edge'] = dict(edge)
        # Microsoft Edge (Chromium)
        paths = [program_files, program_files_x86, local_appdata]
        channels = ['Edge', 'Edge Dev']
        for channel in channels:
            for path in paths:
                edge_path = os.path.join(path, 'Microsoft', channel, 'Application', 'msedge.exe')
                if os.path.isfile(edge_path):
                    browser_name = 'Microsoft {0} (Chromium)'.format(channel)
                    if browser_name not in browsers:
                        browsers[browser_name] = {'exe': edge_path}
                        if channel == 'Edge' and 'Edgium' not in browsers:
                            browsers['Edgium'] = {'exe': edge_path}
                        elif channel == 'Edge Dev' and 'Edgium Dev' not in browsers:
                            browsers['Edgium Dev'] = {'exe': edge_path}
        if local_appdata is not None and 'Microsoft Edge Canary (Chromium)' not in browsers:
            edge_path = os.path.join(local_appdata, 'Microsoft', 'Edge SxS',
                                     'Application', 'msedge.exe')
            if os.path.isfile(edge_path):
                browsers['Microsoft Edge Canary (Chromium)'] = {'exe': edge_path}
                if 'Edgium Canary' not in browsers:
                    browsers['Edgium Canary'] = {'exe': edge_path}
        # Internet Explorer
        paths = [program_files, program_files_x86]
        for path in paths:
            if path is not None and 'IE' not in browsers:
                ie_path = os.path.join(path, 'Internet Explorer', 'iexplore.exe')
                if os.path.isfile(ie_path):
                    browsers['ie'] = {'exe': ie_path, 'type': 'IE'}
        # Brave
        paths = [program_files, program_files_x86]
        for path in paths:
            if path is not None and 'Brave' not in browsers:
                brave_path = os.path.join(path, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe')
                if os.path.isfile(brave_path):
                    browsers['Brave'] = {'exe': brave_path}
            if path is not None and 'Brave Beta' not in browsers:
                brave_path = os.path.join(path, 'BraveSoftware', 'Brave-Browser-Beta', 'Application', 'brave.exe')
                if os.path.isfile(brave_path):
                    browsers['Brave Beta'] = {'exe': brave_path}
            if path is not None and 'Brave Dev' not in browsers:
                brave_path = os.path.join(path, 'BraveSoftware', 'Brave-Browser-Dev', 'Application', 'brave.exe')
                if os.path.isfile(brave_path):
                    browsers['Brave Dev'] = {'exe': brave_path}
            if path is not None and 'Brave Nightly' not in browsers:
                brave_path = os.path.join(path, 'BraveSoftware', 'Brave-Browser-Nightly', 'Application', 'brave.exe')
                if os.path.isfile(brave_path):
                    browsers['Brave Nightly'] = {'exe': brave_path}
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
        # Chromium
        chromium_path = '/usr/lib/chromium-browser/chromium-browser'
        if 'Chromium' not in browsers and os.path.isfile(chromium_path):
            browsers['Chromium'] = {'exe': chromium_path}
        if 'Chrome' not in browsers and os.path.isfile(chromium_path):
            browsers['Chrome'] = {'exe': chromium_path}
        chromium_path = '/usr/bin/chromium-browser'
        if 'Chromium' not in browsers and os.path.isfile(chromium_path):
            browsers['Chromium'] = {'exe': chromium_path}
        if 'Chrome' not in browsers and os.path.isfile(chromium_path):
            browsers['Chrome'] = {'exe': chromium_path}
        # Opera
        opera_path = '/usr/lib/x86_64-linux-gnu/opera/opera'
        if 'Opera' not in browsers and os.path.isfile(opera_path):
            browsers['Opera'] = {'exe': opera_path}
        opera_path = '/usr/lib64/opera/opera'
        if 'Opera' not in browsers and os.path.isfile(opera_path):
            browsers['Opera'] = {'exe': opera_path}
        beta_path = '/usr/lib/x86_64-linux-gnu/opera-beta/opera-beta'
        if 'Opera beta' not in browsers and os.path.isfile(beta_path):
            browsers['Opera beta'] = {'exe': beta_path}
        beta_path = '/usr/lib64/opera-beta/opera-beta'
        if 'Opera beta' not in browsers and os.path.isfile(beta_path):
            browsers['Opera beta'] = {'exe': beta_path}
        dev_path = '/usr/lib/x86_64-linux-gnu/opera-developer/opera-developer'
        if 'Opera developer' not in browsers and os.path.isfile(dev_path):
            browsers['Opera developer'] = {'exe': dev_path}
        dev_path = '/usr/lib64/opera-developer/opera-developer'
        if 'Opera developer' not in browsers and os.path.isfile(dev_path):
            browsers['Opera developer'] = {'exe': dev_path}
        # Firefox browsers
        firefox_path = '/usr/lib/firefox/firefox'
        if 'Firefox' not in browsers and os.path.isfile(firefox_path):
            browsers['Firefox'] = {'exe': firefox_path, 'type': 'Firefox'}
        firefox_path = '/usr/bin/firefox'
        if 'Firefox' not in browsers and os.path.isfile(firefox_path):
            browsers['Firefox'] = {'exe': firefox_path, 'type': 'Firefox'}
        firefox_path = '/usr/lib/firefox-esr/firefox-esr'
        if 'Firefox' not in browsers and os.path.isfile(firefox_path):
            browsers['Firefox'] = {'exe': firefox_path, 'type': 'Firefox'}
        if 'Firefox ESR' not in browsers and os.path.isfile(firefox_path):
            browsers['Firefox ESR'] = {'exe': firefox_path, 'type': 'Firefox'}
        nightly_path = '/usr/lib/firefox-trunk/firefox-trunk'
        if 'Firefox Nightly' not in browsers and os.path.isfile(nightly_path):
            browsers['Firefox Nightly'] = {'exe': nightly_path,
                                           'type': 'Firefox',
                                           'log_level': 5}
        nightly_path = '/usr/bin/firefox-trunk'
        if 'Firefox Nightly' not in browsers and os.path.isfile(nightly_path):
            browsers['Firefox Nightly'] = {'exe': nightly_path,
                                           'type': 'Firefox',
                                           'log_level': 5}
        # Brave
        brave_path = '/opt/brave.com/brave/brave-browser'
        if 'Brave' not in browsers and os.path.isfile(brave_path):
            browsers['Brave'] = {'exe': brave_path}
        brave_path = '/opt/brave.com/brave-beta/brave-browser-beta'
        if 'Brave Beta' not in browsers and os.path.isfile(brave_path):
            browsers['Brave Beta'] = {'exe': brave_path}
        brave_path = '/opt/brave.com/brave-dev/brave-browser-dev'
        if 'Brave Dev' not in browsers and os.path.isfile(brave_path):
            browsers['Brave Dev'] = {'exe': brave_path}
        brave_path = '/opt/brave.com/brave-nightly/brave-browser-nightly'
        if 'Brave Nightly' not in browsers and os.path.isfile(brave_path):
            browsers['Brave Nightly'] = {'exe': brave_path}
        # Vivaldi
        vivaldi_path = '/usr/bin/vivaldi'
        if 'Vivaldi' not in browsers and os.path.isfile(vivaldi_path):
            browsers['Vivaldi'] = {'exe': vivaldi_path}
        # Microsoft Edge
        edge_path = '/usr/bin/microsoft-edge-stable'
        if os.path.isfile(edge_path):
            if 'Edge' not in browsers:
                browsers['Edge'] = {'exe': edge_path}
            if 'Microsoft Edge (Chromium)' not in browsers:
                browsers['Microsoft Edge (Chromium)'] = {'exe': edge_path}
            if 'Microsoft Edge' not in browsers:
                browsers['Microsoft Edge'] = {'exe': edge_path}
        edge_path = '/usr/bin/microsoft-edge-beta'
        if os.path.isfile(edge_path):
            if 'Microsoft Edge Beta (Chromium)' not in browsers:
                browsers['Microsoft Edge Beta (Chromium)'] = {'exe': edge_path}
            if 'Microsoft Edge Beta' not in browsers:
                browsers['Microsoft Edge Beta'] = {'exe': edge_path}
            if 'Edge Beta' not in browsers:
                browsers['Edge Beta'] = {'exe': edge_path}
            if 'Microsoft Edge (Chromium)' not in browsers:
                browsers['Microsoft Edge (Chromium)'] = {'exe': edge_path}
            if 'Microsoft Edge' not in browsers:
                browsers['Microsoft Edge'] = {'exe': edge_path}
        edge_path = '/usr/bin/microsoft-edge-dev'
        if os.path.isfile(edge_path):
            if 'Microsoft Edge Dev (Chromium)' not in browsers:
                browsers['Microsoft Edge Dev (Chromium)'] = {'exe': edge_path}
            if 'Microsoft Edge Dev' not in browsers:
                browsers['Microsoft Edge Dev'] = {'exe': edge_path}
            if 'Edge Dev' not in browsers:
                browsers['Edge Dev'] = {'exe': edge_path}
            if 'Microsoft Edge (Chromium)' not in browsers:
                browsers['Microsoft Edge (Chromium)'] = {'exe': edge_path}
            if 'Microsoft Edge' not in browsers:
                browsers['Microsoft Edge'] = {'exe': edge_path}
        # Epiphany (WebKit)
        epiphany_path = '/usr/bin/epiphany'
        if os.path.isfile(epiphany_path):
            if 'Epiphany' not in browsers:
                browsers['Epiphany'] = {'exe': epiphany_path, 'type': 'WebKitGTK'}
            if 'WebKit' not in browsers:
                browsers['WebKit'] = {'exe': epiphany_path, 'type': 'WebKitGTK'}

    elif plat == "Darwin":
        chrome_path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        if 'Chrome' not in browsers and os.path.isfile(chrome_path):
            browsers['Chrome'] = {'exe': chrome_path}
        chrome_path = '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta'
        if 'Chrome Beta' not in browsers and os.path.isfile(chrome_path):
            browsers['Chrome Beta'] = {'exe': chrome_path}
        chrome_path = '/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev'
        if 'Chrome Dev' not in browsers and os.path.isfile(chrome_path):
            browsers['Chrome Dev'] = {'exe': chrome_path}
        canary_path = '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary'
        if os.path.isfile(canary_path):
            if 'Chrome Dev' not in browsers:
                browsers['Chrome Dev'] = {'exe': canary_path}
            if 'Chrome Canary' not in browsers:
                browsers['Chrome Canary'] = {'exe': canary_path}
            if 'Canary' not in browsers:
                browsers['Canary'] = {'exe': canary_path}
        firefox_path = '/Applications/Firefox.app/Contents/MacOS/firefox'
        if 'Firefox' not in browsers and os.path.isfile(firefox_path):
            browsers['Firefox'] = {'exe': firefox_path, 'type': 'Firefox'}
        nightly_path = '/Applications/FirefoxNightly.app/Contents/MacOS/firefox'
        if 'Firefox Nightly' not in browsers and os.path.isfile(nightly_path):
            browsers['Firefox Nightly'] = {'exe': nightly_path,
                                           'type': 'Firefox',
                                           'log_level': 5}
        safari_path = '/Applications/Safari.app/Contents/MacOS/Safari'
        if 'Safari' not in browsers and os.path.isfile(safari_path):
            browsers['Safari'] = {'exe': safari_path, 'type': 'Safari'}
        # Get a list of all of the iOS simulator devices available
        try:
            logging.debug('Scanning for iOS simulator devices...')
            out = subprocess.check_output(['xcrun', 'simctl', 'list', '--json', 'devices', 'available'], universal_newlines=True)
            if out:
                devices = json.loads(out)
                if 'devices' in devices:
                    for runtime in devices['devices']:
                        if runtime.find('.iOS-') >= 0:
                            for device in devices['devices'][runtime]:
                                if 'name' in device:
                                    if device['name'] not in browsers:
                                        browsers[device['name']] = {'type': 'iOS Simulator', 'runtime': runtime, 'device': device}
                                        browsers[device['name'] + ' (simulator)'] = {'type': 'iOS Simulator', 'runtime': runtime, 'device': device}
                                        browsers[device['name'] + ' - Landscape'] = {'type': 'iOS Simulator', 'runtime': runtime, 'device': device, 'rotate': True}
                                        browsers[device['name'] + ' (simulator) - Landscape'] = {'type': 'iOS Simulator', 'runtime': runtime, 'device': device, 'rotate': True}
        except Exception:
            logging.exception('iOS Simulator devices unavailable')

    logging.debug('Detected Browsers:')
    for browser in browsers:
        if 'exe' in browsers[browser]:
            logging.debug('%s: %s', browser, browsers[browser]['exe'])
        else:
            logging.debug('%s', browser)

    return browsers


def upgrade_pip_modules():
    """Upgrade all of the outdated pip modules"""
    try:
        from internal.os_util import run_elevated
        subprocess.call([sys.executable, '-m', 'pip', 'install', '--upgrade', 'pip'])
        run_elevated(sys.executable, '-m pip install --upgrade pip')
        if (sys.version_info >= (3, 0)):
            out = subprocess.check_output([sys.executable, '-m', 'pip', 'list', '--outdated', '--format', 'freeze'], encoding='UTF-8')
        else:
            out = subprocess.check_output([sys.executable, '-m', 'pip', 'list', '--outdated', '--format', 'freeze'])
        for line in out.splitlines():
            separator = line.find('==')
            if separator > 0:
                package = line[:separator]
                run_elevated(sys.executable, '-m pip install --upgrade {0}'.format(package))
    except Exception:
        pass


def get_browser_versions(browsers):
    """Get the version of the available browsers"""
    from internal.os_util import get_file_version
    for browser in browsers:
        if 'exe' in browsers[browser] and \
                os.path.isfile(browsers[browser]['exe']):
            exe = browsers[browser]['exe']
            browsers[browser]['version'] = get_file_version(exe)


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
                        help='Exit after the specified number of minutes.\n'
                        '    Useful for running in a shell script that does some maintenence\n'
                        '    or updates periodically (like hourly).')
    parser.add_argument('--dockerized', action='store_true', default=False,
                        help="Agent is running in a docker container.")
    parser.add_argument('--ec2', action='store_true', default=False,
                        help="Load config settings from EC2 user data.")
    parser.add_argument('--gce', action='store_true', default=False,
                        help="Load config settings from GCE user data.")
    parser.add_argument('--alive',
                        help="Watchdog file to update when successfully connected.")
    parser.add_argument('--log',
                        help="Log critical errors to the given file.")
    parser.add_argument('--noidle', action='store_true', default=False,
                        help="Do not wait for system idle at any point.")
    parser.add_argument('--collectversion', action='store_true', default=False,
                        help="Collection browser versions and submit to controller.")
    parser.add_argument('--healthcheckport', type=int, default=8889, help='Run a HTTP health check server on the given port.')
    parser.add_argument('--har', action='store_true', default=False,
                        help="Generate a per-run HAR file as part of the test result (defaults to False).")

    # Video capture/display settings
    parser.add_argument('--xvfb', action='store_true', default=False,
                        help="Use an xvfb virtual display (Linux only).")
    parser.add_argument('--fps', type=int, choices=range(1, 61), default=10,
                        help='Video capture frame rate (defaults to 10). '
                             'Valid range is 1-60 (Linux only).')

    # Server/location configuration
    parser.add_argument('--server',
                        help="URL for WebPageTest work (i.e. http://www.webpagetest.org/work/).")
    parser.add_argument('--validcertificate', action='store_true', default=False,
                        help="Validate server certificates (HTTPS server, defaults to False).")
    parser.add_argument('--location',
                        help="Location ID (as configured in locations.ini on the server).")
    parser.add_argument('--key', help="Location key (optional).")
    parser.add_argument('--polling', type=int, default=5,
                        help='Polling interval for work (defaults to 5 seconds).')
    parser.add_argument('--pubsub',
                        help="PubSub subscription path (i.e. projects/xxx/subscriptions/queue-yyy).")

    # Traffic-shaping options (defaults to host-based)
    parser.add_argument('--shaper', help='Override default traffic shaper. '
                        'Current supported values are:\n'
                        '    none - Disable traffic-shaping (i.e. when root is not available)\n.'
                        '    netem,<interface> - Use NetEm for bridging rndis traffic '
                        '(specify outbound interface).  i.e. --shaper netem,eth0\n'
                        '    remote,<server>,<down pipe>,<up pipe> - Connect to the remote server '
                        'over ssh and use pre-configured dummynet pipes (ssh keys for root user '
                        'should be pre-authorized).')
    parser.add_argument('--tcpdump', help='Specify an interface to use for tcpdump.')

    # Android options
    parser.add_argument('--android', action='store_true', default=False,
                        help="Run tests on an attached android device.")
    parser.add_argument('--device',
                        help="Device ID (only needed if more than one android device attached).")
    parser.add_argument('--simplert',
                        help="Use SimpleRT for reverse-tethering.  The APK should "
                        "be installed manually (adb install simple-rt/simple-rt-1.1.apk) and "
                        "tested once manually (./simple-rt -i eth0 then disconnect and re-connect"
                        " phone) to dismiss any system dialogs.  The ethernet interface and DNS "
                        "server should be passed as options:\n"
                        "    <interface>,<dns1>: i.e. --simplert eth0,8.8.8.8")
    parser.add_argument('--gnirehtet',
                        help="Use gnirehtet for reverse-tethering. You will need to manually "
                        "approve the vpn once per mobile device. Valid options are:\n"
                        "   <interface>,<dns>: i.e. --gnirehtet eth0,8.8.8.8")
    parser.add_argument('--vpntether',
                        help="Use vpn-reverse-tether for reverse-tethering. You will need to manually "
                        "approve the vpn once per mobile device. Valid options are:\n"
                        "   <interface>,<dns>: i.e. --vpntether eth0,8.8.8.8")
    parser.add_argument('--vpntether2',
                        help="Use vpn-reverse-tether v2 for reverse-tethering. This is the "
                        "recommended way to reverse-tether devices. You will need to manually "
                        "approve the vpn once per mobile device. Valid options are:\n"
                        "   <interface>,<dns>: i.e. --vpntether2 eth0,8.8.8.8")
    parser.add_argument('--rndis',
                        help="(deprecated) Enable reverse-tethering over rndis. "
                        "Valid options are:\n"
                        "    dhcp: Configure interface for DHCP\n"
                        "    <ip>/<network>,<gateway>,<dns1>,<dns2>: Static Address.  \n"
                        "        i.e. 192.168.0.8/24,192.168.0.1,8.8.8.8,8.8.4.4")
    parser.add_argument('--ping', type=str, default='8.8.8.8',
                        help="Set custom IP or domain to ping for checking network connection "
                        "when using Android devices. Default is 8.8.8.8")
    parser.add_argument('--temperature', type=int, default=36,
                        help="set custom temperature treshold for device as int")

    # iOS options
    parser.add_argument('--iOS', action='store_true', default=False,
                        help="Run tests on an attached iOS device "
                        "(specify serial number in --device).")
    parser.add_argument('--list', action='store_true', default=False,
                        help="List available iOS devices.")
    parser.add_argument('--ioswebdriver', action='store_true', default=False,
                        help="Use WebDriver for launching the iOS simulator.")

    # Options for authenticating the agent with the server
    parser.add_argument('--username',
                        help="User name if using HTTP Basic auth with WebPageTest server.")
    parser.add_argument('--password',
                        help="Password if using HTTP Basic auth with WebPageTest server.")
    parser.add_argument('--cert', help="Client certificate if using certificates to "
                        "authenticate the WebPageTest server connection.")
    parser.add_argument('--certkey', help="Client-side private key (if not embedded in the cert).")

    # Scheduler configs
    parser.add_argument('--scheduler', help="Scheduler URL (including trailing slash i.e. http://scheduler.webpagetest.org/).")
    parser.add_argument('--schedulersalt', help="Secret salt to use with the scheduler.")
    parser.add_argument('--schedulernode', help="Scheduler node ID for the queue.")

    # CLI test options
    parser.add_argument('--testurl', help="URL to test (CLI).")
    parser.add_argument('--browser', help="Browser to test in (CLI).")
    parser.add_argument('--testspec', help="JSON test definition file (CLI).")
    parser.add_argument('--testoutdir', help="Output directory for test artifacts (CLI).")
    parser.add_argument('--testout', help="Output format (CLI). Valid options are id, url or json")
    parser.add_argument('--testruns', type=int, default=1, help="Number of test runs (CLI - defaults to 1).")
    parser.add_argument('--testrv', action='store_true', default=False, help="Include Repeat View tests (CLI - defaults to False).")
    parser.add_argument('--alertsize', action='store_true', default=False, help="Alerts on large result file size(logging/alerts.log)")
    options, _ = parser.parse_known_args()

    # Make sure we are running python 2.7.11 or newer (required for Windows 8.1)
    if sys.version_info[0] < 3:
        if platform.system() == "Windows":
            if sys.version_info[0] != 2 or \
                    sys.version_info[1] != 7 or \
                    sys.version_info[2] < 11:
                logging.critical("Requires python 2.7.11 (or later)")
                exit(1)
        elif sys.version_info[0] != 2 or sys.version_info[1] != 7:
            logging.critical("Requires python 2.7")
            exit(1)

    if options.list:
        from internal.ios_device import iOSDevice
        ios = iOSDevice()
        devices = ios.get_devices()
        logging.critical("Available iOS devices:")
        for device in devices:
            logging.critical(device)
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

    if options.log:
        err_log = logging.handlers.RotatingFileHandler(options.log, maxBytes=1000000,
                                                       backupCount=5, delay=True)
        err_log.setLevel(logging.ERROR)
        logging.getLogger().addHandler(err_log)

    if options.ec2 or options.gce:
        upgrade_pip_modules()
    elif platform.system() == "Windows":
        # recovery for a busted Windows install
        try:
            import win32api
        except ImportError:
            subprocess.call([sys.executable, '-m', 'pip', 'uninstall', '-y',
                             'pywin32', 'pypiwin32'])
            subprocess.call([sys.executable, '-m', 'pip', 'install', 'pywin32', 'pypiwin32'])

    browsers = None
    if not options.android and not options.iOS:
        browsers = find_browsers(options)
        if len(browsers) == 0:
            logging.critical("No browsers configured. Check that browsers.ini is present and correct.")
            exit(1)

    if options.collectversion and platform.system() == "Windows":
        get_browser_versions(browsers)

    agent = WPTAgent(options, browsers)
    if agent.startup(browsers):
        # Create a work directory relative to where we are running
        logging.critical("Running agent, hit Ctrl+C to exit")
        agent.run_testing()
        logging.critical("Done")
    agent = None


if __name__ == '__main__':
    main()
    # Force a hard exit so unclean threads can't hang the agent
    os._exit(0)
