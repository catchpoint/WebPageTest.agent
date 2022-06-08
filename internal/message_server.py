# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
try:
    import asyncio
except Exception:
    pass
from multiprocessing import JoinableQueue
import logging
import sys
import threading
import time
import tornado.ioloop
import tornado.web

MESSAGE_SERVER = None

BLANK_PAGE = """<html>
<head>
<title>Blank</title>
<style type="text/css">body {background-color: #FFF;}</style>
</head>
<body>
</body>
</html>"""

ORANGE_PAGE = """<html>
<head>
<title>Orange</title>
<style>
body {background-color: white; margin: 0;}
#wptorange {width:100%; height: 100%; background-color: #DE640D;}
</style>
</head>
<body><div id='wptorange'></div></body>
</html>"""


class TornadoRequestHandler(tornado.web.RequestHandler):
    """Request handler for when we are using tornado"""
    def get(self):
        """Handle GET requests"""
        try:
            import ujson as json
        except BaseException:
            import json
        response = None
        content_type = 'text/plain'
        if self.request.uri == '/ping':
            response = 'pong'
        elif self.request.uri == '/blank.html':
            content_type = 'text/html'
            response = BLANK_PAGE
        elif self.request.uri == '/orange.html':
            content_type = 'text/html'
            response = ORANGE_PAGE
        elif self.request.uri == '/config':
            # JSON config data
            content_type = 'application/json'
            response = '{}'
            if MESSAGE_SERVER.config is not None:
                response = json.dumps(MESSAGE_SERVER.config)
        elif self.request.uri == '/config.html':
            # Orange HTML page that can be queried from the extension for config data
            content_type = 'text/html'
            response = "<html><head>\n"
            response += "<style>\n"
            response += "body {background-color: white; margin: 0;}\n"
            response += "#wptorange {width:100%; height: 100%; background-color: #DE640D;}\n"
            response += "</style>\n"
            response += "</head><body><div id='wptorange'></div>\n"
            if MESSAGE_SERVER.config is not None:
                import html
                response += '<div id="wptagentConfig" style="display: none;">'
                response += html.escape(json.dumps(MESSAGE_SERVER.config))
                response += '</div>'
            response += "</body></html>"
        elif self.request.uri == '/wpt-start-recording':
            response = 'ok'

        if response is not None:
            self.set_status(200)
            self.set_header("Content-Type", content_type)
            self.set_header("Referrer-Policy", "no-referrer")
            self.set_header("Access-Control-Allow-Origin", "*")
            self.write(response)

    def post(self):
        """Handle POST messages"""
        try:
            import ujson as json
        except BaseException:
            import json
        try:
            messages = self.request.body
            if messages is not None and len(messages):
                if self.request.uri == '/log':
                    logging.debug(messages)
                else:
                    for line in messages.splitlines():
                        line = line.strip()
                        if len(line):
                            message = json.loads(line)
                            if 'body' not in message and self.request.uri != '/etw':
                                message['body'] = None
                            MESSAGE_SERVER.handle_message(message)
        except Exception:
            logging.exception('Error processing POST message')
        self.set_status(200)


class MessageServer(object):
    """Local HTTP server for interacting with the extension"""
    def __init__(self):
        global MESSAGE_SERVER
        MESSAGE_SERVER = self
        self.thread = None
        self.messages = JoinableQueue()
        self.config = None
        self.__is_started = threading.Event()

    def get_message(self, timeout):
        """Get a single message from the queue"""
        message = self.messages.get(block=True, timeout=timeout)
        self.messages.task_done()
        return message

    def flush_messages(self):
        """Flush all of the pending messages"""
        try:
            while True:
                self.messages.get_nowait()
                self.messages.task_done()
        except Exception:
            pass

    def handle_message(self, message):
        """Add a received message to the queue"""
        self.messages.put(message)

    def start(self):
        """Start running the server in a background thread"""
        self.__is_started.clear()
        self.thread = threading.Thread(target=self.run)
        self.thread.daemon = True
        self.thread.start()
        self.__is_started.wait(timeout=30)

    def stop(self):
        """Stop running the server"""
        logging.debug("Shutting down extension server")
        self.must_exit = True
        if self.thread is not None:
            ioloop = tornado.ioloop.IOLoop.instance()
            ioloop.add_callback(ioloop.stop)
            self.thread.join()
        self.thread = None
        logging.debug("Extension server stopped")

    def is_ok(self):
        """Check that the server is responding and restart it if necessary"""
        import requests
        if (sys.version_info >= (3, 0)):
            from time import monotonic
        else:
            from monotonic import monotonic
        end_time = monotonic() + 30
        server_ok = False
        proxies = {"http": None, "https": None}
        while not server_ok and monotonic() < end_time:
            try:
                response = requests.get('http://127.0.0.1:8888/ping', timeout=10, proxies=proxies)
                if response.text == 'pong':
                    server_ok = True
            except Exception:
                pass
            if not server_ok:
                time.sleep(5)
        return server_ok

    def run(self):
        """Main server loop"""
        logging.debug('Starting extension server on port 8888')
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
        except Exception:
            pass
        application = tornado.web.Application([(r"/.*", TornadoRequestHandler)])
        application.listen(8888, '127.0.0.1')
        self.__is_started.set()
        tornado.ioloop.IOLoop.instance().start()
