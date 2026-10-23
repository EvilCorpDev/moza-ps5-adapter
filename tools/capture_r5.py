#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read-only, labelled evdev capture for the Raspberry Pi R5 input milestone.

Standard library only. Device reads and EVIOCG* queries; no device writes,
EVIOCGRAB, calibration changes, or force-feedback requests.
Linux UAPI reference: include/uapi/linux/input.h and asm-generic/ioctl.h.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import select
import struct
import sys
import time

R5_VENDOR, R5_PRODUCT = 0x346E, 0x0004
EVENT_FORMAT = "@llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
CAPTURE_SCHEMA = "moza-r5-evdev-capture-v1"
GENERIC_IOCTL_MACHINES = {
    "aarch64", "arm64", "armv6l", "armv7l", "armv8l",
    "x86_64", "amd64", "i386", "i486", "i586", "i686",
}


def read_request(number: int, size: int) -> int:
    """Linux asm-generic _IOC(_IOC_READ, 'E', number, size)."""
    return (2 << 30) | (size << 16) | (ord("E") << 8) | number


def bit_codes(data: bytes) -> list[int]:
    return [index for index in range(len(data) * 8) if data[index // 8] & (1 << (index % 8))]


def capabilities(fd: int, ioctl=None) -> dict:
    if ioctl is None:
        import fcntl
        ioctl = fcntl.ioctl

    def query(number: int, size: int) -> bytes:
        buffer = bytearray(size)
        ioctl(fd, read_request(number, size), buffer, True)
        return bytes(buffer)

    bus, vendor, product, version = struct.unpack("@HHHH", query(0x02, 8))
    if (vendor, product) != (R5_VENDOR, R5_PRODUCT):
        raise ValueError(f"Expected R5 346e:0004; selected device is {vendor:04x}:{product:04x}")
    name = query(0x06, 256).split(b"\0", 1)[0].decode("utf-8", errors="replace")
    event_types = bit_codes(query(0x20, 4))
    axes = {}
    for code in bit_codes(query(0x23, 8)) if 3 in event_types else []:
        values = struct.unpack("@iiiiii", query(0x40 + code, 24))
        axes[str(code)] = dict(zip(("value", "minimum", "maximum", "fuzz", "flat", "resolution"), values))
    keys = bit_codes(query(0x21, 96)) if 1 in event_types else []
    pressed = bit_codes(query(0x18, 96)) if keys else []
    relative = bit_codes(query(0x22, 2)) if 2 in event_types else []
    return {
        "name": name, "bus": bus, "vid": f"{vendor:04x}", "pid": f"{product:04x}",
        "device_version": version, "event_types": event_types,
        "absolute_axes": axes, "key_codes": keys, "pressed_keys": pressed,
        "relative_codes": relative,
    }


class EventDecoder:
    def __init__(self, event_format: str = EVENT_FORMAT):
        self.event_format = event_format
        self.size = struct.calcsize(event_format)
        self.pending = bytearray()

    def feed(self, data: bytes) -> list[list[int]]:
        self.pending.extend(data)
        result = []
        while len(self.pending) >= self.size:
            result.append(list(struct.unpack_from(self.event_format, self.pending)))
            del self.pending[:self.size]
        return result


def discard_queued(fd: int) -> None:
    # Bound draining so continuously changing input cannot stall capture startup.
    for _ in range(32):
        try:
            data = os.read(fd, EVENT_SIZE * 128)
        except BlockingIOError:
            return
        if not data:
            raise OSError("Input device disconnected before capture")
    raise OSError("Could not drain queued input; leave controls still and retry")


def capture_window(fd: int, seconds: float, phase: dict, *, clock=time.monotonic,
                   wait=select.select, read=os.read, event_format=EVENT_FORMAT) -> None:
    decoder = EventDecoder(event_format)
    deadline = clock() + seconds
    phase["status"] = "recording"
    phase["events"] = []
    phase["syn_dropped"] = 0
    try:
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            readable, _, _ = wait([fd], [], [], min(remaining, .1))
            if not readable:
                continue
            try:
                data = read(fd, decoder.size * 128)
            except BlockingIOError:
                continue
            if not data:
                raise OSError("Input device disconnected during capture")
            events = decoder.feed(data)
            phase["events"].extend(events)
            phase["syn_dropped"] += sum(event[2:4] == [0, 3] for event in events)
        if decoder.pending:
            raise OSError("Capture ended with a partial input_event")
    except BaseException:
        phase["status"] = "incomplete"
        raise
    phase["status"] = "complete" if phase["syn_dropped"] == 0 else "repeat-required"


def event_summary(phase: dict) -> dict:
    controls = {}
    for code, axis in phase.get("before", {}).get("absolute_axes", {}).items():
        controls[f"3:{code}"] = {"initial": axis["value"], "minimum": axis["value"], "maximum": axis["value"], "last": axis["value"], "events": 0}
    for _, _, kind, code, value in phase.get("events", []):
        if kind not in (1, 2, 3):
            continue
        item = controls.setdefault(f"{kind}:{code}", {"minimum": value, "maximum": value, "last": value, "events": 0})
        item["minimum"] = min(item["minimum"], value)
        item["maximum"] = max(item["maximum"], value)
        item["last"] = value
        item["events"] += 1
    return controls


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(path: str, output: Path, *, label: str | None = None, seconds: float = 10) -> int:
    if not sys.platform.startswith("linux") or platform.machine().lower() not in GENERIC_IOCTL_MACHINES:
        raise ValueError("Run on Raspberry Pi Linux (ARM32/ARM64) or Linux x86 with the generic ioctl ABI")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        device = capabilities(fd)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create: a repeated run cannot overwrite an earlier capture.
        with output.open("x", encoding="utf-8") as stream:
            document = {
                "schema": CAPTURE_SCHEMA, "started_utc": utc_now(), "path": path,
                "system": {"machine": platform.machine(), "kernel": platform.release(), "python": platform.python_version(), "long_bytes": struct.calcsize("l"), "input_event_bytes": EVENT_SIZE},
                "event_columns": ["seconds", "microseconds", "type", "code", "signed_value"],
                "device": device, "status": "recording", "phases": [],
            }

            def save() -> None:
                stream.seek(0)
                json.dump(document, stream, indent=2)
                stream.write("\n")
                stream.truncate()
                stream.flush()

            def record(name: str, duration: float) -> None:
                discard_queued(fd)
                phase = {"label": name, "seconds_requested": duration, "started_utc": utc_now(), "before": capabilities(fd)}
                document["phases"].append(phase)
                print(f"RECORDING {name}: {duration:g} seconds. Move only that control now.", flush=True)
                try:
                    capture_window(fd, duration, phase)
                    phase["after"] = capabilities(fd)
                finally:
                    phase["finished_utc"] = utc_now()
                    phase["observed_controls"] = event_summary(phase)
                    save()
                print(f"Saved {len(phase['events'])} events ({phase['status']}).", flush=True)

            print(f"Device: {device['name']} ({device['vid']}:{device['pid']}) at {path}")
            print(f"Output: {output.resolve()}", flush=True)
            save()
            try:
                if label is not None:
                    record(label, seconds)
                else:
                    print("Keep steering centered, pedals released, and buttons released between phases.")
                    phases = [
                        ("neutral", "Keep everything at rest", 3),
                        ("steering", "Slowly turn left, right, then return to center", 15),
                        ("throttle", "Fully press/release only the throttle a few times", 10),
                        ("brake", "Fully press/release only the brake a few times", 10),
                        ("clutch", "Fully press/release only the clutch, if fitted", 10),
                    ]
                    for name, instruction, duration in phases:
                        answer = input(f"\n{name}: {instruction}. Enter to start, or s to skip: ").strip().lower()
                        if answer == "s":
                            document["phases"].append({"label": name, "status": "skipped"})
                            save()
                            continue
                        record(name, duration)
                    print("\nCapture paddles, D-pad directions, and buttons separately using their physical labels.")
                    while True:
                        name = input("Button/control name (for example left paddle, A, D-pad up); blank finishes: ").strip()
                        if not name:
                            break
                        record("button:" + name, 5)
                document["status"] = "complete"
                if any(p.get("status") == "repeat-required" for p in document["phases"]):
                    document["status"] = "repeat-required"
            except (KeyboardInterrupt, EOFError):
                document["status"] = "interrupted"
            except OSError as exc:
                document["status"] = "error"
                document["error"] = str(exc)
            finally:
                document["finished_utc"] = utc_now()
                save()
            print(f"Capture {document['status']}; saved {output.resolve()}", flush=True)
            return 0 if document["status"] == "complete" else 1
    finally:
        os.close(fd)


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise argparse.ArgumentTypeError("seconds must be finite and between 0 and 60")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="R5 evdev path, currently /dev/input/event5")
    parser.add_argument("--output", type=Path, required=True, help="new JSON output file (existing files are preserved)")
    parser.add_argument("--label", help="record one labelled phase instead of the interactive guide")
    parser.add_argument("--seconds", type=positive_seconds, default=10, help="duration for --label; 0 < seconds <= 60")
    args = parser.parse_args(argv)
    try:
        return run(args.path, args.output, label=args.label, seconds=args.seconds)
    except (OSError, ValueError) as exc:
        print(f"Capture could not start: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
