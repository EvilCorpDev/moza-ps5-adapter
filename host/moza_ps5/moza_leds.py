# SPDX-License-Identifier: MIT
"""MOZA serial LED protocol; queries and LED telemetry only, no torque commands.

Protocol facts: pinned Boxflat serial.yml and moza-rev references in
research/outputs/sources.json. Settings are read before choosing a wheel mode.
"""
from __future__ import annotations

from dataclasses import dataclass
import time


def frame(group: int, device: int, command: bytes, payload: bytes) -> bytes:
    body = bytes((group, device)) + command + payload
    if not command or len(command) + len(payload) > 64:
        raise ValueError("invalid MOZA frame length")
    header = bytes((0x7e, len(command) + len(payload)))
    # Body 0x7e bytes are doubled and both copies contribute to the checksum.
    checksum = (13 + sum(header) + sum(body) + 0x7e * body.count(0x7e)) & 255
    return header + b"".join(bytes((b, b)) if b == 0x7e else bytes((b,))
                            for b in body + bytes((checksum,)))


@dataclass(frozen=True)
class SerialFrame:
    group: int
    device: int
    payload: bytes


class FrameParser:
    def __init__(self):
        self.pending = bytearray()
        self.errors = 0

    def feed(self, data: bytes) -> list[SerialFrame]:
        self.pending.extend(data)
        if len(self.pending) > 4096:
            del self.pending[:-512]
            self.errors += 1
        result = []
        while self.pending:
            try:
                start = self.pending.index(0x7e)
            except ValueError:
                self.pending.clear()
                break
            del self.pending[:start]
            if len(self.pending) < 2:
                break
            n = self.pending[1]
            if not 0 <= n <= 64:
                del self.pending[0]
                self.errors += 1
                continue
            decoded, at, bad = bytearray(), 2, False
            while len(decoded) < n + 3:
                if at >= len(self.pending):
                    break
                value = self.pending[at]
                if value == 0x7e:
                    if at + 1 >= len(self.pending):
                        break
                    if self.pending[at + 1] != 0x7e:
                        bad = True
                        break
                    at += 1
                decoded.append(value)
                at += 1
            if bad:
                del self.pending[0]
                self.errors += 1
                continue
            if len(decoded) != n + 3:
                break
            expected = (13 + 0x7e + n + sum(decoded[:-1]) +
                        0x7e * decoded[:-1].count(0x7e)) & 255
            if decoded[-1] != expected:
                del self.pending[0]
                self.errors += 1
                continue
            del self.pending[:at]
            result.append(SerialFrame(decoded[0], decoded[1], bytes(decoded[2:-1])))
        return result


def verified_r5_serial(path: str) -> str:
    """Accept only a tty interface of the expected MOZA R5 USB device."""
    import sys
    from pathlib import Path
    if sys.platform != "linux":
        raise ValueError("MOZA serial output requires Linux")
    resolved = Path(path).resolve()
    if resolved.parent != Path("/dev") or not resolved.name.startswith("ttyACM"):
        raise ValueError("Select the R5 USB CDC port, not the Pi/Pico UART")
    device = Path("/sys/class/tty", resolved.name, "device").resolve()
    for parent in (device, *device.parents):
        if not (parent / "idVendor").exists():
            continue
        vid = (parent / "idVendor").read_text().strip().lower()
        pid = (parent / "idProduct").read_text().strip().lower()
        if (vid, pid) != ("346e", "0004"):
            raise ValueError(f"Expected R5 serial USB 346e:0004, got {vid}:{pid}")
        return str(parent)
    raise ValueError("Could not verify the serial port's R5 USB identity")


class LedSerial:
    def __init__(self, path: str):
        import serial
        self.usb_parent = verified_r5_serial(path)
        self.port = serial.Serial(path, 115200, timeout=.02, write_timeout=.1,
                                  xonxoff=False, rtscts=False, dsrdtr=False,
                                  exclusive=False)
        self.parser = FrameParser()
        self.port.reset_input_buffer()

    def query(self, device: int, command: bytes, size: int, *, timeout=.6, group=0x40) -> tuple[bytes | None, dict]:
        # Boxflat's read marker is one in the last byte. Array reads use zeros.
        payload = bytes(size - 1) + b"\1" if size != 10 else bytes(size)
        request = frame(group, device, command, payload)
        self.port.write(request)
        deadline = time.monotonic() + timeout
        responses, value, raw = [], None, bytearray()
        while time.monotonic() < deadline:
            data = self.port.read(256)
            raw.extend(data)
            for reply in self.parser.feed(data):
                responses.append({"group": reply.group, "device": reply.device,
                                  "payload_hex": reply.payload.hex()})
                # Response addresses swap nibbles. Some ES wheels reply via base.
                swapped = ((reply.device & 15) << 4) | (reply.device >> 4)
                if (reply.group == group | 0x80 and swapped in (0x12, 0x13, device)
                        and reply.payload.startswith(command)
                        and len(reply.payload) == len(command) + size):
                    value = reply.payload[len(command):]
            if value is not None:
                break
        return value, {"request_hex": request.hex(), "responses": responses,
                       "raw_response_hex": raw.hex(), "parser_errors": self.parser.errors,
                       "value_hex": value.hex() if value is not None else None}

    def settings(self) -> dict:
        result = {}
        for device in (0x17, 0x15, 0x13):
            result[str(device)] = {}
            for name, cmd, size in (("indicator_mode", b"\4", 1),
                                    ("rpm_timings", b"\2", 10),
                                    ("display_mode", b"\10", 1),
                                    ("telemetry_mode", b"\34\0", 1),
                                    ("idle_effect", b"\35\0", 1)):
                _, record = self.query(device, cmd, size)
                result[str(device)][name] = record
        return result

    def close(self):
        self.port.close()


def legacy_led_mask(mask: int) -> int:
    """Expand each of the G29's five LEDs to its corresponding ESX pair.

    Literal masks were verified by a stationary physical sweep on this rig.
    RPM threshold percentages are configuration, not this command's payload.
    Preserve sparse patterns as well as the game's progressive bars.
    """
    if isinstance(mask, bool) or not isinstance(mask, int) or not 0 <= mask <= 31:
        raise ValueError("Expected a five-bit G29 LED mask")
    return sum(3 << (2 * i) for i in range(5) if mask & (1 << i))


def legacy_led_frame(mask: int) -> bytes:
    return frame(0x41, 0x13, b"\xfd\xde", legacy_led_mask(mask).to_bytes(4, "big"))


class LegacyLedBar:
    """The hardware-probed R5/ESX protocol, proxy device 0x13 and LED bitmask.

    Does not change modes, colors, brightness or thresholds. The existing
    indicator mode must already be telemetry (1), as confirmed on this rig.
    Full-bar flashing is rendered by the wheel and is still under investigation.
    """
    def __init__(self, path: str):
        self.link = LedSerial(path)
        try:
            mode, _ = self.link.query(0x13, b"\4", 1)
            if mode != b"\1":
                raise ValueError("ESX LED mode must already be telemetry (1)")
        except BaseException:
            self.link.close()
            raise

    def apply(self, mask: int):
        message = legacy_led_frame(mask)
        if self.link.port.write(message) != len(message):
            raise OSError("short ESX LED serial write")

    def close(self):
        try:
            self.apply(0)
        finally:
            self.link.close()
