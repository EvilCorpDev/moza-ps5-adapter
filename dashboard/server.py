#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Local-network web dashboard. No shell commands or profile values from HTTP."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import signal
import socket
import sys
from threading import Thread
from urllib.parse import urlsplit

if __package__:
    from .controller import BridgeController
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dashboard.controller import BridgeController

STATIC = Path(__file__).with_name('static')
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.css': ('app.css', 'text/css; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/favicon.svg': ('favicon.svg', 'image/svg+xml')}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, controller):
        self.controller = controller
        self.token = secrets.token_hex(32)
        self.hostnames = {'localhost', 'wheelpi', 'wheelpi.local', socket.gethostname().lower()}
        super().__init__(address, Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address


class Handler(BaseHTTPRequestHandler):
    server_version = 'PitControl/1.0'

    def log_message(self, *args):
        pass

    def valid_host(self):
        try:
            raw = self.headers.get('Host', '')
            parsed = urlsplit('http://' + raw)
            name = (parsed.hostname or '').lower()
            return (not parsed.username and not parsed.password and not parsed.path
                    and not parsed.query and not parsed.fragment
                    and (parsed.port or 80) == self.server.server_port
                    and name in self.server.hostnames | {self.connection.getsockname()[0], '127.0.0.1', '::1'})
        except ValueError:
            return False

    def send_data(self, code, body, content_type='application/json; charset=utf-8', *, download=False):
        if isinstance(body, str): body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if download: self.send_header('Content-Disposition', 'attachment; filename="wheel-bridge-recent.log"')
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def send_json(self, code, value):
        self.send_data(code, json.dumps(value))

    def do_GET(self):
        if not self.valid_host():
            return self.send_json(403, {'error': 'Use the Pi hostname or IP address to open this dashboard.'})
        path = urlsplit(self.path).path
        if path == '/api/status':
            value = self.server.controller.snapshot()
            value['token'] = self.server.token
            return self.send_json(200, value)
        if path == '/api/logs/export':
            return self.send_data(200, self.server.controller.recent_log(), 'text/plain; charset=utf-8', download=True)
        if path in ASSETS:
            name, content_type = ASSETS[path]
            return self.send_data(200, (STATIC / name).read_bytes(), content_type)
        self.send_json(404, {'error': 'Not found.'})

    def do_POST(self):
        if not self.valid_host():
            return self.send_json(403, {'error': 'Invalid dashboard address.'})
        expected_origin = 'http://' + self.headers.get('Host', '').lower()
        if self.headers.get('Origin', '').lower() != expected_origin:
            return self.send_json(403, {'error': 'Open this dashboard directly on the Pi address.'})
        if not secrets.compare_digest(self.headers.get('X-Bridge-Token', ''), self.server.token):
            return self.send_json(403, {'error': 'Dashboard session changed. Reload the page.'})
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            return self.send_json(415, {'error': 'Expected JSON.'})
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= length <= 1024:
                return self.send_json(413, {'error': 'Invalid request size.'})
            body = json.loads(self.rfile.read(length))
            if body != {}:
                return self.send_json(400, {'error': 'The saved bridge profile cannot be changed from the browser.'})
        except (ValueError, UnicodeError):
            return self.send_json(400, {'error': 'Invalid JSON.'})
        path = urlsplit(self.path).path
        try:
            if path == '/api/start': changed = self.server.controller.start()
            elif path == '/api/stop': changed = self.server.controller.stop()
            else: return self.send_json(404, {'error': 'Not found.'})
            self.send_json(202, {'changed': changed, 'state': self.server.controller.snapshot()['state']})
        except RuntimeError as exc:
            self.send_json(409, {'error': str(exc)})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True, type=Path)
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=8080)
    args = ap.parse_args()
    controller = BridgeController(args.root)
    server = None
    try:
        server = DashboardServer((args.host, args.port), controller)
        def shutdown(*_): Thread(target=server.shutdown, name='http-shutdown', daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        print(f'Pit Control listening on {args.host}:{server.server_port}; bridge starts only on request.', flush=True)
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        if server: server.server_close()
        controller.close()


if __name__ == '__main__':
    main()
