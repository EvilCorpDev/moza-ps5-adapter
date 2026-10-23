#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""At operator request, SIGKILL the bridge after its next nonzero play write.

Uses the unchanged production bridge at 10% and its accepted polarity. The
witness logs a successful native EV_FF play write immediately before SIGKILL.
No test torque is injected: only accepted live game force can trigger it.
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
from moza_ps5 import bridge, output_worker
from moza_ps5.linux_ffb import EFFECT_MS, LinuxConstantForce

armed, finished = Event(), Event()


def emit(action, **fields):
    record = {'utc': datetime.now(timezone.utc).isoformat(),
              'monotonic': time.monotonic(), 'kill_probe': action, **fields}
    # A single short write preserves this witness among the bridge's log lines.
    os.write(1, ('\n' + json.dumps(record) + '\n').encode())


class KillWitness(LinuxConstantForce):
    def apply(self, level, valid_until=None):
        self.witness_level = level
        self.witness_deadline = valid_until
        super().apply(level, valid_until=valid_until)

    def _play(self, value):
        super()._play(value)
        if value == 1 and self.witness_level != 0 and armed.is_set():
            emit('sigkill-after-nonzero-play', pid=os.getpid(), signal=9,
                 effect_id=self.effect_id, level=self.witness_level,
                 requested_duration_ms=EFFECT_MS, native_play_write_completed=True,
                 valid_until=self.witness_deadline)
            os.kill(os.getpid(), signal.SIGKILL)


def operator_loop():
    deadline = time.monotonic() + 300
    while not finished.is_set():
        if time.monotonic() >= deadline:
            emit('idle-timeout-graceful-stop')
            os.kill(os.getpid(), signal.SIGINT)
            return
        readable, _, _ = select.select([sys.stdin], [], [], .25)
        if not readable:
            continue
        line = sys.stdin.readline()
        if not line:
            emit('stdin-closed-graceful-stop')
            os.kill(os.getpid(), signal.SIGINT)
            return
        command = line.strip()
        deadline = time.monotonic() + 300
        emit('operator-command', command=command)
        if command == 'arm-kill':
            armed.set()
            emit('armed-waiting-for-nonzero-play')
        elif command == 'disarm':
            armed.clear()
        elif command == 'status':
            emit('status', armed=armed.is_set())
        elif command in ('quit', 'stop'):
            os.kill(os.getpid(), signal.SIGINT)
            return


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--event', required=True)
    ap.add_argument('--led', required=True)
    ap.add_argument('--donor', required=True)
    args = ap.parse_args()
    output_worker.LinuxConstantForce = KillWitness
    emit('starting', pid=os.getpid(), gain=.10, armed=False,
         bridge_module=bridge.__file__, commands=['status', 'arm-kill', 'disarm', 'quit'])
    control = Thread(target=operator_loop, name='kill-probe-control', daemon=True)
    control.start()
    try:
        return bridge.run('/dev/serial0', args.donor, press_ps=True,
                          r5_path=args.event, ffb=True, ffb_gain=.10,
                          ffb_invert=False, led_path=args.led)
    finally:
        finished.set()
        control.join(timeout=1)
        emit('graceful-finish')  # Must be absent from a successful SIGKILL test.


if __name__ == '__main__':
    raise SystemExit(main())
