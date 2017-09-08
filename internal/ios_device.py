# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Interface for iWptBrowser on iOS devices"""
import base64
import logging
import os
import Queue
import select
import threading
import monotonic
import ujson as json

class iOSDevice(object):
    """iOS device interface"""
    def __init__(self, serial=None):
        from .support.ios.usbmux import USBMux
        self.socket = None
        self.serial = serial
        self.must_disconnect = False
        self.mux = USBMux()
        self.message_thread = None
        self.messages = Queue.Queue()
        self.notification_queue = None
        self.current_id = 0
        self.video_path = None

    def get_devices(self):
        """Get a list of available devices"""
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
        if response and response > 0.75:
            is_ready = True
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

    def show_orange(self):
        """Bring up the orange overlay"""
        is_ok = False
        if self.send_message("showorange"):
            is_ok = True
        return is_ok

    def screenshot(self, png=True):
        """Capture a screenshot (PNG or JPEG)"""
        msg = "screenshot" if png else "screenshotjpeg"
        return self.send_message(msg)

    def start_video(self):
        """Start video capture"""
        is_ok = False
        if self.send_message("startvideo"):
            is_ok = True
        return is_ok

    def stop_video(self, video_path):
        """Stop the video capture and store it at the given path"""
        is_ok = False
        self.video_path = video_path
        if self.send_message("stopvideo"):
            if self.send_message("getvideo", timeout=600):
                self.send_message("deletevideo")
                if os.path.isfile(self.video_path):
                    is_ok = True
                self.video_path = None
        return is_ok

    def landscape(self):
        """Switch to landscape orientation"""
        is_ok = False
        if self.send_message("landscape"):
            is_ok = True
        return is_ok

    def portrait(self):
        """Switch to portrait orientation"""
        is_ok = False
        if self.send_message("portrait"):
            is_ok = True
        return is_ok

    def connect(self):
        """Connect to the device with the matching serial number"""
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
                            self.socket = self.mux.connect(device, 19222)
                            self.message_thread = threading.Thread(target=self.pump_messages)
                            self.message_thread.daemon = True
                            self.message_thread.start()
                            break
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
            self.message_thread.join()
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
        while not self.must_disconnect and self.socket != None:
            rlo, _, xlo = select.select([self.socket], [], [self.socket])
            if xlo:
                logging.debug("iWptBrowser disconnected")
                self.messages.put({"msg":"disconnected"})
                return
            if rlo:
                data_in = self.socket.recv(8192)
                if not data_in:
                    logging.debug("iWptBrowser disconnected")
                    self.messages.put({"msg":"disconnected"})
                    return
                buff += data_in
                pos = 0
                while pos >= 0:
                    pos = buff.find("\n")
                    if pos >= 0:
                        message = buff[:pos].strip()
                        buff = buff[pos + 1:]
                        if message:
                            parts = message.split("\t")
                            if len(parts) > 1:
                                msg = {'ts': parts[0]}
                                data = None
                                if len(parts) > 2:
                                    data = parts[2]
                                parts = parts[1].split(":")
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
        if msg['msg'] == 'StartVideo':
            if self.video_path is not None and os.path.isfile(self.video_path):
                try:
                    os.remove(self.video_path)
                except Exception:
                    pass
        elif msg['msg'] == 'VideoData' and 'data' in msg:
            if self.video_path is not None:
                with open(self.video_path, 'ab') as video_file:
                    video_file.write(msg['data'])
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
