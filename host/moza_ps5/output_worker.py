# SPDX-License-Identifier: MIT
"""Bounded latest-state output worker, separate from UART and donor signing."""
from __future__ import annotations

from collections import Counter
from queue import SimpleQueue
from threading import Event, Lock, Thread
import time

from .linux_ffb import LinuxConstantForce, LinuxDamperForce, scaled_force, scaled_damper
from .moza_leds import LegacyLedBar
from .wheel_output import WheelCommand, DamperSlots

FORCE_TTL = .15
LED_TTL = 1.0
BRIDGE_TTL = .10
DAMPER_REFRESH = .05


def bounded_dampers(active, gain: float, constant_level: int):
    """Share the remaining force limit across dampers without amplifying clips."""
    remaining = max(0., gain - abs(constant_level) / 32767)
    # Linux saturation is unsigned full scale, constant force is signed full
    # scale. Bound their worst-case sum to the configured total gain.
    total_clip = sum(p.clip * 256 / 65535 for p in active.values())
    damper_gain = min(gain, remaining / total_clip) if total_clip else 0.
    return {bit: scaled_damper(p, damper_gain) for bit, p in active.items()}


class OutputWorker:
    def __init__(self, *, ffb_path=None, led_path=None, gain=.10, invert=False, dry_run=False,
                 damper=False):
        scaled_force(0, gain, invert)  # Validate before opening any output device.
        if dry_run and (ffb_path or led_path):
            raise ValueError("Dry-run mode cannot open hardware output devices")
        if damper and not ffb_path:
            raise ValueError("Damper output requires constant-force output")
        self.gain, self.invert = gain, invert
        self.force = self.leds = None
        self.dampers = {}
        self.damper_state = DamperSlots()
        self.last_damper = {}
        self.applied_dampers = {}
        self.lock = Lock()
        self.shutdown = Event()
        self.thread = None
        self.ready_at = self.force_at = self.led_at = float("-inf")
        self.ready = False
        self.force_value = self.led_mask = 0
        self.counts = Counter()
        self.masks = set()
        self.force_min = self.force_max = 0
        self.applied_force = None
        self.failed = False
        self.messages = SimpleQueue()
        try:
            if ffb_path:
                self.force = LinuxConstantForce(ffb_path)
                if damper:
                    if self.force.slots < 4:
                        raise ValueError("Damper output requires at least four native effect slots")
                    for bit in (1, 2, 4, 8):
                        self.dampers[bit] = LinuxDamperForce(ffb_path)
            if led_path:
                self.leds = LegacyLedBar(led_path)
        except BaseException:
            self._close_devices()
            raise
        if self.force or self.leds:
            self.thread = Thread(target=self._run, name="r5-outputs", daemon=True)
            self.thread.start()

    def set_ready(self, ready: bool, now: float):
        with self.lock:
            stale = self.ready and now - self.ready_at >= BRIDGE_TTL
            self.ready = ready and not self.failed
            self.ready_at = now
            if not self.ready or stale:
                self.force_at = self.led_at = float("-inf")
                self.force_value = self.led_mask = 0
                self.damper_state.clear()

    def invalidate(self):
        self.set_ready(False, time.monotonic())

    def submit(self, command: WheelCommand, now: float):
        with self.lock:
            self.counts[command.kind] += 1
            if not self.ready or self.failed:
                return
            self.damper_state.submit(command, now, FORCE_TTL)
            if command.kind in ("constant", "unsupported-slot-1"):
                self.force_value, self.force_at = command.value, now
                self.force_min = min(self.force_min, command.value)
                self.force_max = max(self.force_max, command.value)
            elif command.slots & 1:
                # A damper/download/play/unknown replacement of Logitech slot
                # zero releases its old constant force independently of damping.
                self.force_value, self.force_at = 0, float("-inf")
            elif command.kind == "leds":
                self.led_mask = command.value if command.value in range(32) else 0
                self.led_at = now
                self.masks.add(command.value)

    def tick(self, now: float):
        with self.lock:
            live = self.ready and not self.failed and now - self.ready_at < BRIDGE_TTL
            level = scaled_force(self.force_value, self.gain, self.invert) if live and now - self.force_at < FORCE_TTL else 0
            deadline = min(self.force_at + FORCE_TTL, self.ready_at + BRIDGE_TTL)
            mask = self.led_mask if live and now - self.led_at < LED_TTL else 0
            self.damper_state.expire(now, FORCE_TTL)
            if not live:
                self.damper_state.clear()
            conditions = bounded_dampers(self.damper_state.active(), self.gain, level)
            damper_deadline = min(self.damper_state.activity_at + FORCE_TTL, self.ready_at + BRIDGE_TTL)
        # Reduce/release old damper caps before increasing constant force. This
        # also frees Logitech slot zero's damper before allocating its constant
        # replacement, keeping the native allocation at at most four effects.
        for bit, device in self.dampers.items():
            condition = conditions.get(bit)
            previous_condition, _ = self.last_damper.get(bit, (None, float("-inf")))
            if device.playing and (condition is None or condition.saturation < previous_condition.saturation):
                self._apply_damper(bit, condition, now, damper_deadline)
        if self.force:
            self.force.apply(level, valid_until=deadline)
            with self.lock:
                previous = self.applied_force
                # A successful expired apply also stops/erases the effect.
                self.applied_force = level if getattr(self.force, "playing", True) else 0
            if previous not in (None, 0) and self.applied_force == 0:
                self.messages.put("R5 force released: playback stopped and uploaded effect erased")
        for bit, device in self.dampers.items():
            condition = conditions.get(bit)
            previous_condition, previous_at = self.last_damper.get(bit, (None, float("-inf")))
            if condition is None or condition != previous_condition or now - previous_at >= DAMPER_REFRESH:
                self._apply_damper(bit, condition, now, damper_deadline)
        with self.lock:
            previous_dampers = self.applied_dampers
            self.applied_dampers = {bit: device.effect_id for bit, device in self.dampers.items() if device.playing}
        if previous_dampers and not self.applied_dampers:
            self.messages.put("R5 dampers released: playback stopped and uploaded effects erased")
        if self.leds and (not hasattr(self, "last_led_at") or now - self.last_led_at >= .05):
            self.leds.apply(mask)
            self.last_led_at = now

    def _apply_damper(self, bit, condition, now, deadline):
        self.dampers[bit].apply(condition, valid_until=deadline)
        if condition is None:
            self.last_damper.pop(bit, None)
        else:
            self.last_damper[bit] = condition, now

    def summary(self) -> str:
        with self.lock:
            return (f"R5 outputs ready={self.ready and not self.failed} counts={dict(self.counts)} "
                    f"constant_range={self.force_min}..{self.force_max} "
                    f"led_masks={[f'{mask:02x}' for mask in sorted(self.masks)]} "
                    f"gain={self.gain:.2f} failed={self.failed} "
                    f"applied_force={self.applied_force} "
                    f"force_effect_id={getattr(self.force, 'effect_id', None)} "
                    f"damper_enabled={bool(self.dampers)} damper_effect_ids={self.applied_dampers} "
                    f"damper_slots={ {bit: (p.left, p.right, p.clip) for bit, p in self.damper_state.active().items()} }")

    def _close_devices(self):
        for device in (self.force, *self.dampers.values(), self.leds):
            if device:
                try:
                    device.close()
                except Exception as exc:
                    self.messages.put(f"R5 output cleanup: {exc}")

    def _run(self):
        try:
            while not self.shutdown.wait(.01):
                self.tick(time.monotonic())
        except Exception as exc:
            with self.lock:
                self.failed = True
                self.ready = False
            self.messages.put(f"R5 output worker stopped: {exc}")
        finally:
            self._close_devices()

    def close(self):
        self.invalidate()
        self.shutdown.set()
        if self.thread:
            self.thread.join(timeout=1)
            if self.thread.is_alive():
                self.messages.put("R5 worker has not exited; its last force effect was requested with a finite 150 ms duration")
