#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read-only R5 output capability probe; no effect uploads or serial writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import sys

FF_NAMES = {0x50: "rumble", 0x51: "periodic", 0x52: "constant", 0x53: "spring",
            0x54: "friction", 0x55: "damper", 0x56: "inertia", 0x57: "ramp",
            0x58: "square", 0x59: "triangle", 0x5a: "sine", 0x5b: "saw_up",
            0x5c: "saw_down", 0x5d: "custom", 0x60: "gain", 0x61: "autocenter"}


def text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def usb_parent(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        if text(parent / "idVendor") and text(parent / "idProduct"):
            return parent
    return None


def probe(event: str) -> dict:
    if sys.platform != "linux" or platform.machine().lower() not in {
        "aarch64", "arm64", "armv6l", "armv7l", "armv8l", "x86_64", "amd64",
        "i386", "i486", "i586", "i686",
    }:
        raise ValueError("This probe requires Linux with the generic ioctl ABI")
    import fcntl
    fd = os.open(event, os.O_RDONLY | os.O_NONBLOCK)
    try:
        def query(number: int, size: int) -> bytes:
            data = bytearray(size)
            fcntl.ioctl(fd, (2 << 30) | (size << 16) | (ord("E") << 8) | number, data, True)
            return bytes(data)
        _, vid, pid, version = struct.unpack("@HHHH", query(2, 8))
        if (vid, pid) != (0x346e, 4):
            raise ValueError(f"Expected MOZA R5 346e:0004, got {vid:04x}:{pid:04x}")
        bits = query(0x20 + 21, 16)
        codes = [i for i in range(128) if bits[i // 8] & (1 << (i % 8))]
        effects = struct.unpack("@i", query(0x84, 4))[0] if codes else 0
        name = query(6, 256).split(b"\0", 1)[0].decode("utf-8", errors="replace")
    finally:
        os.close(fd)
    device = Path("/sys/class/input", Path(event).resolve().name, "device").resolve()
    usb = usb_parent(device)
    hid = next((p for p in (device, *device.parents) if (p / "report_descriptor").exists()), None)
    descriptor = (hid / "report_descriptor").read_bytes() if hid else b""
    ports = []
    for tty in Path("/sys/class/tty").glob("ttyACM*"):
        parent = usb_parent((tty / "device").resolve())
        if parent is not None and parent == usb:
            ports.append({"path": f"/dev/{tty.name}", "sysfs": str(parent),
                          "stable_paths": [str(p) for p in Path("/dev/serial/by-id").glob("*")
                                           if p.resolve() == Path("/dev", tty.name)]})
    return {
        "schema": "moza-r5-output-capabilities-v1", "read_only": True,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "system": {"kernel": platform.release(), "machine": platform.machine(),
                   "python": platform.python_version(), "pointer_bytes": struct.calcsize("P")},
        "event": event, "name": name, "vid": f"{vid:04x}", "pid": f"{pid:04x}",
        "device_version": version, "usb_sysfs": str(usb),
        "usb_serial": text(usb / "serial") if usb else "",
        "ff_codes": codes, "ff_effects": [FF_NAMES.get(i, f"unknown_{i}") for i in codes],
        "effect_slots": effects,
        "hid_driver": (hid / "driver").resolve().name if hid else "",
        "hid_report_descriptor_hex": descriptor.hex(),
        "hid_report_descriptor_sha256": hashlib.sha256(descriptor).hexdigest(),
        "serial_interfaces": ports,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("event", help="R5 event device or stable /dev/input/by-id path")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    result = probe(args.event)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
