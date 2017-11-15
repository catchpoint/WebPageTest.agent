# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
import logging
import tornado.ioloop
import tornado.web

MESSAGE_SERVER = None

class TornadoRequestHandler(tornado.web.RequestHandler):
    """Request handler for when we are using tornado"""
    def get(self):
        """Handle GET requests"""
        import ujson as json
        logging.debug(self.request.uri)
        response = None
        content_type = 'text/plain'
        if self.request.uri == '/ping':
            response = 'pong'
        elif self.request.uri == '/config':
            # JSON config data
            content_type = 'application/json'
            response = '{}'
            if MESSAGE_SERVER.config is not None:
                response = json.dumps(MESSAGE_SERVER.config)
        elif self.request.uri == '/config.html':
            # HTML page that can be queried from the extension for config data
            content_type = 'text/html'
            response = "<html><head>\n"
            if MESSAGE_SERVER.config is not None:
                import cgi
                response += '<div id="wptagentConfig" style="display: none;">'
                response += cgi.escape(json.dumps(MESSAGE_SERVER.config))
                response += '</div>'
            response += "</head><body></body></html>"

        if response is not None:
            self.set_status(200)
            self.set_header("Content-Type", content_type)
            self.write(response)

    def post(self):
        """Handle POST messages"""
        import ujson as json
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
            pass
        self.set_status(200)

def stop_tornado():
    """Stop the tornado server"""
    ioloop = tornado.ioloop.IOLoop.instance()
    ioloop.add_callback(ioloop.stop)

def start_tornado(message_server):
    """Start (and run) the tornado server"""
    global MESSAGE_SERVER
    MESSAGE_SERVER = message_server
    application = tornado.web.Application([(r"/.*", TornadoRequestHandler)])
    application.listen(8888, '127.0.0.1')
    message_server.is_started.set()
    tornado.ioloop.IOLoop.instance().start()
