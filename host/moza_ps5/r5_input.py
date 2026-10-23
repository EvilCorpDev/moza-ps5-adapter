"""Captured R5 input mapping and read-only Linux evdev source."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import struct
import sys
import time

from .ps_button import neutral_input

BUTTONS = {name: 1 << index for index, name in enumerate((
    "square", "cross", "circle", "triangle", "L1", "R1", "L2", "R2",
    "share", "options", "L3", "R3", "PS", "touchpad",
))}
DEFAULT_MAP = Path(__file__).with_name("profiles") / "r5-esx-srp-lite.json"
EVENT_FORMAT = "@llHHi"
GENERIC_IOCTL_MACHINES = {"aarch64", "arm64", "armv6l", "armv7l", "armv8l", "x86_64", "amd64", "i386", "i486", "i586", "i686"}


def _integer(value, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Invalid {name}: expected integer {low}..{high}")
    return value


@dataclass(frozen=True)
class Axis:
    code: int
    minimum: int
    maximum: int
    released: int | None = None
    pressed: int | None = None
    deadzone: int = 0
    endpoint_deadzone: int = 0

    def convert(self, value: int) -> int:
        if self.released is None:
            low, high = self.minimum + self.endpoint_deadzone, self.maximum - self.endpoint_deadzone
            value = min(high, max(low, value))
            width = high - low
            return ((value - low) * 65535 + width // 2) // width
        direction = 1 if self.pressed > self.released else -1
        width = abs(self.pressed - self.released) - self.deadzone - self.endpoint_deadzone
        amount = min(width, max(0, direction * (value - self.released) - self.deadzone))
        return 65535 - (amount * 65535 + width // 2) // width


@dataclass(frozen=True)
class InputMap:
    name: str
    axes: dict[str, Axis]
    buttons: dict[int, int]
    hat: dict[str, int]

    def payload(self, axes: dict[int, int], pressed: set[int]) -> bytes:
        if any(axis.code not in axes for axis in self.axes.values()):
            return neutral_input()
        values = [self.axes[name].convert(axes[self.axes[name].code]) for name in ("steering", "throttle", "brake", "clutch")]
        buttons = 0
        for code, mask in self.buttons.items():
            if code in pressed:
                buttons |= mask
        x = int(self.hat["right"] in pressed) - int(self.hat["left"] in pressed)
        y = int(self.hat["down"] in pressed) - int(self.hat["up"] in pressed)
        hat = {(0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3,
               (0, 1): 4, (-1, 1): 5, (-1, 0): 6, (-1, -1): 7, (0, 0): 8}[x, y]
        return struct.pack("<5H2B", *values, buttons, hat, 0)


def load_map(path: str | Path | None = None) -> InputMap:
    data = json.loads(Path(path or DEFAULT_MAP).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "moza-r5-input-map-v1" or data.get("device") != {"vid": "346e", "pid": "0004"}:
        raise ValueError("Expected a moza-r5-input-map-v1 profile for 346e:0004")
    if not isinstance(data.get("axes"), dict) or set(data["axes"]) != {"steering", "throttle", "brake", "clutch"}:
        raise ValueError("Profile must define steering, throttle, brake, and clutch")
    axes = {}
    for name, row in data["axes"].items():
        if not isinstance(row, dict): raise ValueError(f"Invalid {name} axis definition")
        code = _integer(row.get("code"), name + " code", 0, 63)
        minimum = _integer(row.get("minimum"), name + " minimum", -(1 << 31), (1 << 31)-1)
        maximum = _integer(row.get("maximum"), name + " maximum", -(1 << 31), (1 << 31)-1)
        if minimum >= maximum:
            raise ValueError(f"Invalid {name} range")
        endpoint = _integer(row.get("endpoint_deadzone", 0), name + " endpoint_deadzone", 0, maximum-minimum)
        if name == "steering":
            if 2 * endpoint >= maximum - minimum:
                raise ValueError("Steering endpoint zones consume the entire range")
            axes[name] = Axis(code, minimum, maximum, endpoint_deadzone=endpoint)
        else:
            released = _integer(row.get("released"), name + " released", minimum, maximum)
            pressed = _integer(row.get("pressed"), name + " pressed", minimum, maximum)
            deadzone = _integer(row.get("release_deadzone", 0), name + " release_deadzone", 0, maximum-minimum)
            if abs(pressed-released) <= deadzone + endpoint:
                raise ValueError(f"Invalid {name} calibration endpoints")
            axes[name] = Axis(code, minimum, maximum, released, pressed, deadzone, endpoint)
    if len({a.code for a in axes.values()}) != 4:
        raise ValueError("Each physical axis must have a distinct code")
    buttons = {}
    if not isinstance(data.get("buttons"), dict): raise ValueError("Invalid buttons definition")
    for code, target in data.get("buttons", {}).items():
        if not isinstance(code, str) or not code.isdecimal() or not isinstance(target, str) or target not in BUTTONS:
            raise ValueError("Invalid button mapping")
        if int(code) in buttons: raise ValueError("Duplicate normalized button code")
        buttons[_integer(int(code), "button code", 0, 767)] = BUTTONS[target]
    if not isinstance(data.get("hat"), dict) or set(data["hat"]) != {"up", "right", "down", "left"}:
        raise ValueError("Profile must define the four D-pad directions")
    hat = {name: _integer(code, "hat key", 0, 767) for name, code in data["hat"].items()}
    if len(set(hat.values())) != 4 or set(hat.values()) & set(buttons):
        raise ValueError("D-pad keys must be distinct from mapped buttons")
    return InputMap(str(data.get("name", "R5 custom map")), axes, buttons, hat)


def _request(number: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (ord("E") << 8) | number


def _codes(bits: bytes) -> set[int]:
    return {n for n in range(len(bits)*8) if bits[n//8] & (1 << (n % 8))}


class InputState:
    def __init__(self):
        self.axes: dict[int, int] = {}
        self.pressed: set[int] = set()
        self.valid = False
        self.dropping = self.resync = False
        self.syn_dropped = 0
        self.pending_axes: dict[int, int] = {}
        self.pending_keys: dict[int, int] = {}

    def snapshot(self, axes: dict[int, int], pressed: set[int]) -> None:
        self.axes = dict(axes)
        self.pressed = set(pressed)
        self.pending_axes.clear(); self.pending_keys.clear()
        self.valid = True
        self.dropping = self.resync = False

    def event(self, kind: int, code: int, value: int) -> None:
        if kind == 0 and code == 3:  # SYN_DROPPED: discard until next SYN_REPORT, then query state.
            self.valid = False
            self.dropping = True
            self.resync = False
            self.syn_dropped += 1
            self.pending_axes.clear(); self.pending_keys.clear()
        elif self.dropping or self.resync:
            if kind == 0 and code == 0:
                self.dropping = False
                self.resync = True
        elif kind == 3:
            self.pending_axes[code] = value
        elif kind == 1 and value in (0, 1, 2):
            self.pending_keys[code] = value
        elif kind == 0 and code == 0:
            self.axes.update(self.pending_axes)
            for key, state in self.pending_keys.items():
                if state: self.pressed.add(key)
                else: self.pressed.discard(key)
            self.pending_axes.clear(); self.pending_keys.clear()


class LinuxR5Source:
    """Poll read-only evdev; query liveness even when controls are held steady."""
    def __init__(self, path: str, mapping: InputMap):
        if not sys.platform.startswith("linux") or platform.machine().lower() not in GENERIC_IOCTL_MACHINES:
            raise ValueError("R5 forwarding requires Raspberry Pi Linux or generic-ioctl Linux x86")
        self.path = path
        self.mapping = mapping
        self.fd: int | None = None
        self.state = InputState()
        self.buffer = bytearray()
        self.size = struct.calcsize(EVENT_FORMAT)
        self.next_connect = self.next_snapshot = 0.0
        self.messages: list[str] = []
        self.last_problem: str | None = None
        self.frames = 0

    def _query(self, number: int, size: int) -> bytes:
        import fcntl
        buffer = bytearray(size)
        fcntl.ioctl(self.fd, _request(number, size), buffer, True)
        return bytes(buffer)

    def _snapshot(self) -> None:
        axes = {}
        for axis in self.mapping.axes.values():
            value, minimum, maximum, _, _, _ = struct.unpack("@iiiiii", self._query(0x40 + axis.code, 24))
            if (minimum, maximum) != (axis.minimum, axis.maximum):
                raise ValueError(f"Axis {axis.code} range changed: {minimum}..{maximum}")
            axes[axis.code] = value
        self.state.snapshot(axes, _codes(self._query(0x18, 96)))

    def _connect(self) -> None:
        self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        _, vendor, product, _ = struct.unpack("@HHHH", self._query(0x02, 8))
        if (vendor, product) != (0x346e, 0x0004):
            raise ValueError(f"Expected R5 346e:0004; selected device is {vendor:04x}:{product:04x}")
        abs_codes = _codes(self._query(0x23, 8))
        keys = _codes(self._query(0x21, 96))
        if not {a.code for a in self.mapping.axes.values()} <= abs_codes:
            raise ValueError("R5 is missing a configured absolute axis")
        if not (set(self.mapping.buttons) | set(self.mapping.hat.values())) <= keys:
            raise ValueError("R5 is missing a configured button or D-pad key")
        self._snapshot()
        self.last_problem = None
        self.messages.append(f"R5 input connected path={self.path} profile={self.mapping.name}")

    @property
    def live(self) -> bool:
        return self.fd is not None and self.state.valid

    def poll(self, now: float) -> bytes:
        if self.fd is None and now < self.next_connect:
            return neutral_input()
        try:
            if self.fd is None:
                self._connect()
                self.next_snapshot = now + .1
            for _ in range(8):
                try:
                    data = os.read(self.fd, self.size * 128)
                except BlockingIOError:
                    break
                if not data:
                    raise OSError("R5 input device disconnected")
                self.buffer.extend(data)
                while len(self.buffer) >= self.size:
                    _, _, kind, code, value = struct.unpack_from(EVENT_FORMAT, self.buffer)
                    del self.buffer[:self.size]
                    self.state.event(kind, code, value)
                    if kind == 0 and code == 0: self.frames += 1
            if self.state.resync or (now >= self.next_snapshot and not self.state.dropping):
                resync = self.state.resync
                self._snapshot()
                self.next_snapshot = now + .1
                if resync: self.messages.append("R5 input state resynchronized after SYN_DROPPED")
            return self.mapping.payload(self.state.axes, self.state.pressed) if self.live else neutral_input()
        except (OSError, ValueError) as exc:
            problem = str(exc)
            if problem != self.last_problem:
                self.messages.append(f"R5 input unavailable; controls neutral: {problem}")
            self.last_problem = problem
            self.close()
            self.next_connect = now + 1
            return neutral_input()

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.fd = None
        self.state.valid = False
        self.state.axes.clear(); self.state.pressed.clear()
        self.state.pending_axes.clear(); self.state.pending_keys.clear()
        self.state.dropping = self.state.resync = False
        self.buffer.clear()


def input_text(payload: bytes, live: bool, frames: int, syn_dropped: int) -> str:
    steering, throttle, brake, clutch, buttons, hat, _ = struct.unpack("<5H2B", payload)
    return (f"R5 input live={live} steering={steering} throttle={throttle} brake={brake} "
            f"clutch={clutch} buttons=0x{buttons:04x} hat={hat} frames={frames} syn_dropped={syn_dropped}")


def preview(path: str, map_path: str | None = None) -> int:
    source = LinuxR5Source(path, load_map(map_path))
    next_log = 0.0
    try:
        while True:
            now = time.monotonic()
            payload = source.poll(now)
            for message in source.messages: print(message, flush=True)
            source.messages.clear()
            if now >= next_log:
                print(input_text(payload, source.live, source.frames, source.state.syn_dropped), flush=True)
                next_log = now + .5
            time.sleep(.01)
    except KeyboardInterrupt:
        return 0
    finally:
        source.close()
