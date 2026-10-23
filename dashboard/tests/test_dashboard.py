# SPDX-License-Identifier: MIT
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest

from dashboard.controller import BridgeController
from dashboard.server import DashboardServer, public_origin

ROOT = Path(__file__).resolve().parents[2]
DUMMY = """import signal,time,sys
signal.signal(signal.SIGINT,lambda *a:(print('clean-stop',flush=True),sys.exit(0)))
print('bridge session test; fake bridge only',flush=True)
print('R5 input live=True steering=32768',flush=True)
print('Pico status mounted=True donor_live=True',flush=True)
print('R5 outputs ready=True failed=False',flush=True)
while True: time.sleep(.02)
"""


def eventually(condition, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition(): return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for process state')


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bridge-dashboard-')
        self.root = Path(self.temp.name)
        self.controller = BridgeController(self.root, command=[sys.executable, '-u', '-c', DUMMY], stop_timeout=.3)

    def tearDown(self):
        self.controller.close()
        self.temp.cleanup()

    def test_repeated_and_concurrent_starts_create_one_process(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.controller.start(), range(5)))
        self.assertEqual(results.count(True), 1)
        eventually(lambda: self.controller.snapshot()['state'] == 'running')
        self.assertFalse(self.controller.start())
        eventually(lambda: self.controller.snapshot()['devices']['pico_mounted'] is True)
        status = self.controller.snapshot()
        self.assertTrue(status['devices']['pico_mounted'])
        self.assertEqual(status['profile']['gain_percent'], 25)

    @unittest.skipUnless(os.name == 'posix', 'Linux signal behavior')
    def test_stop_delivers_sigint_and_waits_for_clean_exit(self):
        self.controller.start()
        eventually(lambda: self.controller.snapshot()['state'] == 'running')
        self.controller.stop(wait=True)
        eventually(lambda: self.controller.snapshot()['state'] == 'stopped')
        self.assertEqual(self.controller.exit_code, 0)
        self.assertIn('clean-stop', self.controller.recent_log())
        self.assertFalse(self.controller.stop())

    def test_shutdown_stops_child_and_forbids_restart(self):
        self.controller.start()
        process = self.controller.process
        self.controller.close()
        self.assertIsNotNone(process.poll())
        with self.assertRaises(RuntimeError): self.controller.start()

    def test_failed_child_is_reported_and_can_be_restarted(self):
        self.controller.command = [sys.executable, '-u', '-c', "print('device failed',flush=True);raise SystemExit(7)"]
        self.controller.start()
        eventually(lambda: self.controller.snapshot()['state'] == 'error')
        self.assertEqual(self.controller.exit_code, 7)
        self.assertIn('device failed', self.controller.recent_log())
        self.controller.command = [sys.executable, '-u', '-c', DUMMY]
        self.controller.start()
        eventually(lambda: self.controller.snapshot()['state'] == 'running')
        self.assertIsNone(self.controller.error)

    def test_missing_executable_fails_without_running_state(self):
        self.controller.command = [str(self.root / 'missing-executable')]
        with self.assertRaises(RuntimeError): self.controller.start()
        self.assertEqual(self.controller.snapshot()['state'], 'error')
        self.assertIsNone(self.controller.process)

    def test_bounded_console_and_stale_device_state(self):
        for value in range(500): self.controller.record(str(value))
        self.assertEqual(len(self.controller.snapshot()['logs']), 180)
        self.controller.start()
        eventually(lambda: self.controller.snapshot()['state'] == 'running')
        eventually(lambda: self.controller.snapshot()['devices']['pico_mounted'] is True)
        with self.controller.lock:
            self.controller.telemetry['pico']['at'] -= 7
        self.assertIsNone(self.controller.snapshot()['devices']['pico_mounted'])

    @unittest.skipUnless(os.name == 'posix', 'Linux flock behavior')
    def test_terminal_lock_prevents_dashboard_duplicate(self):
        import fcntl
        with (self.root / '.bridge.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.controller.snapshot()['state'], 'external')
            with self.assertRaises(RuntimeError): self.controller.start()
        self.assertTrue(self.controller.start())

    @unittest.skipUnless(os.name == 'posix', 'Linux kill escalation')
    def test_unresponsive_child_is_killed_with_bounded_stop(self):
        script = "import signal,time;signal.signal(signal.SIGINT,signal.SIG_IGN);signal.signal(signal.SIGTERM,signal.SIG_IGN);print('bridge session stubborn',flush=True);time.sleep(30)"
        self.controller.command = [sys.executable, '-u', '-c', script]
        self.controller.start()
        eventually(lambda: self.controller.snapshot()['state'] == 'running')
        process = self.controller.process
        self.controller.stop(wait=True)
        eventually(lambda: self.controller.process is None)
        self.assertEqual(process.returncode, -signal.SIGKILL)


class FakeController:
    def __init__(self): self.starts = self.stops = 0
    def snapshot(self): return {'state': 'stopped', 'logs': [], 'profile': {'gain_percent': 25}}
    def start(self): self.starts += 1; return True
    def stop(self): self.stops += 1; return True
    def recent_log(self): return '<script>not executable</script>\n'


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.server = DashboardServer(('127.0.0.1', 0), self.controller,
                                      public_origins=['https://dashboard.example/'])
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
        self.authority = f'127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally: connection.close()

    def headers(self):
        return {'Origin': 'http://' + self.authority, 'X-Bridge-Token': self.server.token, 'Content-Type': 'application/json'}

    def test_static_status_and_read_only_get_cannot_start(self):
        status, headers, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'PIT', body)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        status, _, body = self.request('GET', '/api/status')
        self.assertEqual(json.loads(body)['token'], self.server.token)
        self.assertEqual(self.request('GET', '/api/start')[0], 404)
        self.assertEqual(self.controller.starts, 0)

    def test_start_and_stop_require_valid_same_origin_token(self):
        self.assertEqual(self.request('POST', '/api/start', '{}', self.headers())[0], 202)
        self.assertEqual(self.request('POST', '/api/stop', '{}', self.headers())[0], 202)
        self.assertEqual((self.controller.starts, self.controller.stops), (1, 1))

    def test_cross_origin_and_missing_token_do_not_change_process(self):
        for changed in ({'Origin': 'http://other.example'}, {'X-Bridge-Token': 'invalid'}):
            headers = self.headers(); headers.update(changed)
            self.assertEqual(self.request('POST', '/api/start', '{}', headers)[0], 403)
        self.assertEqual(self.request('POST', '/api/start', '{}', {})[0], 403)
        self.assertEqual(self.controller.starts, 0)

    def test_rebinding_host_cannot_read_token_or_control(self):
        self.assertEqual(self.request('GET', '/api/status', headers={'Host': 'other.example:' + str(self.server.server_port)})[0], 403)
        headers = self.headers(); headers['Host'] = 'other.example:' + str(self.server.server_port)
        self.assertEqual(self.request('POST', '/api/start', '{}', headers)[0], 403)
        self.assertEqual(self.controller.starts, 0)

    def test_explicit_https_origin_can_load_and_control_through_http_proxy(self):
        for host in ('dashboard.example', 'dashboard.example:443'):
            with self.subTest(host=host):
                self.assertEqual(self.request('GET', '/', headers={'Host': host})[0], 200)
                status, _, body = self.request('GET', '/api/status', headers={'Host': host})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['token'], self.server.token)
                headers = self.headers()
                headers.update(Host=host, Origin='https://dashboard.example')
                self.assertEqual(self.request('POST', '/api/start', '{}', headers)[0], 202)
                self.assertEqual(self.request('POST', '/api/stop', '{}', headers)[0], 202)
        self.assertEqual((self.controller.starts, self.controller.stops), (2, 2))

    def test_public_origin_rejects_http_cross_origin_and_missing_token(self):
        for origin in ('http://dashboard.example', 'https://other.example',
                       'https://dashboard.example.attacker.test', 'null', ''):
            with self.subTest(origin=origin):
                headers = self.headers()
                headers.update(Host='dashboard.example', Origin=origin)
                self.assertEqual(self.request('POST', '/api/start', '{}', headers)[0], 403)
        headers = {'Host': 'dashboard.example', 'Origin': 'https://dashboard.example',
                   'Content-Type': 'application/json'}
        self.assertEqual(self.request('POST', '/api/start', '{}', headers)[0], 403)
        self.assertEqual(self.controller.starts, 0)

    def test_forwarded_headers_cannot_authorize_unknown_host_or_change_lan_origin(self):
        forwarded = {'X-Forwarded-Host': 'dashboard.example', 'X-Forwarded-Proto': 'https',
                     'Forwarded': 'host=dashboard.example;proto=https'}
        headers = self.headers()
        headers.update(forwarded, Host='other.example', Origin='https://dashboard.example')
        self.assertEqual(self.request('GET', '/api/status', headers=headers)[0], 403)
        self.assertEqual(self.request('POST', '/api/start', '{}', headers)[0], 403)
        headers.update(Host=self.authority, Origin='https://' + self.authority)
        self.assertEqual(self.request('POST', '/api/start', '{}', headers)[0], 403)
        headers['Origin'] = 'http://' + self.authority
        self.assertEqual(self.request('POST', '/api/start', '{}', headers)[0], 202)

    def test_public_host_suffix_bad_port_and_url_syntax_are_rejected(self):
        for host in ('dashboard.example.attacker.test', 'dashboard.example:80',
                     'dashboard.example:8080', 'dashboard.example:0',
                     'dashboard.example:99999', 'dashboard.example:',
                     'user@dashboard.example', 'dashboard.example/path',
                     'dashboard.example?query', 'dashboard.example#fragment',
                     'dashboard.example,other.example', 'dashboard.example\\other'):
            with self.subTest(host=host):
                self.assertEqual(self.request('GET', '/api/status', headers={'Host': host})[0], 403)

    def test_duplicate_host_headers_are_rejected(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.putrequest('GET', '/api/status', skip_host=True)
            connection.putheader('Host', self.authority)
            connection.putheader('Host', 'dashboard.example')
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 403)
        finally:
            connection.close()

    def test_public_origin_configuration_is_explicit_and_https_only(self):
        with DashboardServer(('127.0.0.1', 0), self.controller) as server:
            self.assertEqual(server.public_origins, {})
        self.assertEqual(public_origin('https://DASHBOARD.example:8443/'),
                         (('dashboard.example', 8443), 'https://dashboard.example:8443'))
        for value in ('http://dashboard.example', 'dashboard.example',
                      'https://dashboard.example/path', 'https://user@dashboard.example',
                      'https://dashboard.example?', 'https://dashboard.example#',
                      'https://dashboard.example:0', 'https://dashboard.example:99999',
                      'https://*.example', ' https://dashboard.example',
                      'https://dash\tboard.example', 'https://dashboard.\nexample',
                      'https://dashboard.example\\other', 'https://[::1]other'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                public_origin(value)

    def test_browser_cannot_supply_shell_command_or_gain(self):
        self.assertEqual(self.request('POST', '/api/start', '{"gain": 1, "command": "anything"}', self.headers())[0], 400)
        self.assertEqual(self.request('POST', '/api/start', 'not json', self.headers())[0], 400)
        headers = self.headers(); headers['Content-Type'] = 'text/plain'
        self.assertEqual(self.request('POST', '/api/start', '{}', headers)[0], 415)
        self.assertEqual(self.controller.starts, 0)

    def test_oversized_request_traversal_and_log_export(self):
        self.assertEqual(self.request('POST', '/api/start', 'x' * 1025, self.headers())[0], 413)
        self.assertEqual(self.request('GET', '/../server.py')[0], 404)
        status, headers, body = self.request('GET', '/api/logs/export')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'text/plain; charset=utf-8')
        self.assertIn('attachment', headers['Content-Disposition'])


@unittest.skipUnless(sys.platform == 'linux', 'Real Linux launcher and parent-death guard')
class LinuxProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pit-linux-test-')
        self.root = Path(self.temp.name)
        (self.root / '.venv/bin').mkdir(parents=True)
        (self.root / '.venv/bin/python').symlink_to(sys.executable)
        self.launcher = self.root / 'start-bridge.sh'
        source = ROOT / 'tools/start_bridge_current.sh'
        if not source.exists(): source = ROOT / 'start-bridge.sh'
        self.launcher.write_bytes(source.read_bytes())
        self.launcher.chmod(0o755)
        package = self.root / 'moza_ps5'; package.mkdir()
        (package / '__main__.py').write_text(DUMMY, encoding='utf-8')

    def tearDown(self): self.temp.cleanup()

    def test_real_saved_launcher_has_25_percent_and_rejects_second_bridge(self):
        import shlex
        command = shlex.split(subprocess.check_output([str(self.launcher), '--check'], text=True))
        self.assertEqual(command[command.index('--ffb-gain') + 1], '0.25')
        process = subprocess.Popen([str(self.launcher)], cwd=self.root, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            self.assertIn('bridge session test', process.stdout.readline())
            result = subprocess.run([str(self.launcher)], cwd=self.root, capture_output=True, timeout=2)
            self.assertEqual(result.returncode, 75)
        finally:
            os.killpg(process.pid, signal.SIGINT)
            process.communicate(timeout=3)

    def test_bridge_cannot_outlive_abrupt_dashboard_parent_death(self):
        fake = self.root / 'child.sh'
        marker = self.root / 'alive'
        fake.write_text(f'#!/bin/sh\nexec {sys.executable} -c "from pathlib import Path;import time;Path(\'{marker}\').touch();time.sleep(30)"\n', encoding='utf-8')
        fake.chmod(0o755)
        parent_code = "import os,subprocess,sys,time;p=subprocess.Popen([sys.executable,sys.argv[1],str(os.getpid()),sys.argv[2]],stdout=subprocess.DEVNULL);print(p.pid,flush=True);time.sleep(30)"
        parent = subprocess.Popen([sys.executable, '-u', '-c', parent_code, str(ROOT / 'dashboard/bridge_child.py'), str(fake)], stdout=subprocess.PIPE, text=True)
        child_pid = int(parent.stdout.readline())
        try:
            eventually(marker.exists)
            parent.kill(); parent.wait(timeout=2)
            def child_gone():
                path = Path(f'/proc/{child_pid}/stat')
                return not path.exists() or path.read_text().split()[2] == 'Z'
            eventually(child_gone)
        finally:
            if parent.poll() is None: parent.kill(); parent.wait(timeout=2)
            parent.stdout.close()
            try: os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError: pass


if __name__ == '__main__': unittest.main()
