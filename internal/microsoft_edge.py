# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Microsoft Edge testing"""
from datetime import datetime, timedelta
import glob
import gzip
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import time
if (sys.version_info >= (3, 0)):
    from time import monotonic
    unicode = str
    from urllib.parse import urlsplit # pylint: disable=import-error
    GZIP_TEXT = 'wt'
else:
    from monotonic import monotonic
    from urlparse import urlsplit # pylint: disable=import-error
    GZIP_TEXT = 'w'
try:
    import ujson as json
except BaseException:
    import json
from .desktop_browser import DesktopBrowser
from .optimization_checks import OptimizationChecks

class Edge(DesktopBrowser):
    """Microsoft Edge"""
    def __init__(self, path, options, job):
        DesktopBrowser.__init__(self, path, options, job)
        self.job = job
        self.task = None
        self.options = options
        self.path = path
        self.event_name = None
        self.driver = None
        self.nav_error = None
        self.page_loaded = None
        self.recording = False
        self.browser_version = None
        self.extension_loaded = False
        self.navigating = False
        self.page = {}
        self.requests = {}
        self.request_count = 0
        self.last_activity = None
        self.script_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'js')
        self.wpt_etw_done = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                         'support', 'edge', 'wpt-etw', 'wpt-etw.done')
        self.wpt_etw_proc = None
        self.dns = {}
        self.sockets = {}
        self.socket_ports = {}
        self.requests = {}
        self.pageContexts = []
        self.CMarkup = []
        self.start = None
        self.bodies_path = None
        self.pid = None
        self.supports_interactive = True
        self.start_page = 'http://127.0.0.1:8888/config.html'
        self.edge_registry_path = r"SOFTWARE\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppContainer\Storage\microsoft.microsoftedge_8wekyb3d8bbwe\MicrosoftEdge\Privacy"
        self.edge_registry_key_value = 0
        self.total_sleep = 0
        self.wait_interval = 5.0
        self.wait_for_script = None

    def reset(self):
        """Reset the ETW tracking"""
        self.dns = {}
        self.sockets = {}
        self.socket_ports = {}
        self.requests = {}
        self.request_count = 0
        self.CMarkup = []

    def prepare(self, job, task):
        """Prepare the profile/OS for the browser"""
        if self.must_exit:
            return
        self.kill()
        self.page = {}
        self.requests = {}
        self.bodies_path = os.path.join(task['dir'], 'bodies')
        if not os.path.isdir(self.bodies_path):
            os.makedirs(self.bodies_path)
        try:
            import _winreg # pylint: disable=import-error
            registry_key = _winreg.CreateKeyEx(_winreg.HKEY_CURRENT_USER, self.edge_registry_path, 0, _winreg.KEY_READ | _winreg.KEY_WRITE)
            self.edge_registry_key_value = _winreg.QueryValueEx(registry_key, "ClearBrowsingHistoryOnExit")[0]
            if not task['cached']:
                self.clear_cache()
            if task['cached'] or job['fvonly']:
                _winreg.SetValueEx(registry_key, "ClearBrowsingHistoryOnExit", 0, _winreg.REG_DWORD, 1)
                _winreg.CloseKey(registry_key)
            else:
                _winreg.SetValueEx(registry_key, "ClearBrowsingHistoryOnExit", 0, _winreg.REG_DWORD, 0)
                _winreg.CloseKey(registry_key)
        except Exception as err:
            logging.exception("Error clearing cache: %s", str(err))
        DesktopBrowser.prepare(self, job, task)
        # Prepare the config for the extension to query
        if self.job['message_server'] is not None:
            config = None
            names = ['block',
                     'block_domains',
                     'block_domains_except',
                     'headers',
                     'cookies',
                     'overrideHosts']
            for name in names:
                if name in task and task[name]:
                    if config is None:
                        config = {}
                    config[name] = task[name]
            self.job['message_server'].config = config

    def get_driver(self, task):
        """Get the webdriver instance"""
        from selenium import webdriver # pylint: disable=import-error
        from .os_util import run_elevated
        path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                            'support', 'edge')
        reg_file = os.path.join(path, 'keys.reg')
        if os.path.isfile(reg_file):
            run_elevated('reg', 'IMPORT "{0}"'.format(reg_file))
        capabilities = webdriver.DesiredCapabilities.EDGE.copy()
        extension_src = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                     'support', 'edge', 'extension')
        extension_dir = os.path.join(os.environ.get('LOCALAPPDATA'), 'Packages',
                                     'Microsoft.MicrosoftEdge_8wekyb3d8bbwe',
                                     'LocalState', 'wptagent')
        if not os.path.isdir(extension_dir):
            os.makedirs(extension_dir)
        files = os.listdir(extension_src)
        for file_name in files:
            try:
                src = os.path.join(extension_src, file_name)
                if os.path.isfile(src):
                    shutil.copy(src, extension_dir)
            except Exception:
                logging.exception('Error copying extension')
        capabilities['extensionPaths'] = [extension_dir]
        capabilities['ms:extensionPaths'] = [extension_dir]
        driver = webdriver.Edge(executable_path=self.path, capabilities=capabilities)
        return driver

    def launch(self, job, task):
        """Launch the browser"""
        if self.must_exit:
            return
        if self.job['message_server'] is not None:
            self.job['message_server'].flush_messages()
        try:
            logging.debug('Launching browser : %s', self.path)
            self.driver = self.get_driver(task)
            self.driver.set_page_load_timeout(task['time_limit'])
            if 'browserVersion' in self.driver.capabilities:
                self.browser_version = self.driver.capabilities['browserVersion']
            elif 'version' in self.driver.capabilities:
                self.browser_version = self.driver.capabilities['version']
            DesktopBrowser.wait_for_idle(self)
            self.driver.get(self.start_page)
            logging.debug('Resizing browser to %dx%d', task['width'], task['height'])
            self.driver.set_window_position(0, 0)
            self.driver.set_window_size(task['width'], task['height'])
            # Start the relay agent to capture ETW events
            wpt_etw_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                        'support', 'edge', 'wpt-etw', 'wpt-etw.exe')
            if os.path.isfile(self.wpt_etw_done):
                try:
                    os.remove(self.wpt_etw_done)
                except Exception:
                    pass
            from .os_util import run_elevated
            self.wpt_etw_proc = run_elevated(wpt_etw_path,
                                             '--bodies "{0}"'.format(self.bodies_path),
                                             wait=False)
            self.wait_for_extension()
            if self.extension_loaded:
                # Figure out the native viewport size
                size = self.execute_js("return [window.innerWidth, window.innerHeight];")
                if size is not None and len(size) == 2:
                    task['actual_viewport'] = {"width": size[0], "height": size[1]}
                    if 'adjust_viewport' in job and job['adjust_viewport']:
                        delta_x = max(task['width'] - size[0], 0)
                        delta_y = max(task['height'] - size[1], 0)
                        if delta_x or delta_y:
                            width = task['width'] + delta_x
                            height = task['height'] + delta_y
                            logging.debug('Resizing browser to %dx%d', width, height)
                            self.driver.set_window_size(width, height)
                DesktopBrowser.wait_for_idle(self)
            else:
                task['error'] = 'Error waiting for wpt-etw to start. Make sure .net is installed'
        except Exception as err:
            logging.exception('Error starting browser')
            task['error'] = 'Error starting browser: {0}'.format(err.__str__())

    def stop(self, job, task):
        """Kill the browser"""
        logging.debug("Stopping the browser...")
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                logging.exception('Error quitting webdriver')
            self.driver = None
        DesktopBrowser.stop(self, job, task)
        if self.wpt_etw_proc is not None:
            with open(self.wpt_etw_done, 'a'):
                os.utime(self.wpt_etw_done, None)
            from .os_util import wait_for_elevated_process
            wait_for_elevated_process(self.wpt_etw_proc)
            self.wpt_etw_proc = None
            if os.path.isfile(self.wpt_etw_done):
                try:
                    os.remove(self.wpt_etw_done)
                except Exception:
                    pass
        try:
            import _winreg # pylint: disable=import-error
            registry_key = _winreg.CreateKeyEx(_winreg.HKEY_CURRENT_USER, self.edge_registry_path, 0, _winreg.KEY_WRITE)
            _winreg.SetValueEx(registry_key, "ClearBrowsingHistoryOnExit", 0, _winreg.REG_DWORD, self.edge_registry_key_value)
            _winreg.CloseKey(registry_key)        
        except Exception as err:
            logging.exception("Error resetting Edge cache settings: %s", str(err)) 
        self.kill()
        if self.bodies_path is not None and os.path.isdir(self.bodies_path):
            shutil.rmtree(self.bodies_path, ignore_errors=True)

    def kill(self):
        """Kill any running instances"""
        from .os_util import run_elevated
        processes = ['MicrosoftEdge.exe', 'MicrosoftEdgeCP.exe', 'plugin-container.exe',
                     'browser_broker.exe', 'smartscreen.exe', 'dllhost.exe']
        for exe in processes:
            try:
                run_elevated('taskkill', '/F /T /IM {0}'.format(exe))
            except Exception:
                pass

    def clear_cache(self):
        """Clear the browser cache"""
        appdata = os.environ.get('LOCALAPPDATA')
        edge_dir = os.path.join(appdata, 'Packages', 'Microsoft.MicrosoftEdge_8wekyb3d8bbwe')
        temp_dir = os.path.join(edge_dir, 'AC')
        if os.path.exists(temp_dir):
            for directory in os.listdir(temp_dir):
                if directory.startswith('#!'):
                    try:
                        shutil.rmtree(os.path.join(temp_dir, directory),
                                      ignore_errors=True)
                    except Exception:
                        pass
        cookie_dir = os.path.join(temp_dir, 'MicrosoftEdge', 'Cookies')
        if os.path.exists(cookie_dir):
            try:
                shutil.rmtree(cookie_dir, ignore_errors=True)
            except Exception:
                pass
        app_dir = os.path.join(edge_dir, 'AppData')
        if os.path.exists(app_dir):
            try:
                shutil.rmtree(app_dir, ignore_errors=True)
            except Exception:
                pass

    def run_lighthouse_test(self, task):
        """Stub for lighthouse test"""
        pass

    def run_task(self, task):
        """Run an individual test"""
        if self.driver is not None and self.extension_loaded and not self.must_exit:
            self.task = task
            logging.debug("Running test")
            end_time = monotonic() + task['test_time_limit']
            task['current_step'] = 1
            recording = False
            while len(task['script']) and task['error'] is None and monotonic() < end_time and not self.must_exit:
                self.prepare_task(task)
                command = task['script'].pop(0)
                if not recording and command['record']:
                    recording = True
                    self.on_start_recording(task)
                try:
                    self.process_command(command)
                except Exception:
                    logging.exception("Exception running task")
                if command['record']:
                    self.wait_for_page_load()
                    if not task['combine_steps'] or not len(task['script']):
                        self.on_stop_capture(task)
                        self.on_stop_recording(task)
                        recording = False
                        self.on_start_processing(task)
                        self.wait_for_processing(task)
                        self.step_complete(task)
                        if task['log_data']:
                            # Move on to the next step
                            task['current_step'] += 1
                            self.event_name = None
                    task['navigated'] = True
            # Always navigate to about:blank after finishing in case the tab is
            # remembered across sessions
            try:
                self.driver.get('about:blank')
            except Exception:
                logging.exception('Webdriver exception navigating to about:blank after the test')
            self.task = None

    def wait_for_extension(self):
        """Wait for the extension to send the started message"""
        if self.job['message_server'] is not None:
            end_time = monotonic()  + 30
            while monotonic() < end_time and not self.must_exit:
                try:
                    message = self.job['message_server'].get_message(1)
                    logging.debug(message)
                    logging.debug('Extension started')
                    self.extension_loaded = True
                    break
                except Exception:
                    pass

    def wait_for_page_load(self):
        """Wait for the onload event from the extension"""
        if self.job['message_server'] is not None:
            logging.debug("Waiting for page load...")
            start_time = monotonic()
            end_time = start_time + self.task['time_limit']
            done = False
            self.last_activity = None
            last_wait_interval = start_time
            max_requests = int(self.job['max_requests']) if 'max_requests' in self.job else 0
            while not done and not self.must_exit:
                try:
                    self.process_message(self.job['message_server'].get_message(1))
                except Exception:
                    pass
                now = monotonic()
                elapsed_test = now - start_time
                if 'minimumTestSeconds' in self.task and \
                        elapsed_test < self.task['minimumTestSeconds'] and \
                        now < end_time:
                    continue
                if self.nav_error is not None:
                    done = True
                    if self.page_loaded is None or 'minimumTestSeconds' in self.task:
                        self.task['error'] = self.nav_error
                        self.task['page_data']['result'] = 12999
                    logging.debug("Page load navigation error: %s", self.nav_error)
                elif now >= end_time:
                    done = True
                    logging.debug("Page load reached time limit")
                    # only consider it an error if we didn't get a page load event
                    if self.page_loaded is None:
                        self.task['error'] = "Page Load Timeout"
                        self.task['soft_error'] = True
                        self.task['page_data']['result'] = 99998
                elif max_requests > 0 and self.request_count > max_requests:
                    done = True
                    # only consider it an error if we didn't get a page load event
                    if self.page_loaded is None:
                        self.task['error'] = "Exceeded Maximum Requests"
                        self.task['soft_error'] = True
                        self.task['page_data']['result'] = 99997
                elif self.wait_for_script is not None:
                    elapsed_interval = now - last_wait_interval
                    if elapsed_interval >= self.wait_interval:
                        last_wait_interval = now
                        ret = self.execute_js('return (' + self.wait_for_script + ');')
                        if ret == True:
                            done = True
                elif self.last_activity is not None:
                    elapsed_activity = now - self.last_activity
                    elapsed_page_load = now - self.page_loaded if self.page_loaded else 0
                    if elapsed_page_load >= 1 and elapsed_activity >= self.task['activity_time']:
                        logging.debug("Page Load Activity Time Finished")
                        done = True
                    elif self.task['error'] is not None:
                        logging.debug("Page load error: %s", self.task['error'])
                        done = True

    def process_message(self, message):
        """Process a message from the extension"""
        logging.debug(message)
        if self.recording:
            try:
                if 'Provider' in message and 'Event' in message and \
                        'ts' in message and 'pid' in message:
                    if message['Provider'] == 'Microsoft-IE':
                        if self.pid is None:
                            self.pid = message['pid']
                        if message['pid'] == self.pid:
                            self.process_ie_message(message)
                    elif message['Provider'] == 'Microsoft-Windows-WinINet' and \
                            message['pid'] == self.pid:
                        self.process_wininet_message(message)
                    elif message['Provider'] == 'Microsoft-IEFRAME':
                        if self.pid is None:
                            self.pid = message['pid']
                        if message['pid'] == self.pid:
                            self.process_ieframe_message(message)
            except Exception:
                logging.exception('Error processing message')

    def process_ie_message(self, message):
        """Handle IE trace events"""
        if message['Event'] == 'Mshtml_CWindow_SuperNavigate2/Start':
            self.navigating = True
            self.page_loaded = None
        if self.navigating and message['Event'] == 'Mshtml_CDoc_Navigation' and 'data' in message:
            if 'URL' in message['data'] and \
                    message['data']['URL'].startswith('http') and \
                    message['data']['URL'].startswith('http') and \
                    not message['data']['URL'].startswith('http://127.0.0.1:8888'):
                tid = message['data']['EventContextId']  if 'EventContextId' in message['data'] else  message['tid']
                self.pageContexts.append(tid)
                self.CMarkup.append(message['data']['CMarkup'])
                self.navigating = False
                self.last_activity = monotonic()
                if 'start' not in self.page:
                    logging.debug("Navigation started")
                    self.page['start'] = message['ts']
                if 'url' not in self.page:
                    self.page['url'] = message['data']['URL']
        # Page Navigation events
        if 'start' in self.page and 'data' in message:
            elapsed = message['ts'] - self.page['start']
            if message['Event'] == 'Mshtml_NotifyGoesInteractive/Start' and \
                    'injectScript' in self.job and \
                    'Markup' in message['data'] and \
                    message['data']['Markup'] in self.CMarkup:
                logging.debug("Injecting script: \n%s", self.job['injectScript'])
                self.execute_js(self.job['injectScript'])
            tid = message['data']['EventContextId']  if 'EventContextId' in message['data'] else  message['tid'];
            if  tid in self.pageContexts:
                if message['Event'] == 'Mshtml_WebOCEvents_DocumentComplete':
                    if 'CMarkup' in message['data'] and message['data']['CMarkup'] in self.CMarkup:
                        if 'loadEventStart' not in self.page:
                            self.page['loadEventStart'] = elapsed
                        logging.debug("Page Loaded")
                        self.page_loaded = monotonic()
                if message['Event'] == 'Mshtml_CMarkup_DOMContentLoadedEvent_Start/Start':
                    self.page['domContentLoadedEventStart'] = elapsed
                elif message['Event'] == 'Mshtml_CMarkup_DOMContentLoadedEvent_Stop/Stop':
                    self.page['domContentLoadedEventEnd'] = elapsed
                elif message['Event'] == 'Mshtml_CMarkup_LoadEvent_Start/Start':
                    self.page['loadEventStart'] = elapsed
                elif message['Event'] == 'Mshtml_CMarkup_LoadEvent_Stop/Stop':
                    self.page['loadEventEnd'] = elapsed
                    logging.debug("Page loadEventEnd")
                    self.page_loaded = monotonic()

    def process_ieframe_message(self, message):
        """Handle IEFRAME trace events"""
        if 'start' in self.page and not self.pageContexts:
            elapsed = message['ts'] - self.page['start']
            if message['Event'] == 'Shdocvw_BaseBrowser_DocumentComplete':
                self.page['loadEventStart'] = elapsed
                self.page['loadEventEnd'] = elapsed
                self.page_loaded = monotonic()
                logging.debug("Page loaded (Document Complete)")

    def process_wininet_message(self, message):
        """Handle WinInet trace events"""
        if 'Activity' in message:
            self.last_activity = monotonic()
            self.process_dns_message(message)
            self.process_socket_message(message)
            self.process_request_message(message)

    def process_dns_message(self, message):
        """Handle DNS events"""
        event_id = message['Activity']
        if message['Event'] == 'WININET_DNS_QUERY/Start' and event_id not in self.dns:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            if 'data' in message and 'HostName' in message['data']:
                self.dns[event_id] = {'host': message['data']['HostName']}
        if message['Event'] == 'WININET_DNS_QUERY/Stop' and event_id in self.dns:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            if 'data' in message and 'AddressList' in message['data']:
                self.dns[event_id]['addresses'] = list(
                    filter(None, message['data']['AddressList'].split(';')))
        if message['Event'] == 'Wininet_Getaddrinfo/Start' and event_id in self.dns:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            self.dns[event_id]['start'] = message['ts'] - self.page['start']
        if message['Event'] == 'Wininet_Getaddrinfo/Stop' and event_id in self.dns:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            self.dns[event_id]['end'] = message['ts'] - self.page['start']

    def process_socket_message(self, message):
        """Handle socket connect events"""
        event_id = message['Activity']
        if message['Event'] == 'Wininet_SocketConnect/Start' and event_id not in self.sockets:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            self.sockets[event_id] = {'start': message['ts'] - self.page['start'],
                                      'index': len(self.sockets)}
            if 'data' in message and 'Socket' in message['data']:
                self.sockets[event_id]['socket'] = message['data']['Socket']
            if 'data' in message and 'SourcePort' in message['data']:
                # keep a mapping from the source port to the connection activity id
                self.socket_ports[message['data']['SourcePort']] = event_id
                self.sockets[event_id]['srcPort'] = message['data']['SourcePort']
            if 'data' in message and 'RemoteAddressIndex' in message['data']:
                self.sockets[event_id]['addrIndex'] = message['data']['RemoteAddressIndex']
        if message['Event'] == 'Wininet_SocketConnect/Stop' and event_id in self.sockets:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            self.sockets[event_id]['end'] = message['ts'] - self.page['start']
        if message['Event'] == 'WININET_TCP_CONNECTION/Start' and event_id in self.sockets:
            if 'ServerName' in message['data']:
                self.sockets[event_id]['host'] = message['data']['ServerName']
        if message['Event'] == 'WININET_TCP_CONNECTION/Stop' and event_id in self.sockets:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            if 'end' not in self.sockets[event_id]:
                self.sockets[event_id]['end'] = message['ts'] - self.page['start']
            if 'srcPort' in self.sockets[event_id] and \
                    self.sockets[event_id]['srcPort'] in self.socket_ports:
                del self.socket_ports[self.sockets[event_id]['srcPort']]
        if message['Event'] == 'WININET_TCP_CONNECTION/Fail' and event_id in self.sockets:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            if 'end' not in self.sockets[event_id]:
                self.sockets[event_id]['end'] = message['ts'] - self.page['start']
            if 'data' in message and 'Error' in message['data']:
                self.sockets[event_id]['error'] = message['data']['Error']
        if message['Event'] == 'Wininet_Connect/Stop':
            if 'data' in message and 'Socket' in message['data'] and \
                    message['data']['Socket'] in self.socket_ports:
                connect_id = self.socket_ports[message['data']['Socket']]
                if connect_id in self.sockets:
                    if 'LocalAddress' in message['data']:
                        self.sockets[connect_id]['local'] = message['data']['LocalAddress']
                    if 'RemoteAddress' in message['data']:
                        self.sockets[connect_id]['remote'] = message['data']['RemoteAddress']
        # TLS
        if message['Event'] == 'WININET_HTTPS_NEGOTIATION/Start' and event_id in self.sockets:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            self.sockets[event_id]['tlsStart'] = message['ts'] - self.page['start']
        if message['Event'] == 'WININET_HTTPS_NEGOTIATION/Stop' and event_id in self.sockets:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            self.sockets[event_id]['tlsEnd'] = message['ts'] - self.page['start']

    def process_request_message(self, message):
        """Handle request-level messages"""
        event_id = message['Activity']
        # Request created (not necessarily sent)
        if message['Event'] == 'Wininet_SendRequest/Start':
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            if event_id not in self.requests:
                self.requests[event_id] = {'activity': event_id, 'id': len(self.requests) + 1}
            if 'created' not in self.requests[event_id]:
                self.requests[event_id]['created'] = message['ts'] - self.page['start']
            if 'data' in message and 'AddressName' in message['data'] and \
                    'url' not in self.requests[event_id]:
                self.requests[event_id]['url'] = message['data']['AddressName']
        # Headers and size of outbound request - Length, Headers
        if message['Event'] == 'WININET_REQUEST_HEADER':
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            if event_id not in self.requests:
                self.requests[event_id] = {'activity': event_id, 'id': len(self.requests) + 1}
            if 'created' not in self.requests[event_id]:
                self.requests[event_id]['created'] = message['ts'] - self.page['start']
            if 'data' in message and 'Headers' in message['data']:
                self.requests[event_id]['outHeaders'] = message['data']['Headers']
                self.requests[event_id]['outBytes'] = len(self.requests[event_id]['outHeaders'])
            if 'start' not in self.requests[event_id]:
                self.requests[event_id]['start'] = message['ts'] - self.page['start']
            if 'data' in message and 'Length' in message['data'] and \
                    'outBytes' not in self.requests[event_id]:
                length = int(message['data']['Length'])
                if length > 0:
                    self.requests[event_id]['outBytes'] = length
        # size of outbound request (and actual start) - Size
        if message['Event'] == 'Wininet_SendRequest_Main':
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            if event_id not in self.requests:
                self.requests[event_id] = {'activity': event_id, 'id': len(self.requests) + 1}
            if 'created' not in self.requests[event_id]:
                self.requests[event_id]['created'] = message['ts'] - self.page['start']
            self.requests[event_id]['start'] = message['ts'] - self.page['start']
            if 'data' in message and 'Size' in message['data']:
                length = int(message['data']['Size'])
                if length > 0:
                    self.requests[event_id]['outBytes'] = int(message['data']['Size'])
        # Maps request to source port of connection "Socket" == local port
        if message['Event'] == 'Wininet_LookupConnection/Stop':
            if 'data' in message and 'Socket' in message['data'] and \
                    message['data']['Socket'] in self.socket_ports:
                if event_id not in self.requests:
                    self.requests[event_id] = {'activity': event_id, 'id': len(self.requests) + 1}
                connect_id = self.socket_ports[message['data']['Socket']]
                self.requests[event_id]['connection'] = connect_id
                if connect_id not in self.sockets:
                    self.sockets[connect_id] = {'index': len(self.sockets)}
                if 'requests' not in self.sockets[connect_id]:
                    self.sockets[connect_id]['requests'] = []
                self.sockets[connect_id]['requests'].append(event_id)
        # Headers and size of headers - Length, Headers
        if message['Event'] == 'WININET_RESPONSE_HEADER' and event_id in self.requests:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            self.requests[event_id]['end'] = message['ts'] - self.page['start']
            if 'firstByte' not in self.requests[event_id]:
                self.requests[event_id]['firstByte'] = message['ts'] - self.page['start']
            if 'data' in message and 'Headers' in message['data']:
                self.requests[event_id]['inHeaders'] = message['data']['Headers']
            if 'data' in message and 'Length' in message['data']:
                self.requests[event_id]['inHeadersLen'] = int(message['data']['Length'])
        # inbound bytes (ttfb, keep incrementing end) - Size
        if message['Event'] == 'Wininet_ReadData' and event_id in self.requests:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            if 'start' in self.requests[event_id]:
                self.requests[event_id]['end'] = message['ts'] - self.page['start']
                if 'firstByte' not in self.requests[event_id]:
                    self.requests[event_id]['firstByte'] = message['ts'] - self.page['start']
                if 'data' in message and 'Size' in message['data']:
                    bytesIn = int(message['data']['Size'])
                    if 'inBytes' not in self.requests[event_id]:
                        self.requests[event_id]['inBytes'] = 0
                    self.requests[event_id]['inBytes'] += bytesIn
                    if 'chunks' not in self.requests[event_id]:
                        self.requests[event_id]['chunks'] = []
                    ts = message['ts'] - self.page['start']
                    self.requests[event_id]['chunks'].append( {'ts': ts, 'bytes': bytesIn})
        if message['Event'] == 'WININET_STREAM_DATA_INDICATED' and event_id in self.requests:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            self.requests[event_id]['protocol'] = 'HTTP/2'
            if 'start' in self.requests[event_id]:
                self.requests[event_id]['end'] = message['ts'] - self.page['start']
                if 'firstByte' not in self.requests[event_id]:
                    self.requests[event_id]['firstByte'] = message['ts'] - self.page['start']
                if 'data' in message and 'Size' in message['data']:
                    bytesIn = int(message['data']['Size'])
                    if 'inBytes' not in self.requests[event_id]:
                        self.requests[event_id]['inBytes'] = 0
                    self.requests[event_id]['inBytes'] += bytesIn
                    if 'chunks' not in self.requests[event_id]:
                        self.requests[event_id]['chunks'] = []
                    ts = message['ts'] - self.page['start']
                    self.requests[event_id]['chunks'].append( {'ts': ts, 'bytes': bytesIn})
        # completely finished
        if message['Event'] == 'Wininet_UsageLogRequest' and \
                event_id in self.requests and 'data' in message:
            if 'URL' in message['data']:
                self.requests[event_id]['url'] = message['data']['URL']
            if 'Verb' in message['data']:
                self.requests[event_id]['verb'] = message['data']['Verb']
            if 'Status' in message['data']:
                self.requests[event_id]['status'] = message['data']['Status']
            if 'RequestHeaders' in message['data']:
                self.requests[event_id]['outHeaders'] = message['data']['RequestHeaders']
            if 'ResponseHeaders' in message['data']:
                self.requests[event_id]['inHeaders'] = message['data']['ResponseHeaders']
        # Headers done - Direction changing for capture (no params)
        if message['Event'] == 'Wininet_SendRequest/Stop' and event_id in self.requests:
            if 'start' not in self.page:
                self.page['start'] = message['ts']
            if 'end' not in self.requests[event_id]:
                self.requests[event_id]['end'] = message['ts'] - self.page['start']
            self.request_count += 1

    def execute_js(self, script):
        """Run javascipt"""
        if self.must_exit:
            return
        ret = None
        if self.driver is not None:
            try:
                self.driver.set_script_timeout(30)
                ret = self.driver.execute_script(script)
            except Exception:
                logging.exception('Error executing script')
        return ret

    def run_js_file(self, file_name):
        """Execute one of our js scripts"""
        if self.must_exit:
            return
        ret = None
        script = None
        script_file_path = os.path.join(self.script_dir, file_name)
        if os.path.isfile(script_file_path):
            with open(script_file_path, 'r') as script_file:
                script = script_file.read()
        if script is not None:
            try:
                self.driver.set_script_timeout(30)
                ret = self.driver.execute_script('return ' + script)
            except Exception:
                logging.exception('Error executing script file')
            if ret is not None:
                logging.debug(ret)
        return ret

    def get_sorted_requests_json(self, include_bodies):
        return 'null'

    def collect_browser_metrics(self, task):
        """Collect all of the in-page browser metrics that we need"""
        if self.must_exit:
            return
        # Trigger a message to start writing the interactive periods asynchronously
        if self.supports_interactive:
            self.execute_js('window.postMessage({ wptagent: "GetInteractivePeriods"}, "*");')
        if 'customMetrics' in self.job:
            self.driver.set_script_timeout(30)
            custom_metrics = {}
            requests = None
            bodies = None
            for name in sorted(self.job['customMetrics']):
                logging.debug("Collecting custom metric %s", name)
                custom_script = unicode(self.job['customMetrics'][name])
                if custom_script.find('$WPT_TEST_URL') >= 0:
                    wpt_url = 'window.location.href'
                    if 'page_data' in self.task and 'URL' in self.task['page_data']:
                        wpt_url = '{}'.format(json.dumps(self.task['page_data']['URL']))
                    elif 'url' in self.job:
                        wpt_url = '{}'.format(json.dumps(self.job['URL']))
                    try:
                        custom_script = custom_script.replace('$WPT_TEST_URL', wpt_url)
                    except Exception:
                        logging.exception('Error substituting URL data into custom script')
                if custom_script.find('$WPT_REQUESTS') >= 0:
                    if requests is None:
                        requests = self.get_sorted_requests_json(False)
                    try:
                        custom_script = custom_script.replace('$WPT_REQUESTS', requests)
                    except Exception:
                        logging.exception('Error substituting request data into custom script')
                if custom_script.find('$WPT_BODIES') >= 0:
                    if bodies is None:
                        bodies = self.get_sorted_requests_json(True)
                    try:
                        custom_script = custom_script.replace('$WPT_BODIES', bodies)
                    except Exception:
                        logging.exception('Error substituting request data with bodies into custom script')
                script = 'var wptCustomMetric = function() {' + custom_script + '};try{return wptCustomMetric();}catch(e){};'
                try:
                    custom_metrics[name] = self.driver.execute_script(script)
                    if custom_metrics[name] is not None:
                        logging.debug(custom_metrics[name])
                except Exception:
                    logging.exception('Error collecting custom metric')
            path = os.path.join(task['dir'], task['prefix'] + '_metrics.json.gz')
            with gzip.open(path, GZIP_TEXT, 7) as outfile:
                outfile.write(json.dumps(custom_metrics))
        # Collect the regular browser metrics
        logging.debug("Collecting user timing metrics")
        user_timing = self.run_js_file('user_timing.js')
        if user_timing is not None:
            path = os.path.join(task['dir'], task['prefix'] + '_timed_events.json.gz')
            with gzip.open(path, GZIP_TEXT, 7) as outfile:
                outfile.write(json.dumps(user_timing))
        logging.debug("Collecting page-level metrics")
        page_data = self.run_js_file('page_data.js')
        if page_data is not None:
            task['page_data'].update(page_data)
        # Wait for the interactive periods to be written
        if self.supports_interactive:
            end_time = monotonic() + 10
            interactive = None
            while interactive is None and monotonic() < end_time:
                interactive = self.execute_js(
                    'return document.getElementById("wptagentLongTasks").innerText;')
                if interactive is None:
                    time.sleep(0.2)
            if interactive is not None and len(interactive):
                interactive_file = os.path.join(task['dir'],
                                                task['prefix'] + '_interactive.json.gz')
                with gzip.open(interactive_file, GZIP_TEXT, 7) as f_out:
                    f_out.write(interactive)

    def prepare_task(self, task):
        """Format the file prefixes for multi-step testing"""
        if task['current_step'] == 1:
            task['prefix'] = task['task_prefix']
            task['video_subdirectory'] = task['task_video_prefix']
        else:
            task['prefix'] = '{0}_{1:d}'.format(task['task_prefix'], task['current_step'])
            task['video_subdirectory'] = '{0}_{1:d}'.format(task['task_video_prefix'],
                                                            task['current_step'])
        if task['video_subdirectory'] not in task['video_directories']:
            task['video_directories'].append(task['video_subdirectory'])
        if self.event_name is not None:
            task['step_name'] = self.event_name
        else:
            task['step_name'] = 'Step_{0:d}'.format(task['current_step'])
        if 'steps' not in task:
            task['steps'] = []
        task['steps'].append({
            'prefix': str(task['prefix']),
            'video_subdirectory': str(task['video_subdirectory']),
            'step_name': str(task['step_name']),
            'start_time': time.time(),
            'num': int(task['current_step'])
        })

    def on_start_recording(self, task):
        """Notification that we are about to start an operation that needs to be recorded"""
        # Clear the state
        self.page = {}
        self.requests = {}
        self.reset()
        task['page_data'] = {'date': time.time()}
        task['page_result'] = None
        task['run_start_time'] = monotonic()
        if self.job['message_server'] is not None:
            self.job['message_server'].flush_messages()
        if self.browser_version is not None and 'browserVersion' not in task['page_data']:
            task['page_data']['browserVersion'] = self.browser_version
            task['page_data']['browser_version'] = self.browser_version
        self.recording = True
        self.navigating = True
        now = monotonic()
        if self.page_loaded is not None:
            self.page_loaded = now
        DesktopBrowser.on_start_recording(self, task)
        logging.debug('Starting measurement')
        task['start_time'] = datetime.utcnow()

    def on_stop_capture(self, task):
        """Do any quick work to stop things that are capturing data"""
        DesktopBrowser.on_stop_capture(self, task)

    def on_stop_recording(self, task):
        """Notification that we are done with recording"""
        self.recording = False
        DesktopBrowser.on_stop_recording(self, task)
        if self.job['pngScreenShot']:
            screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.png')
            self.grab_screenshot(screen_shot, png=True)
        else:
            screen_shot = os.path.join(task['dir'], task['prefix'] + '_screen.jpg')
            self.grab_screenshot(screen_shot, png=False, resize=600)
        # Collect end of test data from the browser
        self.collect_browser_metrics(task)

    def on_start_processing(self, task):
        """Start any processing of the captured data"""
        DesktopBrowser.on_start_processing(self, task)
        self.process_requests(task)

    def wait_for_processing(self, task):
        """Wait for any background processing threads to finish"""
        DesktopBrowser.wait_for_processing(self, task)

    def process_command(self, command):
        """Process an individual script command"""
        logging.debug("Processing script command:")
        logging.debug(command)
        if command['command'] == 'navigate':
            self.task['page_data']['URL'] = command['target']
            self.task['url'] = command['target']
            url = str(command['target']).replace('"', '\"')
            script = 'window.location="{0}";'.format(url)
            script = self.prepare_script_for_record(script)
            try:
                self.driver.set_script_timeout(30)
                self.driver.execute_script(script)
            except Exception:
                logging.exception('Error navigating')
            self.page_loaded = None
        elif command['command'] == 'logdata':
            self.task['combine_steps'] = False
            if int(re.search(r'\d+', str(command['target'])).group()):
                logging.debug("Data logging enabled")
                self.task['log_data'] = True
            else:
                logging.debug("Data logging disabled")
                self.task['log_data'] = False
        elif command['command'] == 'combinesteps':
            self.task['log_data'] = True
            self.task['combine_steps'] = True
        elif command['command'] == 'seteventname':
            self.event_name = command['target']
        elif command['command'] == 'exec':
            script = command['target']
            if command['record']:
                script = self.prepare_script_for_record(script)
            try:
                self.driver.set_script_timeout(30)
                self.driver.execute_script(script)
            except Exception:
                logging.exception('Error executing script command')
        elif command['command'] == 'sleep':
            available_sleep = 60 - self.total_sleep
            delay = min(available_sleep, max(0, int(re.search(r'\d+', str(command['target'])).group())))
            if delay > 0:
                self.total_sleep += delay
                time.sleep(delay)
        elif command['command'] == 'setabm':
            self.task['stop_at_onload'] = \
                bool('target' in command and int(re.search(r'\d+',
                                                           str(command['target'])).group()) == 0)
        elif command['command'] == 'setactivitytimeout':
            if 'target' in command:
                milliseconds = int(re.search(r'\d+', str(command['target'])).group())
                self.task['activity_time'] = max(0, min(30, float(milliseconds) / 1000.0))
        elif command['command'] == 'setminimumstepseconds':
            self.task['minimumTestSeconds'] = int(re.search(r'\d+', str(command['target'])).group())
        elif command['command'] == 'setuseragent':
            self.job['user_agent_string'] = command['target']
        elif command['command'] == 'setcookie':
            if 'target' in command and 'value' in command:
                try:
                    url = command['target'].strip()
                    cookie = command['value']
                    pos = cookie.find(';')
                    if pos > 0:
                        cookie = cookie[:pos]
                    pos = cookie.find('=')
                    if pos > 0:
                        name = cookie[:pos].strip()
                        value = cookie[pos+1:].strip()
                        if len(name) and len(value) and len(url):
                            try:
                                self.driver.add_cookie({'url': url, 'name': name, 'value': value})
                            except Exception:
                                logging.exception('Error adding cookie')
                            try:
                                import win32inet # pylint: disable=import-error
                                cookie_string = cookie
                                if cookie.find('xpires') == -1:
                                    expires = datetime.utcnow() + timedelta(days=30)
                                    expires_string = expires.strftime("%a, %d %b %Y %H:%M:%S GMT")
                                    cookie_string += '; expires={0}'.format(expires_string)
                                logging.debug("Setting cookie: %s", cookie_string)
                                win32inet.InternetSetCookie(url, None, cookie_string)
                            except Exception as err:
                                logging.exception("Error setting cookie: %s", str(err))
                except Exception:
                    logging.exception('Error setting cookie')
        elif command['command'] == 'waitfor':
            try:
                self.wait_for_script = command['target'] if command['target'] else None
            except Exception:
                logging.exception('Error processing waitfor command')
        elif command['command'] == 'waitinterval':
            try:
                interval = float(command['target'])
                if interval > 0:
                    self.wait_interval = interval
            except Exception:
                logging.exception('Error processing waitfor command')

    def navigate(self, url):
        """Navigate to the given URL"""
        if self.driver is not None:
            try:
                self.driver.get(url)
            except Exception as err:
                logging.exception("Error navigating Edge: %s", str(err))

    def grab_screenshot(self, path, png=True, resize=0):
        """Save the screen shot (png or jpeg)"""
        if self.driver is not None and not self.must_exit:
            try:
                data = self.driver.get_screenshot_as_png()
                if data is not None:
                    resize_string = '' if not resize else '-resize {0:d}x{0:d} '.format(resize)
                    if png:
                        with open(path, 'wb') as image_file:
                            image_file.write(data)
                        if len(resize_string):
                            cmd = '{0} -format png -define png:color-type=2 '\
                                  '-depth 8 {1}"{2}"'.format(self.job['image_magick']['mogrify'],
                                                             resize_string, path)
                            logging.debug(cmd)
                            subprocess.call(cmd, shell=True)
                    else:
                        tmp_file = path + '.png'
                        with open(tmp_file, 'wb') as image_file:
                            image_file.write(data)
                        command = '{0} "{1}" {2}-quality {3:d} "{4}"'.format(
                            self.job['image_magick']['convert'],
                            tmp_file, resize_string, self.job['imageQuality'], path)
                        logging.debug(command)
                        subprocess.call(command, shell=True)
                        if os.path.isfile(tmp_file):
                            try:
                                os.remove(tmp_file)
                            except Exception:
                                pass
            except Exception as err:
                logging.exception('Exception grabbing screen shot: %s', str(err))

    def process_requests(self, task):
        """Convert all of the request and page events into the format needed for WPT"""
        result = {}
        self.process_sockets()
        result['requests'] = self.process_raw_requests()
        result['pageData'] = self.calculate_page_stats(result['requests'])
        if 'metadata' in self.job:
            result['pageData']['metadata'] = self.job['metadata']
        self.check_optimization(task, result['requests'], result['pageData'])
        devtools_file = os.path.join(task['dir'], task['prefix'] + '_devtools_requests.json.gz')
        with gzip.open(devtools_file, GZIP_TEXT, 7) as f_out:
            json.dump(result, f_out)

    def process_sockets(self):
        """Map/claim the DNS and socket-connection level details"""
        # Fill in the host and address for any sockets that had a DNS entry
        # (even if the DNS did not require a lookup)
        for event_id in self.sockets:
            if event_id in self.dns:
                if 'host' not in self.sockets[event_id] and 'host' in self.dns[event_id]:
                    self.sockets[event_id]['host'] = self.dns[event_id]['host']
                if 'addresses' in self.dns[event_id]:
                    self.sockets[event_id]['addresses'] = self.dns[event_id]['addresses']
                    if 'addrIndex' in self.sockets[event_id]:
                        index = self.sockets[event_id]['addrIndex']
                        if index < len(self.dns[event_id]['addresses']):
                            self.sockets[event_id]['address'] = \
                                    self.dns[event_id]['addresses'][index]
        # Copy over the connect and dns timings to the first request on a given
        # socket.
        for event_id in self.sockets:
            try:
                if 'requests' in self.sockets[event_id]:
                    first_request = None
                    first_request_time = None
                    count = len(self.sockets[event_id]['requests'])
                    for i in range(0, count):
                        rid = self.sockets[event_id]['requests'][i]
                        if rid in self.requests and 'start' in self.requests[rid]:
                            if first_request is None or \
                                    self.requests[rid]['start'] < first_request_time:
                                first_request = rid
                                first_request_time = self.requests[rid]['start']
                    if first_request is not None:
                        if 'start' in self.sockets[event_id]:
                            self.requests[first_request]['connectStart'] = \
                                self.sockets[event_id]['start']
                            if 'end' in self.sockets[event_id]:
                                self.requests[first_request]['connectEnd'] = \
                                    self.sockets[event_id]['end']
                        if 'tlsStart' in self.sockets[event_id]:
                            self.requests[first_request]['tlsStart'] = \
                                self.sockets[event_id]['tlsStart']
                            if 'tlsEnd' in self.sockets[event_id]:
                                self.requests[first_request]['tlsEnd'] = \
                                    self.sockets[event_id]['tlsEnd']
                        if event_id in self.dns:
                            if 'start' in self.dns[event_id]:
                                self.requests[first_request]['dnsStart'] = \
                                    self.dns[event_id]['start']
                                if 'end' in self.dns[event_id]:
                                    self.requests[first_request]['dnsEnd'] = \
                                        self.dns[event_id]['end']
            except Exception:
                logging.exception('Error processing request timings')

    def get_empty_request(self, request_id, url):
        """Return and empty, initialized request"""
        parts = urlsplit(url)
        request = {'type': 3,
                   'id': request_id,
                   'request_id': request_id,
                   'ip_addr': '',
                   'full_url': url,
                   'is_secure': 1 if parts.scheme == 'https' else 0,
                   'method': '',
                   'host': parts.netloc,
                   'url': parts.path,
                   'responseCode': -1,
                   'load_start': -1,
                   'load_ms': -1,
                   'ttfb_ms': -1,
                   'dns_start': -1,
                   'dns_end': -1,
                   'dns_ms': -1,
                   'connect_start': -1,
                   'connect_end': -1,
                   'connect_ms': -1,
                   'ssl_start': -1,
                   'ssl_end': -1,
                   'ssl_ms': -1,
                   'bytesIn': 0,
                   'bytesOut': 0,
                   'objectSize': 0,
                   'initiator': '',
                   'initiator_line': '',
                   'initiator_column': '',
                   'server_rtt': None,
                   'headers': {'request': [], 'response': []},
                   'score_cache': -1,
                   'score_cdn': -1,
                   'score_gzip': -1,
                   'score_cookies': -1,
                   'score_keep-alive': -1,
                   'score_minify': -1,
                   'score_combine': -1,
                   'score_compress': -1,
                   'score_etags': -1,
                   'gzip_total': None,
                   'gzip_save': None,
                   'minify_total': None,
                   'minify_save': None,
                   'image_total': None,
                   'image_save': None,
                   'cache_time': None,
                   'cdn_provider': None,
                   'server_count': None,
                   'socket': -1
                  }
        if len(parts.query):
            request['url'] += '?' + parts.query
        return request

    def get_header_value(self, headers, name):
        """Return the value for the given header"""
        value = ''
        name = name.lower()
        for header in headers:
            pos = header.find(':')
            if pos > 0:
                key = header[0:pos].lower()
                if key.startswith(name):
                    val = header[pos + 1:].strip()
                    if len(value):
                        value += '; '
                    value += val
        return value

    def process_raw_requests(self):
        """Convert the requests into the format WPT is expecting"""
        import zipfile
        requests = []
        bodies_zip_file = None
        body_index = 0
        if 'bodies' in self.job and self.job['bodies']:
            bodies_zip_path = os.path.join(self.task['dir'], \
                                           self.task['prefix'] + '_bodies.zip')
            bodies_zip_file = zipfile.ZipFile(bodies_zip_path, 'w', zipfile.ZIP_DEFLATED)
        for req_id in self.requests:
            try:
                req = self.requests[req_id]
                if 'start' in req and 'url' in req and \
                        not req['url'].startswith("https://www.bing.com/cortanaassist/gping"):
                    request = self.get_empty_request(req['id'], req['url'])
                    if 'verb' in req:
                        request['method'] = req['verb']
                    if 'status' in req:
                        request['responseCode'] = req['status']
                        request['status'] = req['status']
                    if 'protocol' in req:
                        request['protocol'] = req['protocol']
                    if 'created' in req:
                        request['created'] = req['created']
                    if 'start' in req:
                        request['load_start'] = int(round(req['start']))
                    if 'firstByte' in req:
                        ttfb = int(round(req['firstByte'] - req['start']))
                        request['ttfb_ms'] = max(0, ttfb)
                    if 'end' in req:
                        load_time = int(round(req['end'] - req['start']))
                        request['load_ms'] = max(0, load_time)
                    if 'dnsStart' in req:
                        request['dns_start'] = int(round(req['dnsStart']))
                    if 'dnsEnd' in req:
                        request['dns_end'] = int(round(req['dnsEnd']))
                    if 'connectStart' in req:
                        request['connect_start'] = int(round(req['connectStart']))
                    if 'connectEnd' in req:
                        request['connect_end'] = int(round(req['connectEnd']))
                    if 'tlsStart' in req:
                        request['ssl_start'] = int(round(req['tlsStart']))
                    if 'tlsEnd' in req:
                        request['ssl_end'] = int(round(req['tlsEnd']))
                    if 'inBytes' in req:
                        request['bytesIn'] = req['inBytes']
                        request['objectSize'] = req['inBytes']
                    if 'chunks' in req:
                        request['chunks'] = req['chunks']
                    if 'outBytes' in req:
                        request['bytesOut'] = req['outBytes']
                    if 'connection' in req:
                        connect_id = req['connection']
                        if connect_id not in self.sockets:
                            self.sockets[connect_id] = {'index': len(self.sockets)}
                        request['socket'] = self.sockets[connect_id]['index']
                        if 'address' in self.sockets[connect_id]:
                            request['ip_addr'] = self.sockets[connect_id]['address']
                    # Process the headers
                    if 'outHeaders' in req:
                        for header in req['outHeaders'].splitlines():
                            if len(header):
                                request['headers']['request'].append(header)
                        # key: value format for the optimization checks
                        request['request_headers'] = {}
                        for header in request['headers']['request']:
                            split_pos = header.find(":", 1)
                            if split_pos > 1:
                                name = header[:split_pos].strip()
                                value = header[split_pos + 1:].strip()
                                if len(name) and len(value):
                                    if name in request['request_headers']:
                                        request['request_headers'][name] += "\r\n" + value
                                    else:
                                        request['request_headers'][name] = value
                    if 'inHeaders' in req:
                        for header in req['inHeaders'].splitlines():
                            if len(header):
                                request['headers']['response'].append(header)
                        # key: value format for the optimization checks
                        request['response_headers'] = {}
                        for header in request['headers']['response']:
                            split_pos = header.find(":", 1)
                            if split_pos > 1:
                                name = header[:split_pos].strip()
                                value = header[split_pos + 1:].strip()
                                if len(name) and len(value):
                                    if name in request['response_headers']:
                                        request['response_headers'][name] += "\r\n" + value
                                    else:
                                        request['response_headers'][name] = value
                    value = self.get_header_value(request['headers']['response'], 'Expires')
                    if value:
                        request['expires'] = value
                    value = self.get_header_value(request['headers']['response'], 'Cache-Control')
                    if value:
                        request['cacheControl'] = value
                    value = self.get_header_value(request['headers']['response'], 'Content-Type')
                    if value:
                        request['contentType'] = value
                    value = self.get_header_value(request['headers']['response'],
                                                  'Content-Encoding')
                    if value:
                        request['contentEncoding'] = value
                    value = self.get_header_value(request['headers']['response'], 'Content-Length')
                    if value:
                        if 'objectSize' not in request or value < request['objectSize']:
                            request['objectSize'] = value
                    # process the response body
                    body_file = os.path.join(self.bodies_path, req_id)
                    if os.path.isfile(body_file):
                        request['body'] = body_file
                        request['objectSizeUncompressed'] = os.path.getsize(body_file)
                        is_text = False
                        if 'contentType' in request and 'responseCode' in request and \
                                request['responseCode'] == 200:
                            if request['contentType'].startswith('text/') or \
                                    request['contentType'].find('javascript') >= 0 or \
                                    request['contentType'].find('json') >= 0 or \
                                    request['contentType'].find('xml') >= 0 or\
                                    request['contentType'].find('/svg') >= 0:
                                is_text = True
                        if bodies_zip_file is not None and is_text:
                            body_index += 1
                            name = '{0:03d}-{1}-body.txt'.format(body_index, request['id'])
                            bodies_zip_file.write(body_file, name)
                            request['body_id'] = request['id']
                            logging.debug('%s: Stored body in zip for (%s)',
                                          request['id'], request['url'])
                    requests.append(request)
            except Exception:
                logging.exception('Error processing request')
        if bodies_zip_file is not None:
            bodies_zip_file.close()
        # Strip the headers if necessary
        noheaders = False
        if 'noheaders' in self.job and self.job['noheaders']:
            noheaders = True
        if noheaders:
            for request in requests:
                if 'headers' in request:
                    del request['headers']
        requests.sort(key=lambda x: x['load_start'])
        return requests

    def check_optimization(self, task, requests, page_data):
        """Run the optimization checks"""
        if self.must_exit:
            return
        # build an dictionary of the requests
        opt_requests = {}
        for request in requests:
            opt_requests[request['id']] = request

        optimization = OptimizationChecks(self.job, task, opt_requests)
        optimization.start()
        optimization.join()

        # remove the temporary entries we added
        for request in requests:
            if 'response_headers' in request:
                del request['response_headers']
            if 'request_headers' in request:
                del request['request_headers']
            if 'body' in request:
                del request['body']
            if 'response_body' in request:
                del request['response_body']

        # merge the optimization results
        optimization_file = os.path.join(self.task['dir'], self.task['prefix']) + \
                            '_optimization.json.gz'
        if os.path.isfile(optimization_file):
            with gzip.open(optimization_file, 'r') as f_in:
                optimization_results = json.load(f_in)
            page_data['score_cache'] = -1
            page_data['score_cdn'] = -1
            page_data['score_gzip'] = -1
            page_data['score_cookies'] = -1
            page_data['score_keep-alive'] = -1
            page_data['score_minify'] = -1
            page_data['score_combine'] = -1
            page_data['score_compress'] = -1
            page_data['score_etags'] = -1
            page_data['score_progressive_jpeg'] = -1
            page_data['gzip_total'] = 0
            page_data['gzip_savings'] = 0
            page_data['minify_total'] = -1
            page_data['minify_savings'] = -1
            page_data['image_total'] = 0
            page_data['image_savings'] = 0
            page_data['optimization_checked'] = 1
            page_data['base_page_cdn'] = ''
            cache_count = 0
            cache_total = 0
            cdn_count = 0
            cdn_total = 0
            keep_alive_count = 0
            keep_alive_total = 0
            progressive_total_bytes = 0
            progressive_bytes = 0
            for request in requests:
                if request['responseCode'] == 200:
                    request_id = str(request['id'])
                    if request_id in optimization_results:
                        opt = optimization_results[request_id]
                        if 'cache' in opt:
                            request['score_cache'] = opt['cache']['score']
                            request['cache_time'] = opt['cache']['time']
                            if request['score_cache'] >= 0:
                                cache_count += 1
                                cache_total += request['score_cache']
                        if 'cdn' in opt:
                            request['score_cdn'] = opt['cdn']['score']
                            request['cdn_provider'] = opt['cdn']['provider']
                            if request['score_cdn'] >= 0:
                                cdn_count += 1
                                cdn_total += request['score_cdn']
                            if 'is_base_page' in request and request['is_base_page'] and \
                                    request['cdn_provider'] is not None:
                                page_data['base_page_cdn'] = request['cdn_provider']
                        if 'keep_alive' in opt:
                            request['score_keep-alive'] = opt['keep_alive']['score']
                            if request['score_keep-alive'] >= 0:
                                keep_alive_count += 1
                                keep_alive_total += request['score_keep-alive']
                        if 'gzip' in opt:
                            savings = opt['gzip']['size'] - opt['gzip']['target_size']
                            request['score_gzip'] = opt['gzip']['score']
                            request['gzip_total'] = opt['gzip']['size']
                            request['gzip_save'] = savings
                            if request['score_gzip']  >= 0:
                                page_data['gzip_total'] += opt['gzip']['size']
                                page_data['gzip_savings'] += savings
                        if 'image' in opt:
                            savings = opt['image']['size'] - opt['image']['target_size']
                            request['score_compress'] = opt['image']['score']
                            request['image_total'] = opt['image']['size']
                            request['image_save'] = savings
                            if request['score_compress'] >= 0:
                                page_data['image_total'] += opt['image']['size']
                                page_data['image_savings'] += savings
                        if 'progressive' in opt:
                            size = opt['progressive']['size']
                            request['jpeg_scan_count'] = opt['progressive']['scan_count']
                            progressive_total_bytes += size
                            if request['jpeg_scan_count'] > 1:
                                request['score_progressive_jpeg'] = 100
                                progressive_bytes += size
                            elif size < 10240:
                                request['score_progressive_jpeg'] = 50
                            else:
                                request['score_progressive_jpeg'] = 0
            if cache_count > 0:
                page_data['score_cache'] = int(round(cache_total / cache_count))
            if cdn_count > 0:
                page_data['score_cdn'] = int(round(cdn_total / cdn_count))
            if keep_alive_count > 0:
                page_data['score_keep-alive'] = int(round(keep_alive_total / keep_alive_count))
            if page_data['gzip_total'] > 0:
                page_data['score_gzip'] = 100 - int(page_data['gzip_savings'] * 100 /
                                                    page_data['gzip_total'])
            if page_data['image_total'] > 0:
                page_data['score_compress'] = 100 - int(page_data['image_savings'] * 100 /
                                                        page_data['image_total'])
            if progressive_total_bytes > 0:
                page_data['score_progressive_jpeg'] = int(round(progressive_bytes * 100 /
                                                                progressive_total_bytes))

    def calculate_page_stats(self, requests):
        """Calculate the page-level stats"""
        if self.must_exit:
            return
        page = {'loadTime': 0,
                'docTime': 0,
                'fullyLoaded': 0,
                'bytesOut': 0,
                'bytesOutDoc': 0,
                'bytesIn': 0,
                'bytesInDoc': 0,
                'requests': len(requests),
                'requestsDoc': 0,
                'responses_200': 0,
                'responses_404': 0,
                'responses_other': 0,
                'result': 0,
                'testStartOffset': 0,
                'cached': 1 if self.task['cached'] else 0,
                'optimization_checked': 0,
                'connections': 0
               }
        if 'loadEventStart' in self.page:
            page['loadTime'] = int(round(self.page['loadEventStart']))
            page['docTime'] = page['loadTime']
            page['fullyLoaded'] = page['loadTime']
            page['loadEventStart'] = page['loadTime']
            page['loadEventEnd'] = page['loadTime']
            if 'loadEventEnd' in self.page:
                page['loadEventEnd'] = int(round(self.page['loadEventEnd']))
        if 'domContentLoadedEventStart' in self.page:
            page['domContentLoadedEventStart'] = int(round(self.page['domContentLoadedEventStart']))
            page['domContentLoadedEventEnd'] = page['domContentLoadedEventStart']
            if 'domContentLoadedEventEnd' in self.page:
                page['domContentLoadedEventEnd'] = int(round(self.page['domContentLoadedEventEnd']))

        connections = {}
        main_request = None
        index = 0
        for request in requests:
            if 'socket' in request and request['socket'] not in connections:
                connections[request['socket']] = request['id']
            if request['load_ms'] >= 0:
                end_time = request['load_start'] + request['load_ms']
                if end_time > page['fullyLoaded']:
                    page['fullyLoaded'] = end_time
                if end_time <= page['loadTime']:
                    page['requestsDoc'] += 1
                    page['bytesInDoc'] += request['bytesIn']
                    page['bytesOutDoc'] += request['bytesOut']
            page['bytesIn'] += request['bytesIn']
            page['bytesOut'] += request['bytesOut']
            if request['responseCode'] == 200:
                page['responses_200'] += 1
            elif request['responseCode'] == 404:
                page['responses_404'] += 1
                page['result'] = 99999
            elif request['responseCode'] > -1:
                page['responses_other'] += 1
            if main_request is None and \
                    (request['responseCode'] == 200 or \
                     request['responseCode'] == 304 or \
                     request['responseCode'] >= 400):
                main_request = request['id']
                request['is_base_page'] = True
                page['final_base_page_request'] = index
                page['final_base_page_request_id'] = main_request
                page['final_url'] = request['full_url']
                if 'URL' not in self.task['page_data']:
                    self.task['page_data']['URL'] = page['final_url']
                if request['ttfb_ms'] >= 0:
                    page['TTFB'] = request['load_start'] + request['ttfb_ms']
                if request['ssl_end'] >= request['ssl_start'] and \
                        request['ssl_start'] >= 0:
                    page['basePageSSLTime'] = int(round(request['ssl_end'] - \
                                                        request['ssl_start']))
                if request['responseCode'] >= 400:
                    page['result'] = request['responseCode']
        if (page['result'] == 0 or page['result'] == 99999) and \
                page['responses_200'] == 0 and len(requests):
            if 'responseCode' in requests[0]:
                page['result'] = requests[0]['responseCode']
            else:
                page['result'] = 12999
        page['connections'] = len(connections)
        self.task['page_result'] = page['result']
        return page
