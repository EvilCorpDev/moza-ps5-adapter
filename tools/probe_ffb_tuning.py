#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Ordinary ACC force/damper/LED output at one explicitly selected tuning gain."""
import argparse
import os
from threading import Thread

import probe_damper_session as session

GAINS = (.10, .15, .20, .25, .30)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--gain', required=True, type=float, choices=GAINS)
    ap.add_argument('--event', required=True)
    ap.add_argument('--led', required=True)
    ap.add_argument('--donor', required=True)
    args = ap.parse_args()
    session.output_worker.LinuxDamperForce = session.DamperWitness
    session.emit('starting', pid=os.getpid(), gain=args.gain, damper=True,
                 module=session.bridge.__file__, normal_game_force=True,
                 synthetic_test_parameters=False, game_leds=True,
                 idle_timeout_seconds=300)
    control = Thread(target=session.operator_loop, name='tuning-operator', daemon=True)
    control.start()
    try:
        return session.bridge.run('/dev/serial0', args.donor, press_ps=True,
                                  r5_path=args.event, ffb=True, ffb_gain=args.gain,
                                  ffb_invert=False, ffb_damper=True, led_path=args.led)
    finally:
        session.finished.set()
        control.join(timeout=1)
        session.emit('graceful-finish')


if __name__ == '__main__':
    raise SystemExit(main())
