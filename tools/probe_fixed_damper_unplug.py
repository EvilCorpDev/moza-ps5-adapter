#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Noticeable, isolated 10% damper with a green unplug cue; no game torque."""
import probe_damper_session as session
import probe_damper_unplug_cue as cue
from damper_test_support import FixedDamperWorker


if __name__ == '__main__':
    session.bridge.OutputWorker = FixedDamperWorker
    session.DamperWitness = cue.CueDamper
    session.output_worker.LegacyLedBar = cue.CueBar
    session.emit('fixed-damper-mode', gain=.10, coefficient_selectors=[15, 15], clip=255,
                 game_constant_force_muted=True, refresh_requires='accepted PS5 LED output',
                 synthetic_test_parameters=True)
    raise SystemExit(session.main())
