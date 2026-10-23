#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Arm a normal SIGINT shutdown immediately after a fresh nonzero damper play.

Uses the same isolated fixed-10% diagnostic as the successful unplug test.
Game constant force is muted. No production modules or previous probes change.
"""
import math
import os
import select
import signal
from threading import Event
import time

import probe_damper_session as session
import probe_damper_unplug_cue as cue
from damper_test_support import FixedDamperWorker

armed = Event()


class GracefulStopDamper(cue.CueDamper):
    def apply(self, condition, valid_until=None):
        self.witness_condition, self.witness_deadline = condition, valid_until
        super().apply(condition, valid_until=valid_until)

    def _play(self, value):
        super()._play(value)
        condition = getattr(self, 'witness_condition', None)
        deadline = getattr(self, 'witness_deadline', None)
        played_at = time.monotonic()
        if (value != 1 or not armed.is_set() or condition is None
                or condition.saturation <= 0 or not (condition.left_coeff or condition.right_coeff)
                or deadline is None or not math.isfinite(deadline) or played_at >= deadline):
            return
        armed.clear()
        # Send SIGINT before logging: a slow log write cannot delay this request
        # past the accepted force deadline. The main bridge performs normal cleanup.
        requested_at = time.monotonic()
        if requested_at >= deadline:
            armed.set()
            return
        os.kill(os.getpid(), signal.SIGINT)
        session.emit('graceful-stop-after-nonzero-damper-play', pid=os.getpid(),
                     native_play_write_completed=True, effect_id=self.effect_id,
                     left_coeff=condition.left_coeff, right_coeff=condition.right_coeff,
                     saturation=condition.saturation, played_at=played_at,
                     signal_requested_at=requested_at, valid_until=deadline,
                     requested_duration_ms=150, signal='SIGINT')

    def close(self):
        before = {'fd': self.fd, 'effect_id': self.effect_id, 'playing': self.playing}
        super().close()
        session.emit('native-close-completed', before=before, fd=self.fd,
                     effect_id=self.effect_id, playing=self.playing)


def operator_loop():
    deadline = time.monotonic() + 300
    while not session.finished.is_set():
        if time.monotonic() >= deadline:
            session.emit('idle-timeout-stop')
            os.kill(os.getpid(), signal.SIGINT)
            return
        readable, _, _ = select.select([session.sys.stdin], [], [], .25)
        if not readable:
            continue
        line = session.sys.stdin.readline()
        if not line:
            session.emit('stdin-closed-stop')
            os.kill(os.getpid(), signal.SIGINT)
            return
        command = line.strip()
        deadline = time.monotonic() + 300
        session.emit('operator-command', command=command)
        if command == 'arm-stop':
            armed.set()
            session.emit('armed-waiting-for-nonzero-damper-play')
        elif command == 'disarm':
            armed.clear()
        elif command in ('quit', 'stop'):
            os.kill(os.getpid(), signal.SIGINT)
            return


def main():
    session.bridge.OutputWorker = FixedDamperWorker
    session.DamperWitness = GracefulStopDamper
    session.output_worker.LegacyLedBar = cue.CueBar
    session.operator_loop = operator_loop
    session.emit('fixed-damper-mode', gain=.10, coefficient_selectors=[15, 15], clip=255,
                 game_constant_force_muted=True, refresh_requires='accepted PS5 LED output',
                 synthetic_test_parameters=True, armed=False,
                 stop_trigger='arm-stop then successful fresh nonzero native play')
    return session.main()


if __name__ == '__main__':
    raise SystemExit(main())
