#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the existing 10% bridge with operator-controlled handle closure.

No automatic reopening after a diagnostic close. Main-thread input and
worker-thread output handles are closed by their respective owners. This
probe isolates lifecycle behavior without modifying production bridge code.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import signal
import sys
from threading import Event, Thread
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'host'))
from moza_ps5 import bridge
from moza_ps5.output_worker import OutputWorker
from moza_ps5.ps_button import neutral_input
from moza_ps5.r5_input import LinuxR5Source

close_force, close_led, close_input, finished = Event(), Event(), Event(), Event()
devices = {}


def emit(action, **fields):
    print(json.dumps({'utc': datetime.now(timezone.utc).isoformat(),
                      'monotonic': time.monotonic(), 'probe_action': action,
                      **fields}), flush=True)


class DiagnosticWorker(OutputWorker):
    def __init__(self, **options):
        super().__init__(**options)
        devices['worker'] = self

    def tick(self, now):
        if close_force.is_set() and self.force is not None:
            old_fd, old_id = self.force.fd, self.force.effect_id
            self.force.close()
            self.force = None
            self.applied_force = 0
            emit('ffb-closed', fd=old_fd, effect_id_before=old_id)
        if close_led.is_set() and self.leds is not None:
            self.leds.close()
            self.leds = None
            emit('led-closed')
        super().tick(now)


class DiagnosticSource(LinuxR5Source):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        devices['source'] = self
        self.closed_reported = False

    def poll(self, now):
        if close_input.is_set():
            if not self.closed_reported:
                old_fd = self.fd
                super().close()
                self.closed_reported = True
                emit('input-closed', fd=old_fd)
            return neutral_input()
        return super().poll(now)


def status():
    worker, source = devices.get('worker'), devices.get('source')
    force = getattr(worker, 'force', None)
    emit('status', input_fd=getattr(source, 'fd', None),
         ffb_fd=getattr(force, 'fd', None),
         effect_id=getattr(force, 'effect_id', None),
         applied_force=getattr(worker, 'applied_force', None),
         led_open=getattr(worker, 'leds', None) is not None,
         ready=getattr(worker, 'ready', False))


def operator_loop():
    deadline = time.monotonic() + 300
    actions = {'ffb-close': close_force, 'led-close': close_led, 'input-close': close_input}
    while not finished.is_set():
        if time.monotonic() >= deadline:
            emit('idle-timeout-stop')
            os.kill(os.getpid(), signal.SIGINT)
            return
        readable, _, _ = select.select([sys.stdin], [], [], .25)
        if not readable:
            continue
        line = sys.stdin.readline()
        if not line:
            emit('stdin-closed-stop')
            os.kill(os.getpid(), signal.SIGINT)
            return
        command = line.strip()
        deadline = time.monotonic() + 300
        emit('operator-command', command=command)
        if command in actions:
            actions[command].set()
        elif command == 'status':
            status()
        elif command in ('quit', 'stop'):
            os.kill(os.getpid(), signal.SIGINT)
            return
        else:
            emit('unknown-command')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--event', required=True)
    ap.add_argument('--led', required=True)
    ap.add_argument('--donor', required=True)
    ap.add_argument('--serial', default='/dev/serial0')
    args = ap.parse_args()
    bridge.OutputWorker, bridge.LinuxR5Source = DiagnosticWorker, DiagnosticSource
    emit('starting', pid=os.getpid(), bridge_module=bridge.__file__, gain=.10,
         commands=['status', 'ffb-close', 'led-close', 'input-close', 'quit'])
    control = Thread(target=operator_loop, name='release-probe-control', daemon=True)
    control.start()
    try:
        return bridge.run(args.serial, args.donor, press_ps=True, r5_path=args.event,
                          ffb=True, ffb_gain=.10, ffb_invert=False, led_path=args.led)
    finally:
        finished.set()
        control.join(timeout=1)
        emit('finished')


if __name__ == '__main__':
    raise SystemExit(main())
