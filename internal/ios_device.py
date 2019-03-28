# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Interface for iWptBrowser on iOS devices"""
import base64
import logging
import multiprocessing
import os
import platform
import select
import shutil
import subprocess
import threading
import monotonic
import ujson as json


class iOSDevice(object):
    """iOS device interface"""
    def __init__(self, serial=None):
        self.socket = None
        self.serial = serial
        self.must_disconnect = False
        self.mux = None
        self.message_thread = None
        self.messages = multiprocessing.JoinableQueue()
        self.notification_queue = None
        self.current_id = 0
        self.video_file = None
        self.last_video_data = None
        self.video_size = 0
        self.last_restart = monotonic.monotonic()

    def check_install(self):
        """Check to make sure usbmux is installed and the device is available"""
        ret = False
        plat = platform.system()
        if plat == "Darwin" or plat == "Linux":
            if not os.path.exists('/var/run/usbmuxd') and plat == "Linux":
                args = ['sudo', 'python', __file__, '--install']
                subprocess.call(args)
            if os.path.exists('/var/run/usbmuxd'):
                ret = True
            else:
                print "usbmuxd is not available, please try installing it manually"
        else:
            print "iOS is only supported on Mac and Linux"
        return ret

    def startup(self):
        """Initialize USBMux if it isn't already"""
        if self.mux is None:
            try:
                from .support.ios.usbmux import USBMux
                self.mux = USBMux()
            except Exception:
                logging.critical("Error initializing usbmux")

    def get_devices(self):
        """Get a list of available devices"""
        self.startup()
        devices = []
        self.mux.process(0.1)
        if self.mux.devices:
            for device in self.mux.devices:
                devices.append(device.serial)
        return devices

    def is_device_ready(self):
        """Get the battery level and only if it responds and is over 75% is it ok"""
        is_ready = False
        response = self.send_message("battery")
        if response:
            level = int(round(float(response) * 100))
            if level > 75:
                logging.debug("Battery level = %d%%", level)
                is_ready = True
            else:
                logging.debug("Device battery is low (%d%%)", level)
        else:
            logging.debug("Device is not connected (or iWptBrowser is not running)")
            self.disconnect()
        return is_ready

    def get_os_version(self):
        """Get the running version of iOS"""
        return self.send_message("osversion")

    def clear_cache(self):
        """Clear the browser cache"""
        is_ok = False
        if self.send_message("clearcache"):
            is_ok = True
        return is_ok

    def start_browser(self):
        """Start the browser"""
        is_ok = False
        if self.send_message("startbrowser"):
            is_ok = True
        return is_ok

    def stop_browser(self):
        """Stop the browser"""
        is_ok = False
        if self.send_message("stopbrowser"):
            is_ok = True
        return is_ok

    def navigate(self, url):
        """Navigate to the given URL"""
        is_ok = False
        if self.send_message("navigate", data=url):
            is_ok = True
        return is_ok

    def execute_js(self, script, remove_orange=False):
        """Run the given script"""
        command = "exec"
        if remove_orange:
            command += ".removeorange"
        ret = self.send_message(command, data=script)
        try:
            ret = json.loads(ret)
        except Exception:
            pass
        return ret

    def set_user_agent(self, ua_string):
        """Override the UA string"""
        is_ok = False
        if self.send_message("setuseragent", data=ua_string):
            is_ok = True
        return is_ok

    def set_cookie(self, url, name, value):
        """Set a cookie"""
        is_ok = False
        if self.send_message("setcookie", data=url + ";" + name + ";" + value):
            is_ok = True
        return is_ok

    def show_orange(self):
        """Bring up the orange overlay"""
        is_ok = False
        if self.send_message("showorange"):
            is_ok = True
        return is_ok

    def screenshot(self, png=True):
        """Capture a screenshot (PNG or JPEG)"""
        msg = "screenshotbig" if png else "screenshotbigjpeg"
        return self.send_message(msg)

    def start_video(self):
        """Start video capture"""
        is_ok = False
        if self.send_message("startvideo"):
            is_ok = True
        return is_ok

    def stop_video(self):
        """Stop the video capture and store it at the given path"""
        is_ok = False
        if self.send_message("stopvideo"):
            is_ok = True
        return is_ok

    def get_video(self, video_path):
        """Retrieve the recorded video"""
        is_ok = False
        self.video_size = 0
        if self.video_file is not None:
            self.video_file.close()
        self.video_file = open(video_path, 'wb')
        if self.video_file:
            if self.send_message("getvideo", timeout=600):
                logging.debug("Video complete: %d bytes", self.video_size)
                self.send_message("deletevideo")
                if self.video_size > 0:
                    is_ok = True
            self.video_file.close()
            self.video_file = None
        return is_ok

    def landscape(self):
        """Switch to landscape orientation"""
        self.send_message("landscape", wait=False)

    def portrait(self):
        """Switch to portrait orientation"""
        self.send_message("portrait", wait=False)

    def connect(self):
        """Connect to the device with the matching serial number"""
        self.startup()
        connecting = False
        needs_restart = False
        try:
            if self.socket is None:
                self.disconnect()
                self.mux.process(0.1)
                devices = self.mux.devices
                if devices:
                    for device in devices:
                        if self.serial is None or device.serial == self.serial:
                            logging.debug("Connecting to device %s", device.serial)
                            self.serial = device.serial
                            self.must_disconnect = False
                            connecting = True
                            self.socket = self.mux.connect(device, 19222)
                            self.message_thread = threading.Thread(target=self.pump_messages)
                            self.message_thread.daemon = True
                            self.message_thread.start()
                            break
        except Exception:
            # If the app isn't running restart the device (no more than every 10 minutes)
            if connecting and monotonic.monotonic() - self.last_restart > 600:
                needs_restart = True
        if needs_restart:
            self.last_restart = monotonic.monotonic()
            try:
                subprocess.call(['idevicediagnostics', 'restart'])
            except Exception:
                pass
        return self.socket is not None

    def disconnect(self):
        """Disconnect from the device"""
        self.must_disconnect = True
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if self.message_thread is not None:
            # self.message_thread.join()
            self.message_thread = None

    def send_message(self, message, data=None, wait=True, timeout=30):
        """Send a command and get the response"""
        response = None
        if self.connect():
            self.current_id += 1
            message_id = self.current_id
            msg = "{0:d}:{1}".format(message_id, message)
            logging.debug(">>> %s", msg)
            if data is not None:
                if data.find("\t") >= 0 or data.find("\n") >= 0 or data.find("\r") >= 0:
                    msg += ".encoded"
                    data = base64.b64encode(data)
                msg += "\t"
                msg += data
            try:
                self.socket.send(msg + "\n")
                if wait:
                    end = monotonic.monotonic() + timeout
                    while response is None and monotonic.monotonic() < end:
                        try:
                            msg = self.messages.get(timeout=1)
                            self.messages.task_done()
                            if msg:
                                if msg['msg'] == 'disconnected':
                                    self.disconnect()
                                    self.connect()
                                elif 'id' in msg and msg['id'] == str(message_id):
                                    if msg['msg'] == 'OK':
                                        if 'data' in msg:
                                            response = msg['data']
                                        else:
                                            response = True
                                    else:
                                        break
                        except Exception:
                            pass
            except Exception:
                self.disconnect()
        return response

    def flush_messages(self):
        """Flush all of the pending messages"""
        try:
            while True:
                self.messages.get_nowait()
                self.messages.task_done()
        except Exception:
            pass

    def pump_messages(self):
        """Background thread for reading messages from the browser"""
        buff = ""
        try:
            while not self.must_disconnect and self.socket != None:
                rlo, _, xlo = select.select([self.socket], [], [self.socket])
                if xlo:
                    logging.debug("iWptBrowser disconnected")
                    self.messages.put({"msg": "disconnected"})
                    return
                if rlo:
                    data_in = self.socket.recv(8192)
                    if not data_in:
                        logging.debug("iWptBrowser disconnected")
                        self.messages.put({"msg": "disconnected"})
                        return
                    buff += data_in
                    pos = 0
                    while pos >= 0:
                        pos = buff.find("\n")
                        if pos >= 0:
                            message = buff[:pos].strip()
                            buff = buff[pos + 1:]
                            if message:
                                self.process_raw_message(message)
        except Exception:
            pass

    def process_raw_message(self, message):
        """Process a single message string"""
        ts_end = message.find("\t")
        if ts_end > 0:
            message_len = len(message)
            timestamp = message[:ts_end]
            event_end = message.find("\t", ts_end + 1)
            if event_end == -1:
                event_end = message_len
            event = message[ts_end + 1:event_end]
            if timestamp and event:
                msg = {'ts': timestamp}
                data = None
                if event_end < message_len:
                    data = message[event_end + 1:]
                parts = event.split(":")
                if len(parts) > 1:
                    msg['id'] = parts[0]
                    message = parts[1].strip()
                else:
                    message = parts[0].strip()
                if message:
                    parts = message.split("!")
                    msg['msg'] = parts[0].strip()
                    if 'encoded' in parts and data is not None:
                        data = base64.b64decode(data)
                    if data is not None:
                        msg['data'] = data
                    self.process_message(msg)

    def process_message(self, msg):
        """Handle a single decoded message"""
        if msg['msg'] == 'VideoData' and 'data' in msg:
            now = monotonic.monotonic()
            self.video_size += len(msg['data'])
            if self.last_video_data is None or now - self.last_video_data >= 0.5:
                logging.debug('<<< Video data (current size: %d)', self.video_size)
                self.last_video_data = now
            if self.video_file is not None:
                self.video_file.write(msg['data'])
        elif 'id' in msg:
            logging.debug('<<< %s:%s', msg['id'], msg['msg'])
            try:
                self.messages.put(msg)
            except Exception:
                pass
        elif self.notification_queue is not None:
            logging.debug('<<< %s', msg['msg'])
            try:
                self.notification_queue.put(msg)
            except Exception:
                pass


def install_main():
    """Main entry-point when running as an installer (under sudo permissions)"""
    if os.getuid() != 0:
        print "Must run as sudo"
        exit(1)
    if not os.path.exists('/var/run/usbmuxd') and platform.system() == "Linux":
        print "Installing usbmuxd"
        if os.uname()[4].startswith('arm'):
            src_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                    'support', 'ios', 'arm')
        else:
            src_path = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                    'support', 'ios', 'Linux64')
        # Make sure all of the files are where they need to be
        for filename in os.listdir(src_path):
            src = os.path.join(src_path, filename)
            if os.path.isfile(src):
                dest = None
                if filename == 'usbmuxd':
                    dest = os.path.join('/usr/local/sbin', filename)
                elif filename.startswith('idevice'):
                    dest = os.path.join('/usr/local/bin', filename)
                elif filename.find('.so') >= 0:
                    dest = os.path.join('/usr/local/lib', filename)
                if dest is not None and not os.path.isfile(dest):
                    print "Copying {0} to {1}".format(filename, dest)
                    shutil.copy(src, dest)
        # Update the library cache
        subprocess.call(['ldconfig'])
        # Start and initialize usbmuxd
        print "Starting usbmuxd"
        subprocess.call(['/usr/local/sbin/usbmuxd'])
        subprocess.call(['/usr/local/bin/ideviceinfo'])


if __name__ == '__main__':
    install_main()
