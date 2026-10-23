#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Local-network web dashboard. No shell commands or profile values from HTTP."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
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


def authority(value):
    """Parse one Host authority without accepting URL/path/userinfo syntax."""
    if not value or any(not 33 <= ord(char) <= 126 or char in '/\\?#@,' for char in value):
        raise ValueError('Invalid dashboard host')
    parsed = urlsplit('//' + value)
    port = parsed.port
    if not parsed.hostname or port == 0 or value.endswith(':'):
        raise ValueError('Invalid dashboard host')
    name = parsed.hostname.lower()
    host = f'[{name}]' if ':' in name else name
    canonical = host + (f':{port}' if port is not None else '')
    if value.lower() != canonical or any(char in name for char in '%*"\''):
        raise ValueError('Invalid dashboard host')
    return name, port


def public_origin(value):
    """Public origins must be explicit HTTPS URLs; forwarded headers grant nothing."""
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.path not in ('', '/')
            or '?' in value or '#' in value or any(char.isspace() for char in value)):
        raise ValueError('Public dashboard origins must be HTTPS origins without paths')
    name, port = authority(parsed.netloc)
    port = port or 443
    host = f'[{name}]' if ':' in name else name
    return (name, port), 'https://' + host + (f':{port}' if port != 443 else '')


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, controller, *, public_origins=()):
        self.controller = controller
        self.token = secrets.token_hex(32)
        self.hostnames = {'localhost', 'wheelpi', 'wheelpi.local', socket.gethostname().lower()}
        self.public_origins = dict(public_origin(value) for value in public_origins)
        super().__init__(address, Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address


class Handler(BaseHTTPRequestHandler):
    server_version = 'PitControl/1.0'

    def log_message(self, *args):
        pass

    def request_origin(self):
        try:
            hosts = self.headers.get_all('Host', [])
            if len(hosts) != 1:
                return None
            raw = hosts[0]
            name, port = authority(raw)
            configured = self.server.public_origins.get((name, port or 443))
            if configured:
                return configured
            local_names = self.server.hostnames | {self.connection.getsockname()[0], '127.0.0.1', '::1'}
            if (port or 80) == self.server.server_port and name in local_names:
                return 'http://' + raw.lower()
        except ValueError:
            pass
        return None

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
        if not self.request_origin():
            return self.send_json(403, {'error': 'Use the Pi address or a configured public dashboard origin.'})
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
        expected_origin = self.request_origin()
        if not expected_origin:
            return self.send_json(403, {'error': 'Invalid dashboard address.'})
        if self.headers.get('Origin', '').lower() != expected_origin:
            return self.send_json(403, {'error': 'Open this dashboard directly at its configured address.'})
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
    ap.add_argument('--public-origin', action='append', default=[],
                    help='explicit HTTPS browser origin behind a protected reverse proxy (repeatable)')
    args = ap.parse_args()
    origins = os.environ.get('PIT_CONTROL_PUBLIC_ORIGINS', '').split() + args.public_origin
    try:
        for value in origins: public_origin(value)
    except ValueError as exc:
        ap.error(str(exc))
    controller = BridgeController(args.root)
    server = None
    try:
        server = DashboardServer((args.host, args.port), controller, public_origins=origins)
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
