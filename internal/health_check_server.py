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
if (sys.version_info >= (3, 0)):
    from time import monotonic
else:
    from monotonic import monotonic

HEALTH_CHECK_SERVER = None

class TornadoRequestHandler(tornado.web.RequestHandler):
    """Request handler for when we are using tornado"""
    def get(self):
        """Handle GET requests"""
        response = None
        content_type = 'text/plain'
        response = "FAIL\n"
        status_code = 503

        # Return health if we have been marked as healthy in the last 5 minutes
        elapsed = monotonic() - HEALTH_CHECK_SERVER.last_healthy
        if elapsed < 300:
            response = "OK\n"
            status_code = 200

        if response is not None:
            self.set_status(status_code)
            self.set_header("Content-Type", content_type)
            self.set_header("Referrer-Policy", "no-referrer")
            self.set_header("Access-Control-Allow-Origin", "*")
            self.write(response)

class HealthCheckServer(object):
    """Local HTTP server for interacting with the extension"""
    def __init__(self, server_port):
        global HEALTH_CHECK_SERVER
        HEALTH_CHECK_SERVER = self
        self.thread = None
        self.messages = JoinableQueue()
        self.server_port = server_port
        self.last_healthy = monotonic()
        self.__is_started = threading.Event()

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

    def healthy(self):
        """Mark the last time that the agent was healthy"""
        self.last_healthy = monotonic()

    def is_ok(self):
        """Check that the server is responding and restart it if necessary"""
        import requests
        end_time = monotonic() + 30
        server_ok = False
        proxies = {"http": None, "https": None}
        while not server_ok and monotonic() < end_time:
            try:
                requests.get('http://127.0.0.1:{}/'.format(self.server_port), timeout=10, proxies=proxies)
                server_ok = True
            except Exception:
                pass
            if not server_ok:
                time.sleep(5)
        return server_ok

    def run(self):
        """Main server loop"""
        logging.debug('Starting health check server on port %d', self.server_port)
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
        except Exception:
            pass
        application = tornado.web.Application([(r"/.*", TornadoRequestHandler)])
        application.listen(self.server_port, '0.0.0.0')
        self.__is_started.set()
        tornado.ioloop.IOLoop.instance().start()
