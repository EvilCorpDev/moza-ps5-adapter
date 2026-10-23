#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Two left green LEDs indicate a currently playing native damper for unplug."""
import probe_damper_session as session

devices = []


class CueDamper(session.DamperWitness):
    def __init__(self, path):
        super().__init__(path)
        devices.append(self)


class CueBar(session.output_worker.LegacyLedBar):
    def apply(self, mask):
        active = [d for d in devices if d.fd is not None and d.playing]
        cue_on = bool(active)
        super().apply(1 if cue_on else 0)
        if cue_on != getattr(self, 'previous_cue', None):
            self.previous_cue = cue_on
            session.emit('unplug-cue', cue_on=cue_on,
                         active_native_effects=[{'fd': d.fd, 'effect_id': d.effect_id} for d in active])


if __name__ == '__main__':
    session.DamperWitness = CueDamper
    session.output_worker.LegacyLedBar = CueBar
    raise SystemExit(session.main())
