# SPDX-License-Identifier: MIT
"""Finite-duration Linux constant/damper effects for the verified R5 identity.

Uses the Linux input.h ABI directly. Never changes global gain or calibration.
Damper coefficients use the lower edge of the new-lg4ff quantization bins.
This preserves the encoding, not a measured G29-to-R5 torque equivalence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
import os
import platform
import struct
import sys
import time

from .wheel_output import DamperParameters

FF_CONSTANT = 0x52
FF_DAMPER = 0x55
EFFECT_MS = 150
MAX_FFB_GAIN = .30


def effect_bytes(level: int, effect_id: int = -1, pointer_bytes: int | None = None) -> bytearray:
    pointer_bytes = struct.calcsize("P") if pointer_bytes is None else pointer_bytes
    if pointer_bytes not in (4, 8) or not -32767 <= level <= 32767 or not -1 <= effect_id <= 32767:
        raise ValueError("invalid force-feedback effect")
    # ff_effect.u starts at byte 16 on generic ARM/x86 32-bit and 64-bit ABIs.
    # ff_periodic_effect determines union size: 28/32 bytes including pointer.
    result = bytearray(44 if pointer_bytes == 4 else 48)
    struct.pack_into("<HhH", result, 0, FF_CONSTANT, effect_id, 0xc000)
    struct.pack_into("<H", result, 10, EFFECT_MS)
    struct.pack_into("<h", result, 16, level)
    return result


def scaled_force(value: int, gain: float, invert: bool = False) -> int:
    if not math.isfinite(gain) or not 0 <= gain <= MAX_FFB_GAIN or not -128 <= value <= 127:
        raise ValueError(f"FFB gain must be between 0 and {MAX_FFB_GAIN:.2f} and force between -128 and 127")
    # G29 encodes the horizontal component. Linux's fixed 0xc000 direction
    # reverses the signed level on that axis. Confirmed on this R5 in ACC.
    return round(value / 128 * 32767 * gain) * (1 if invert else -1)


@dataclass(frozen=True)
class DamperCondition:
    left_coeff: int
    right_coeff: int
    saturation: int

    def __post_init__(self):
        if (not -32767 <= self.left_coeff <= 32767
                or not -32767 <= self.right_coeff <= 32767
                or not 0 <= self.saturation <= 65535):
            raise ValueError("invalid native damper condition")


def scaled_damper(parameters: DamperParameters, gain: float) -> DamperCondition:
    scaled_force(0, gain)  # Share the finite gain constraint with constant force.
    # lg4ff encodes abs(coeff) >> 11 and saturation >> 8. Use the lower
    # edge of each bin; do not turn ACC's captured CLIP=1 into full force.
    return DamperCondition(int(parameters.left * 2048 * gain),
                           int(parameters.right * 2048 * gain),
                           int(parameters.clip * 256 * gain))


def damper_bytes(condition: DamperCondition, effect_id: int = -1,
                 pointer_bytes: int | None = None) -> bytearray:
    result = effect_bytes(0, effect_id, pointer_bytes)
    # MOZA needs the conditional direction 0x4000 (Linux PIDFF's MOZA quirk),
    # independent of the physically verified constant-force inversion.
    struct.pack_into("<HhH", result, 0, FF_DAMPER, effect_id, 0x4000)
    struct.pack_into("<HHhhHh", result, 16, condition.saturation, condition.saturation,
                     condition.right_coeff, condition.left_coeff, 0, 0)
    # condition[1] is zero: only the steering/X axis has damping.
    return result


class LinuxConstantForce:
    effect_type = FF_CONSTANT

    def __init__(self, path: str):
        if sys.platform != "linux" or sys.byteorder != "little" or platform.machine().lower() not in {
            "aarch64", "arm64", "armv6l", "armv7l", "armv8l", "x86_64", "amd64",
            "i386", "i486", "i586", "i686",
        }:
            raise ValueError("R5 FFB requires a supported little-endian Linux input ABI")
        import fcntl
        self.ioctl = fcntl.ioctl
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.effect_id = -1
        self.playing = False
        try:
            _, vid, pid, _ = struct.unpack("@HHHH", self._query(2, 8))
            if (vid, pid) != (0x346e, 4):
                raise ValueError("FFB output is restricted to MOZA R5 346e:0004")
            bits = self._query(0x35, 16)
            self.slots = struct.unpack("@i", self._query(0x84, 4))[0]
            if not bits[self.effect_type // 8] & (1 << (self.effect_type % 8)) or self.slots < 1:
                raise ValueError(f"R5 does not expose force type {self.effect_type:#x} with an available effect slot")
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def _query(self, number: int, size: int) -> bytes:
        data = bytearray(size)
        self.ioctl(self.fd, (2 << 30) | (size << 16) | (ord("E") << 8) | number, data, True)
        return bytes(data)

    def _play(self, value: int):
        event = struct.pack("@llHHi", 0, 0, 21, self.effect_id, value)
        if os.write(self.fd, event) != len(event):
            raise OSError("short R5 force-feedback event write")

    def apply(self, level: int, valid_until: float | None = None):
        if level == 0 or (valid_until is not None and time.monotonic() >= valid_until):
            self.stop()
            return
        data = effect_bytes(level, self.effect_id)
        self._apply_effect(data, valid_until)

    def _apply_effect(self, data: bytearray, valid_until: float | None):
        request = (1 << 30) | (len(data) << 16) | (ord("E") << 8) | 0x80
        self.ioctl(self.fd, request, data, True)
        assigned = struct.unpack_from("<h", data, 2)[0]
        if not 0 <= assigned < self.slots:
            raise OSError("invalid R5 force-feedback effect ID returned by the driver")
        self.effect_id = assigned
        if valid_until is not None and time.monotonic() >= valid_until:
            self.stop()
            return
        self._play(1)  # Restart the finite 150 ms effect, even for a held level.
        self.playing = True

    def stop(self):
        if self.fd is None or self.effect_id < 0:
            return
        # Release and free only our own effect. Both stop and erase are used
        # by the verified close/disconnect path; the earlier suspected held
        # force was not reproduced in the 2026-10-20 physical retest.
        try:
            if self.playing:
                self._play(0)
        finally:
            self.ioctl(self.fd, (1 << 30) | (4 << 16) | (ord("E") << 8) | 0x81, self.effect_id)
            self.effect_id = -1
            self.playing = False

    def close(self):
        if self.fd is None:
            return
        try:
            self.stop()
        finally:
            os.close(self.fd)
            self.fd = None


class LinuxDamperForce(LinuxConstantForce):
    """One owned, finite FF_DAMPER effect with independent stop/erase."""
    effect_type = FF_DAMPER

    def apply(self, condition: DamperCondition | None, valid_until: float | None = None):
        if (condition is None or condition.saturation == 0
                or (condition.left_coeff == 0 and condition.right_coeff == 0)
                or (valid_until is not None and time.monotonic() >= valid_until)):
            self.stop()
            return
        self._apply_effect(damper_bytes(condition, self.effect_id), valid_until)
