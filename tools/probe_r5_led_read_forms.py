#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read known legacy LED settings with documented request variants; no writes."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from moza_ps5.moza_leds import LedSerial, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"settings_writes": 0, "queries": []}
    with args.output.open("x", encoding="utf-8") as evidence:
        link = LedSerial(args.serial)
        try:
            for name, command, size in (("blink_interval", b"\x16", 4),
                                        ("rpm_mode", b"\x17", 1),
                                        ("display_mode", b"\x08", 1),
                                        ("color_first", b"\x15\x00\x00", 3),
                                        ("color_last", b"\x15\x00\x09", 3)):
                for payload in (b"", bytes(size), bytes(size - 1) + b"\1"):
                    link.port.reset_input_buffer()
                    message = frame(0x40, 0x13, command, payload)
                    link.port.write(message)
                    replies, raw = [], bytearray()
                    until = time.monotonic() + .25
                    while time.monotonic() < until:
                        chunk = link.port.read(256)
                        raw.extend(chunk)
                        for reply in link.parser.feed(chunk):
                            replies.append({"group": reply.group, "device": reply.device,
                                            "payload_hex": reply.payload.hex()})
                    record = {"setting": name, "request_hex": message.hex(),
                              "raw_reply_hex": raw.hex(), "replies": replies}
                    result["queries"].append(record)
                    print(f"{name} request_payload={payload.hex() or 'none'} reply={raw.hex()}", flush=True)
            result["parser_errors"] = link.parser.errors
        finally:
            link.close()
            json.dump(result, evidence, indent=2)
            evidence.write("\n")


if __name__ == "__main__":
    main()
