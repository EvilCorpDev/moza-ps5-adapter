"""Opt-in PS assignment pulse for the verified v3 wheel report layout."""
from __future__ import annotations
from dataclasses import dataclass
import struct

PS_BUTTON = 1 << 12  # Button 13: v3 payload byte 6 bit 0 (upstream reports.h).
SETTLE_S = 1.0
HOLD_S = 0.5
RELEASE_S = 0.1
INTERVAL_S = 0.02  # Refresh well inside the Pico's 200 ms input watchdog.
STATUS_TIMEOUT_S = 2.0


def v3_ready_status(payload: bytes) -> bool:
    return (len(payload) >= 64 and payload[32] == 3 and payload[33] == 1
            and int.from_bytes(payload[34:36], "little") == 160 and payload[0] & 3 == 3)


@dataclass
class V3InputGate:
    ready: bool = False
    suspended: bool = False
    last_status: float = float("-inf")

    def new_boot(self) -> None:
        self.ready = self.suspended = False
        self.last_status = float("-inf")

    def event(self, code: int) -> None:
        if code in (1, 2): self.new_boot()
        elif code == 4:
            self.suspended = True
            self.ready = False
        elif code == 5:
            self.suspended = False
            self.ready = False

    def status(self, payload: bytes, now: float) -> None:
        self.ready = v3_ready_status(payload)
        self.last_status = now

    def can_send(self, now: float) -> bool:
        return self.ready and not self.suspended and now - self.last_status <= STATUS_TIMEOUT_S


def neutral_input(press_ps: bool = False) -> bytes:
    """12-byte UART INPUT: centered steering, released pedals, PS only, hat neutral."""
    return struct.pack("<5H2B", 0x8000, 0xffff, 0xffff, 0xffff,
                       PS_BUTTON if press_ps else 0, 8, 0)


@dataclass
class PSButtonPulse:
    stage: str = "waiting"
    suspended: bool = False
    ready: bool = False
    deadline: float = 0.0
    last_status: float = float("-inf")
    last_send: float = float("-inf")

    def new_boot(self) -> None:
        self.stage = "waiting"
        self.suspended = False
        self.ready = False
        self.last_status = self.last_send = float("-inf")

    def event(self, code: int) -> None:
        if code in (1, 2):
            self.new_boot()
        elif code == 4:
            self.suspended = True
            # Firmware neutralizes input on suspend; retain completed state.
            if self.stage != "done": self.stage = "waiting"
        elif code == 5:
            self.suspended = False

    def status(self, payload: bytes, now: float) -> bool:
        self.ready = v3_ready_status(payload)
        self.last_status = now
        if not self.ready:
            self.stage = "waiting"
            return False
        if self.stage == "waiting" and not self.suspended:
            self.stage = "delay"
            self.deadline = now + SETTLE_S
            self.last_send = float("-inf")
            return True
        return False

    def tick(self, now: float) -> tuple[bytes, str] | None:
        if not self.ready or self.suspended:
            return None
        if now - self.last_status > STATUS_TIMEOUT_S:
            self.ready = False
            self.stage = "waiting"
            return None
        if self.stage == "delay" and now >= self.deadline:
            self.stage = "press"
            self.deadline = now + HOLD_S
            self.last_send = float("-inf")
        elif self.stage == "press" and now >= self.deadline:
            self.stage = "release"
            self.deadline = now + RELEASE_S
            self.last_send = float("-inf")
        elif self.stage == "release" and now >= self.deadline:
            self.stage = "done"
        if self.stage not in ("press", "release") or now - self.last_send < INTERVAL_S:
            return None
        self.last_send = now
        return neutral_input(self.stage == "press"), self.stage
