#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Freeze only after an armed, successful, nonzero native damper play write.

Must run as a child of guard_damper_freeze.py, which kills it while stopped.
No synthetic torque is sent; all production bridge gains stay at 10%.
"""
import argparse
import json
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
from probe_damper_session import emit
import probe_damper_unplug_cue as cue
from damper_test_support import FixedDamperWorker

armed, finished = Event(), Event()
guard_fd = None


class FreezeDamper(LinuxDamperForce):
    def __init__(self, path):
        super().__init__(path)
        cue.devices.append(self)

    def apply(self, condition, valid_until=None):
        self.witness_condition, self.witness_deadline = condition, valid_until
        super().apply(condition, valid_until=valid_until)

    def _play(self, value):
        super()._play(value)
        condition = self.witness_condition
        if (value != 1 or not armed.is_set() or condition is None
                or condition.saturation <= 0 or not (condition.left_coeff or condition.right_coeff)):
            return
        played_at = time.monotonic()
        if self.witness_deadline is None or played_at >= self.witness_deadline:
            return
        armed.clear()
        record = {'pid': os.getpid(), 'native_play_write_completed': True,
                  'effect_id': self.effect_id, 'left_coeff': condition.left_coeff,
                  'right_coeff': condition.right_coeff, 'saturation': condition.saturation,
                  'played_at': played_at, 'valid_until': self.witness_deadline,
                  'requested_duration_ms': 150}
        message = (json.dumps(record) + '\n').encode()
        if os.write(guard_fd, message) != len(message):
            raise OSError('short freeze-guard witness write')
        emit('freeze-after-nonzero-damper-play', **record)
        os.kill(os.getpid(), signal.SIGSTOP)
        raise RuntimeError('Frozen test bridge must be killed, never resumed')


def operator_loop():
    while not finished.is_set():
        readable, _, _ = select.select([sys.stdin], [], [], .25)
        if not readable:
            continue
        line = sys.stdin.readline()
        if not line:
            emit('stdin-closed-stop')
            os.kill(os.getpid(), signal.SIGINT)
            return
        command = line.strip()
        emit('operator-command', command=command)
        if command == 'arm-freeze':
            armed.set()
            emit('armed-waiting-for-nonzero-damper-play')
        elif command == 'disarm':
            armed.clear()
        elif command in ('quit', 'stop'):
            os.kill(os.getpid(), signal.SIGINT)
            return


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--guard-fd', required=True, type=int)
    ap.add_argument('--guardian-pid', required=True, type=int)
    ap.add_argument('--event', required=True)
    ap.add_argument('--led', required=True)
    ap.add_argument('--donor', required=True)
    ap.add_argument('--fixed-damper', action='store_true')
    args = ap.parse_args()
    if os.getppid() != args.guardian_pid:
        raise RuntimeError('Independent freeze guardian is missing')
    global guard_fd
    guard_fd = args.guard_fd
    os.fstat(guard_fd)
    output_worker.LinuxDamperForce = FreezeDamper
    output_worker.LegacyLedBar = cue.CueBar
    if args.fixed_damper:
        bridge.OutputWorker = FixedDamperWorker
    emit('starting', pid=os.getpid(), guardian_pid=args.guardian_pid, gain=.10,
         damper=True, armed=False, fixed_damper=args.fixed_damper,
         game_constant_force_muted=args.fixed_damper, module=bridge.__file__)
    control = Thread(target=operator_loop, name='freeze-operator', daemon=True)
    control.start()
    try:
        return bridge.run('/dev/serial0', args.donor, press_ps=True, r5_path=args.event,
                          ffb=True, ffb_gain=.10, ffb_invert=False, ffb_damper=True,
                          led_path=args.led)
    finally:
        finished.set()
        control.join(timeout=1)
        emit('graceful-finish')


if __name__ == '__main__':
    raise SystemExit(main())
