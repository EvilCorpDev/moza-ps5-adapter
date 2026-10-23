#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite LED-only legacy protocol probe; no settings writes or motor access."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from moza_ps5.moza_leds import LedSerial, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings-only", action="store_true")
    parser.add_argument("--full-bar-check", action="store_true",
                        help="compare full bar at 20 Hz, 4 Hz, and a single write")
    args = parser.parse_args()
    result = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "serial": args.serial, "settings_writes": 0, "motor_access": False,
              "settings": {}, "steps": [], "cleanup_zero_sent": False}
    # Open evidence exclusively before touching the LED device.
    with args.output.open("x", encoding="utf-8") as evidence:
        link = None
        try:
            link = LedSerial(args.serial)
            queries = [("indicator_mode", b"\x04", 1), ("rpm_timings", b"\x02", 10),
                       ("rpm_mode", b"\x17", 1), ("blink_interval", b"\x16", 4),
                       ("brightness", b"\x14\x00", 1)]
            if args.full_bar_check:
                queries = queries[:2]
            else:
                queries += [(f"color_{i}", bytes((0x15, 0, i)), 3) for i in range(10)]
            for name, command, size in queries:
                value, record = link.query(0x13, command, size, timeout=.25)
                result["settings"][name] = record
                print(f"{name}: {value.hex() if value is not None else 'unsupported / no value'}", flush=True)
            if args.settings_only:
                return 0
            if result["settings"]["indicator_mode"]["value_hex"] != "01":
                raise ValueError("LED probe requires existing indicator telemetry mode 1")
            print("LED sweep starts in 3 seconds; no torque commands or settings writes", flush=True)
            time.sleep(3)
            # Literal ten-bit masks, matching the primary old-wheel LED test.
            stages = [(f"{lit}/10 lit", (1 << lit) - 1, 20, 2)
                      for lit in (0, 2, 4, 6, 8, 10, 8, 6, 4, 2, 0)]
            if args.full_bar_check:
                stages = [("8 LEDs, 20 Hz", 255, 20, 2),
                          ("full bar, 20 Hz", 1023, 20, 4), ("dark", 0, 20, 2),
                          ("full bar, 4 Hz", 1023, 4, 4), ("dark", 0, 20, 2),
                          ("full bar, single write", 1023, 0, 4), ("dark", 0, 20, 2)]
            link.port.timeout = 0
            for label, mask, rate, duration in stages:
                message = frame(0x41, 0x13, b"\xfd\xde", mask.to_bytes(4, "big"))
                print(f"LED step: {label}; literal mask={mask:03x}", flush=True)
                step = {"label": label, "mask": mask, "rate_hz": rate,
                        "duration_s": duration, "wire_hex": message.hex(), "writes": 0}
                result["steps"].append(step)
                until = time.monotonic() + duration
                next_write = time.monotonic()
                while time.monotonic() < until:
                    if time.monotonic() >= next_write:
                        if link.port.write(message) != len(message):
                            raise OSError("short LED probe write")
                        step["writes"] += 1
                        next_write = time.monotonic() + 1 / rate if rate else float("inf")
                    # Drain serial acknowledgements; do not send additional commands.
                    link.parser.feed(link.port.read(256))
                    time.sleep(.005)
            return 0
        except KeyboardInterrupt:
            result["interrupted"] = True
            return 130
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if link is not None:
                try:
                    if not args.settings_only:
                        zero = frame(0x41, 0x13, b"\xfd\xde", bytes(4))
                        result["cleanup_zero_sent"] = link.port.write(zero) == len(zero)
                        link.port.flush()
                    result["parser_errors"] = link.parser.errors
                finally:
                    link.close()
            result["finished_utc"] = datetime.now(timezone.utc).isoformat()
            json.dump(result, evidence, indent=2)
            evidence.write("\n")
            print("LED probe finished; serial closed", flush=True)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
