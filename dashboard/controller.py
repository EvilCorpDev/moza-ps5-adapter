# SPDX-License-Identifier: MIT
"""One managed bridge process, bounded live logs and graceful release."""
from collections import deque
from datetime import datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from threading import RLock, Thread
import time

R5_INPUT = '/dev/input/by-id/usb-Gudsen_MOZA_R5_Base_3F001C000651333031333639-if02-event-joystick'


class BridgeController:
    def __init__(self, root, *, command=None, stop_timeout=7):
        self.root = Path(root).resolve()
        self.command = command
        self.stop_timeout = stop_timeout
        self.lock = RLock()
        self.process = None
        self.state = 'stopped'
        self.error = None
        self.exit_code = None
        self.started_at = None
        self.closed = False
        self.sequence = 0
        self.lines = deque(maxlen=180)
        self.telemetry = {}
        self.reader = None
        self.stopper = None
        log_dir = self.root / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        self.raw = logging.getLogger('bridge-dashboard-' + str(id(self)))
        self.raw.setLevel(logging.INFO)
        self.raw.propagate = False
        handler = RotatingFileHandler(log_dir / 'dashboard-bridge.log',
            maxBytes=5 * 1024 * 1024, backupCount=4, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        self.raw.addHandler(handler)
        self.record('Dashboard ready. Saved profile: 25% force, damping on, game rev LEDs.', 'system')

    def record(self, text, kind='info'):
        with self.lock:
            self.sequence += 1
            self.lines.append({'id': self.sequence, 'time': datetime.now(timezone.utc).isoformat(),
                               'text': str(text)[:1500], 'kind': kind})

    def externally_running(self):
        if os.name != 'posix':
            return False
        import fcntl
        with (self.root / '.bridge.lock').open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(stream, fcntl.LOCK_UN)
        return False

    def start(self):
        with self.lock:
            if self.closed:
                raise RuntimeError('Dashboard is shutting down.')
            if self.process is not None and self.process.poll() is None:
                return False
            if self.externally_running():
                raise RuntimeError('The bridge is already running outside this dashboard. Stop it in its terminal first.')
            launcher = self.root / 'start-bridge.sh'
            if self.command is None and not launcher.is_file():
                raise RuntimeError('The saved bridge launcher is missing.')
            command = self.command or [sys.executable, str(Path(__file__).with_name('bridge_child.py')),
                                       str(os.getpid()), str(launcher)]
            self.error = None
            self.exit_code = None
            self.telemetry = {}
            self.state = 'starting'
            self.started_at = time.monotonic()
            self.record('Starting bridge with the saved 25% profile.', 'system')
            self.raw.info('\n=== Bridge start %s | gain 25%% | damper ON | game LEDs ===',
                          datetime.now(timezone.utc).isoformat())
            try:
                self.process = subprocess.Popen(command, cwd=self.root,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace', bufsize=1,
                    start_new_session=(os.name == 'posix'))
            except OSError as exc:
                self.process = None
                self.state = 'error'
                self.error = str(exc)
                self.record('Start failed: ' + str(exc), 'error')
                raise RuntimeError('Bridge could not start: ' + str(exc)) from exc
            process = self.process
            self.reader = Thread(target=self._read, args=(process,), name='bridge-log', daemon=True)
            self.reader.start()
            return True

    def _read(self, process):
        try:
            for line in process.stdout:
                line = line.rstrip('\r\n')[:8192]
                self.raw.info(line)
                with self.lock:
                    if self.process is not process:
                        continue
                    self._observe(line)
        except (OSError, ValueError) as exc:
            self.record('Log stream closed: ' + str(exc), 'system')
        finally:
            process.stdout.close()
            code = process.wait()
            with self.lock:
                if self.process is process:
                    expected = self.state == 'stopping' or self.closed
                    self.process = None
                    self.exit_code = code
                    self.telemetry = {}
                    self.state = 'stopped' if expected or code == 0 else 'error'
                    if not expected and code != 0:
                        self.error = ('Bridge is already running in another terminal.' if code == 75
                                      else 'Bridge exited. Check the log below.')
                    self.record('Bridge stopped.' if expected or code == 0 else f'Bridge exited with status {code}.',
                                'system' if expected or code == 0 else 'error')

    def _observe(self, line):
        now = time.monotonic()
        if line.startswith('bridge session ') and self.state != 'stopping':
            self.state = 'running'
        if line.startswith('Pico status '):
            values = dict(re.findall(r'(\w+)=([^ ]+)', line))
            self.telemetry['pico'] = {'mounted': values.get('mounted') == 'True', 'at': now}
        if line.startswith('R5 input live='):
            self.telemetry['r5'] = {'live': line.startswith('R5 input live=True'), 'at': now}
        if line.startswith('R5 outputs ready='):
            failed = 'failed=True' in line
            self.telemetry['outputs'] = {'ready': line.startswith('R5 outputs ready=True'), 'failed': failed, 'at': now}
            if failed:
                self.error = 'Wheel output stopped. Stop the bridge and check the wheelbase connection.'
        # Keep repetitive report bytes in the rotating raw log, not the browser.
        if line.startswith(('Pico status ', 'R5 input ', 'R5 outputs ')):
            return
        if line.startswith('Pico event ') and not any('name=' + name + ' ' in line for name in ('mounted', 'unmounted', 'suspended', 'resumed')):
            return
        if not line.strip():
            return
        kind = 'error' if any(x in line.lower() for x in ('failed', 'traceback', 'error:', 'exception', 'permission denied')) else 'info'
        self.record(line, kind)

    def stop(self, *, wait=False):
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                return False
            if self.state != 'stopping':
                self.state = 'stopping'
                self.record('Stopping bridge; releasing game force and wheel connections.', 'system')
                self.stopper = Thread(target=self._terminate, args=(process,), name='bridge-stop', daemon=True)
                self.stopper.start()
            stopper = self.stopper
        if wait and stopper:
            stopper.join(timeout=self.stop_timeout + 6)
        return True

    def _signal(self, process, value):
        if process.poll() is not None:
            return
        try:
            if os.name == 'posix':
                os.killpg(process.pid, value)
            elif value == signal.SIGINT:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass

    def _terminate(self, process):
        for value, timeout in ((signal.SIGINT, self.stop_timeout), (signal.SIGTERM, 2), (getattr(signal, 'SIGKILL', signal.SIGTERM), 2)):
            self._signal(process, value)
            try:
                process.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                self.record('Bridge did not exit yet; escalating stop.', 'system')
        self.record('Bridge did not exit after the stop request.', 'error')

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            managed = self.process is not None and self.process.poll() is None
            state = self.state
            if not managed and state not in ('starting', 'stopping') and self.externally_running():
                state = 'external'
            def fresh(key):
                value = self.telemetry.get(key, {})
                return value if managed and now - value.get('at', float('-inf')) < 6 else {}
            pico, r5, outputs = fresh('pico'), fresh('r5'), fresh('outputs')
            return {'state': state, 'error': self.error, 'exit_code': self.exit_code,
                    'pid': self.process.pid if managed else None,
                    'uptime': int(now - self.started_at) if managed and self.started_at else 0,
                    'profile': {'gain_percent': 25, 'damping': True, 'leds': True},
                    'devices': {'r5_detected': Path(R5_INPUT).exists(), 'r5_live': r5.get('live'),
                                'pico_mounted': pico.get('mounted'), 'outputs_ready': outputs.get('ready'),
                                'outputs_failed': outputs.get('failed', False)},
                    'logs': list(self.lines), 'server_time': datetime.now(timezone.utc).isoformat()}

    def recent_log(self):
        with self.lock:
            return '\n'.join(f"{row['time']} [{row['kind']}] {row['text']}" for row in self.lines) + '\n'

    def close(self):
        with self.lock:
            self.closed = True
        self.stop(wait=True)
        if self.reader:
            self.reader.join(timeout=2)
        for handler in list(self.raw.handlers):
            handler.close()
            self.raw.removeHandler(handler)
