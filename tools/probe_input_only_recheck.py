#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Operator-controlled input forwarding and output decoding; no R5 output handles."""
import argparse
import os
from threading import Thread

import probe_damper_session as session


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--event', required=True)
    ap.add_argument('--donor', required=True)
    args = ap.parse_args()
    session.emit('starting', pid=os.getpid(), input_forwarding=True,
                 r5_force_output=False, r5_led_output=False, outputs_dry_run=True,
                 module=session.bridge.__file__)
    control = Thread(target=session.operator_loop, name='input-recheck-operator', daemon=True)
    control.start()
    try:
        return session.bridge.run('/dev/serial0', args.donor, press_ps=True,
                                  r5_path=args.event, outputs_dry_run=True)
    finally:
        session.finished.set()
        control.join(timeout=1)
        session.emit('graceful-finish')


if __name__ == '__main__':
    raise SystemExit(main())
