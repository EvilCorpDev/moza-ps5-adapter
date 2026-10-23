#!/usr/bin/env python3
"""Create an RP2040 UF2 from a Pico SDK .bin image."""
from __future__ import annotations
import argparse
import struct
from pathlib import Path

MAGIC0, MAGIC1, MAGIC_END = 0x0A324655, 0x9E5D5157, 0x0AB16F30
RP2040_FAMILY = 0xE48BFF56
BLOCK = 256


def convert(src: Path, dst: Path, base: int = 0x10000000) -> None:
    data = src.read_bytes()
    count = (len(data) + BLOCK - 1) // BLOCK
    with dst.open("wb") as out:
        for number in range(count):
            payload = data[number * BLOCK:(number + 1) * BLOCK].ljust(BLOCK, b"\0")
            header = struct.pack("<IIIIIIII", MAGIC0, MAGIC1, 0x2000, base + number * BLOCK,
                                 BLOCK, number, count, RP2040_FAMILY)
            out.write(header + payload + b"\0" * (512 - len(header) - len(payload) - 4) + struct.pack("<I", MAGIC_END))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path); ap.add_argument("destination", type=Path)
    args = ap.parse_args(); convert(args.source, args.destination)
