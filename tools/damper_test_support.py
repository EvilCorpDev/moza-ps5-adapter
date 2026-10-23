# SPDX-License-Identifier: MIT
"""Isolated 10% diagnostic damper, gated by accepted PS5 output traffic.

Only used by test launchers. The production bridge and parser stay unchanged.
"""
from moza_ps5.output_worker import OutputWorker
from moza_ps5.wheel_output import DamperParameters, WheelCommand

TEST_PARAMETERS = DamperParameters(15, 15, 255)


class FixedDamperWorker(OutputWorker):
    def submit(self, command, now):
        if command.kind == 'leds':
            # These LED packets came through the production complete-output
            # sequence/readiness gate. With no new packet, no test force renews.
            super().submit(command, now)
            super().submit(WheelCommand('damper', slots=8, operation=1,
                                        damper=TEST_PARAMETERS), now)
        else:
            # Mute actual game force, including slot-zero constant commands.
            # A fixed positive damper is the only effect this test can play.
            with self.lock:
                self.counts['muted-game-' + command.kind] += 1
