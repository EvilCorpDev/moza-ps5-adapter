#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Operator-controlled ACC comparison using the production bridge at fixed 10%."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import select
import signal
import sys
from threading import Event, Thread
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from moza_ps5 import bridge, output_worker
from moza_ps5.linux_ffb import LinuxDamperForce

finished = Event()


def emit(action, **fields):
    record = {'utc': datetime.now(timezone.utc).isoformat(), 'monotonic': time.monotonic(),
              'damper_probe': action, **fields}
    os.write(1, ('\n' + json.dumps(record) + '\n').encode())


class DamperWitness(LinuxDamperForce):
    def apply(self, condition, valid_until=None):
        super().apply(condition, valid_until=valid_until)
        state = (self.playing, self.effect_id, condition if self.playing else None)
        if state != getattr(self, 'previous_witness', None):
            self.previous_witness = state
            emit('native-state', fd=self.fd, playing=self.playing, effect_id=self.effect_id,
                 left_coeff=condition.left_coeff if self.playing else 0,
                 right_coeff=condition.right_coeff if self.playing else 0,
                 saturation=condition.saturation if self.playing else 0,
                 native_call_completed=True, requested_duration_ms=150,
                 valid_until=valid_until if valid_until is not None and math.isfinite(valid_until) else None)


def operator_loop():
    deadline = time.monotonic() + 300
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
        if command in ('quit', 'stop'):
            os.kill(os.getpid(), signal.SIGINT)
            return


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--event', required=True)
    ap.add_argument('--led', required=True)
    ap.add_argument('--donor', required=True)
    ap.add_argument('--damper', action='store_true')
    args = ap.parse_args()
    output_worker.LinuxDamperForce = DamperWitness
    emit('starting', pid=os.getpid(), gain=.10, damper=args.damper, module=bridge.__file__)
    control = Thread(target=operator_loop, name='damper-test-control', daemon=True)
    control.start()
    try:
        return bridge.run('/dev/serial0', args.donor, press_ps=True,
                          r5_path=args.event, ffb=True, ffb_gain=.10,
                          ffb_invert=False, ffb_damper=args.damper, led_path=args.led)
    finally:
        finished.set()
        control.join(timeout=1)
        emit('graceful-finish')


if __name__ == '__main__':
    raise SystemExit(main())
