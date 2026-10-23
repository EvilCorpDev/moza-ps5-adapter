#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Isolate R5 handle lifecycle changes without playing force effects.

Interactive commands open/close one handle at a time. LED writes are limited
to the known indicator-mode read and the physically verified dark frame.
With --allow-zero-upload, a zero-level effect may be uploaded and erased.
No play event, nonzero effect, gain, autocenter or calibration write is sent.
The native driver may initialize/enable the device on the first upload.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
from moza_ps5 import linux_ffb
from moza_ps5.moza_leds import LedSerial, legacy_led_frame


def emit(action, **fields):
    print(json.dumps({"utc": datetime.now(timezone.utc).isoformat(),
                      "monotonic": time.monotonic(), "action": action, **fields}), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--event", required=True)
    ap.add_argument("--led", required=True)
    ap.add_argument("--idle-timeout", type=float, default=300)
    ap.add_argument("--allow-zero-upload", action="store_true")
    args = ap.parse_args()
    if not 1 <= args.idle_timeout <= 300:
        ap.error("idle timeout must be between 1 and 300 seconds")
    input_fd = force = led = None
    force_uploads = 0
    try:
        emit("ready", pid=os.getpid(), force_module=linux_ffb.__file__,
             force_uploads=force_uploads, force_plays=0,
             allow_zero_upload=args.allow_zero_upload,
             commands=["input-open", "input-close", "ffb-open", "ffb-close",
                       "led-open", "led-query", "led-dark", "led-close", "status", "quit"]
                       + (["zero-upload", "erase"] if args.allow_zero_upload else []))
        deadline = time.monotonic() + args.idle_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                emit("idle-timeout")
                break
            readable, _, _ = select.select([sys.stdin], [], [], min(remaining, 5))
            if not readable:
                continue
            line = sys.stdin.readline()
            if not line:
                emit("stdin-closed")
                break
            command = line.strip()
            deadline = time.monotonic() + args.idle_timeout
            if command == "quit":
                break
            if command == "input-open" and input_fd is None:
                import fcntl
                input_fd = os.open(args.event, os.O_RDONLY | os.O_NONBLOCK)
                identity = bytearray(8)
                fcntl.ioctl(input_fd, 0x80084502, identity, True)
                if struct.unpack("@HHHH", identity)[1:3] != (0x346e, 4):
                    raise ValueError("input handle is not MOZA R5 346e:0004")
            elif command == "input-close" and input_fd is not None:
                os.close(input_fd)
                input_fd = None
            elif command == "ffb-open" and force is None:
                force = linux_ffb.LinuxConstantForce(args.event)
            elif command == "ffb-close" and force is not None:
                force.close()
                force = None
            elif command == "zero-upload" and args.allow_zero_upload and force is not None and force.effect_id < 0:
                data = linux_ffb.effect_bytes(0)
                force.ioctl(force.fd, (1 << 30) | (len(data) << 16) | (ord("E") << 8) | 0x80, data, True)
                assigned = struct.unpack_from("<h", data, 2)[0]
                if not 0 <= assigned < force.slots:
                    raise OSError("invalid zero-force effect ID")
                force.effect_id = assigned
                force_uploads += 1
                emit("zero-upload-result", effect_id=assigned, level=0, replay_ms=150, effect_hex=data.hex())
            elif command == "erase" and args.allow_zero_upload and force is not None:
                force.stop()
            elif command == "led-open" and led is None:
                led = LedSerial(args.led)
            elif command == "led-query" and led is not None:
                value, record = led.query(0x13, b"\4", 1)
                emit("led-query-reply", value_hex=value.hex() if value is not None else None, record=record)
            elif command == "led-dark" and led is not None:
                wire = legacy_led_frame(0)
                if led.port.write(wire) != len(wire):
                    raise OSError("short LED dark-frame write")
                emit("led-dark-frame", wire_hex=wire.hex())
            elif command != "status":
                raise ValueError(f"command unavailable in current state: {command}")
            emit(command, input_fd=input_fd, force_fd=force.fd if force else None,
                 effect_id=force.effect_id if force else None,
                 led_fd=led.port.fileno() if led else None,
                 force_uploads=force_uploads, force_plays=0)
    except KeyboardInterrupt:
        emit("interrupt")
    finally:
        for name, device in (("force", force), ("led", led)):
            if device is not None:
                try:
                    device.close()
                    emit("cleanup", handle=name)
                except Exception as exc:
                    emit("cleanup-error", handle=name, error=str(exc))
        if input_fd is not None:
            os.close(input_fd)
            emit("cleanup", handle="input")
        emit("finished", force_uploads=force_uploads, force_plays=0)


if __name__ == "__main__":
    main()
