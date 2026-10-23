# SPDX-License-Identifier: MIT
"""Decode evidenced G29 output commands and validate the complete UART stream."""
from __future__ import annotations

from dataclasses import dataclass, field
import struct
from .ps_button import V3InputGate
from .protocol import OUTPUT, Packet


@dataclass(frozen=True)
class DamperParameters:
    # Logitech protocol 1.5 table 49: K1/S1, K2/S2, CLIP.
    left: int
    right: int
    clip: int

    def __post_init__(self):
        if (not -15 <= self.left <= 15 or not -15 <= self.right <= 15
                or not 0 <= self.clip <= 255):
            raise ValueError("invalid G29 damper parameters")


@dataclass(frozen=True)
class WheelCommand:
    kind: str
    value: int | None = None
    raw: bytes = b""
    slots: int = 0  # High-nibble bit mask, not a native Linux effect ID.
    operation: int | None = None
    damper: DamperParameters | None = None


def decode_g29(report_id: int, data: bytes) -> WheelCommand:
    # The physical ACC captures are 32-byte OUT transfers with prefix 0x30.
    # Also accept the descriptor's normal report-ID 5 form, never arbitrary HID.
    if report_id == 0 and len(data) == 32 and data[0] in (0x30, 5):
        body = data[1:]
    elif report_id == 5 and len(data) == 31:
        body = data
    else:
        return WheelCommand("unknown", raw=data)
    slots, operation = body[0] >> 4, body[0] & 15
    force_slots = slots if operation in (0, 1, 2, 3, 12) else 0
    if any(body[7:]):
        return WheelCommand("unknown", raw=body, slots=force_slots)
    if body[:2] == b"\xf8\x12" and body[2] <= 31 and not any(body[3:]):
        return WheelCommand("leds", body[2], body)
    # Slot 1 download-and-play variable force: Linux lg4ff uses its first level
    # for constant force. ACC's second level is 0x01, so it must not be decoded
    # as a second independent motor command.
    if body[:2] == b"\x11\x08" and not any(body[4:]):
        return WheelCommand("constant", body[2] - 128, body, 1, 1)
    if slots and operation in (2, 3) and not any(body[1:]):
        return WheelCommand("play" if operation == 2 else "stop", 0, body, slots, operation)
    if slots and operation in (0, 1, 12) and body[1] == 12:
        if body[2] <= 15 and body[4] <= 15 and body[3] <= 1 and body[5] <= 1:
            parameters = DamperParameters(body[2] * (-1 if body[3] else 1),
                                          body[4] * (-1 if body[5] else 1), body[6])
            return WheelCommand("damper", raw=body, slots=slots,
                                operation=operation, damper=parameters)
    # Replacing/refreshing any supported slot with an unknown type must release it.
    if slots and operation in (0, 1, 12):
        return WheelCommand("unsupported-slots", 0, body, slots, operation)
    return WheelCommand("unknown", raw=body, slots=force_slots)


@dataclass
class DamperSlots:
    """Four Logitech slots; retained only while accepted force traffic is fresh.

    Refresh changes an already playing damper, never starts an unknown slot.
    Watchdog expiry forgets downloaded parameters so later traffic cannot revive
    an effect across a lost output stream. LEDs do not renew this watchdog.
    """
    downloaded: dict[int, DamperParameters] = field(default_factory=dict)
    playing: set[int] = field(default_factory=set)
    activity_at: float = float("-inf")

    def clear(self):
        self.downloaded.clear()
        self.playing.clear()
        self.activity_at = float("-inf")

    def expire(self, now: float, ttl: float):
        if now - self.activity_at >= ttl:
            self.clear()

    def submit(self, command: WheelCommand, now: float, ttl: float):
        self.expire(now, ttl)
        slots = command.slots or (1 if command.kind in ("constant", "unsupported-slot-1") else 0)
        for bit in (1, 2, 4, 8):
            if not slots & bit:
                continue
            if command.kind == "damper":
                if command.operation == 12 and bit not in self.playing:
                    self.downloaded.pop(bit, None)
                    continue
                self.downloaded[bit] = command.damper
                if command.operation == 0:
                    self.playing.discard(bit)
                else:
                    self.playing.add(bit)
            elif command.kind == "play":
                if bit in self.downloaded:
                    self.playing.add(bit)
            elif command.kind == "stop":
                self.playing.discard(bit)
            else:
                self.downloaded.pop(bit, None)
                self.playing.discard(bit)
        if command.kind in ("constant", "damper", "play", "stop"):
            self.activity_at = now

    def active(self) -> dict[int, DamperParameters]:
        return {bit: self.downloaded[bit] for bit in sorted(self.playing)}


@dataclass
class FullOutputGate:
    link: V3InputGate = field(default_factory=V3InputGate)
    capable: bool = False
    sequence: int | None = None
    device_ms: int | None = None
    estimated_at: float | None = None
    rejected: int = 0
    reason: str = "waiting for output-capable firmware"

    def reset(self):
        self.link.new_boot()
        self.capable = False
        self.sequence = self.device_ms = self.estimated_at = None

    def event(self, code: int):
        self.link.event(code)
        if code in (1, 2, 4, 5):
            self.sequence = self.device_ms = self.estimated_at = None

    def status(self, payload: bytes, now: float):
        self.link.status(payload, now)
        self.capable = len(payload) >= 76 and bool(int.from_bytes(payload[72:76], "little") & 1)

    def ready(self, now: float) -> bool:
        return self.capable and self.link.can_send(now)

    def accept(self, packet: Packet, now: float) -> WheelCommand | None:
        if not self.ready(now) or packet.kind != OUTPUT:
            self.reason = "output link is not ready"
            return None
        data = packet.payload
        if len(data) < 7 or not 1 <= data[5] <= 64 or len(data) != 6 + data[5]:
            self.rejected += 1
            self.reason = "invalid complete-output payload"
            return None
        device_ms, report_id, _ = struct.unpack_from("<IBB", data)
        continuous = self.sequence is None or packet.token == (self.sequence + 1) & 0xffffffff
        self.sequence = packet.token
        if self.device_ms is None:
            self.estimated_at = now
        else:
            delta = (device_ms - self.device_ms) & 0xffffffff
            if delta >= 0x80000000:
                continuous = False
                self.estimated_at = now
            else:
                self.estimated_at = min(now, self.estimated_at + delta / 1000)
        self.device_ms = device_ms
        if not continuous or now - self.estimated_at > .05:
            self.rejected += 1
            self.reason = "output sequence gap or queued output older than 50 ms"
            return None
        self.reason = "complete output received"
        return decode_g29(report_id, data[6:])
