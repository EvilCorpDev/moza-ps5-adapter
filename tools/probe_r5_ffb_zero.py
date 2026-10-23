#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Upload and erase a zero-force R5 effect; never send a play event."""
import argparse
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
from moza_ps5.linux_ffb import LinuxConstantForce, effect_bytes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("event")
    args = ap.parse_args()
    device = LinuxConstantForce(args.event)
    try:
        data = effect_bytes(0)
        device.ioctl(device.fd, (1 << 30) | (len(data) << 16) | (ord("E") << 8) | 0x80, data, True)
        assigned = struct.unpack_from("<h", data, 2)[0]
        if not 0 <= assigned < device.slots:
            raise OSError("Invalid effect ID from R5 driver")
        device.effect_id = assigned
        result = {"zero_force_effect_uploaded": True, "effect_id": assigned,
                  "effect_bytes": len(data), "effect_slots": device.slots,
                  "level": 0, "replay_ms": 150, "play_events_sent": 0}
    finally:
        device.close()
    result["effect_erased_and_device_closed"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
