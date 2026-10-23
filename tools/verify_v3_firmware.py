#!/usr/bin/env python3
"""Check the v3 HID layout and actual compiled BIN/UF2 USB descriptors."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_SHA256 = "2e5a4573523b7f2d2c2ab22864d0c90b71d814b8b80c932f6a7c11144d50128d"
DEVICE = bytes.fromhex("12 01 00 02 00 00 00 40 6d 04 60 c2 00 01 01 02 00 01")
CONFIGURATION = bytes.fromhex(
    "09 02 29 00 01 01 00 c0 64 "
    "09 04 00 00 02 03 00 00 00 "
    "09 21 11 01 00 01 22 a0 00 "
    "07 05 03 03 40 00 05 07 05 84 03 40 00 05"
)


def descriptor_bytes(path: Path = ROOT / "firmware/ps_report_descriptor_v3.h") -> bytes:
    source = re.sub(r"//[^\n]*|/\*.*?\*/", "", path.read_text(), flags=re.S)
    body = source.split("= {", 1)[1].split("}", 1)[0]
    return bytes(int(word.strip(), 0) for word in body.split(",") if word.strip())


def hid_layout(data: bytes) -> tuple[dict[tuple[str, int], int], list[tuple[int, int]]]:
    """Parse HID short items, accounting for inherited report size and count."""
    offset = depth = 0
    report_id = report_size = report_count = usage_page = 0
    usages: list[int] = []
    reports: dict[tuple[str, int], int] = {}
    applications: list[tuple[int, int]] = []
    while offset < len(data):
        prefix = data[offset]; offset += 1
        if prefix == 0xfe:
            raise ValueError("unexpected HID long item")
        size = (0, 1, 2, 4)[prefix & 3]
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if offset + size > len(data):
            raise ValueError("truncated HID item")
        value = int.from_bytes(data[offset:offset+size], "little"); offset += size
        if kind == 1:
            if tag == 0: usage_page = value
            elif tag == 7: report_size = value
            elif tag == 8:
                if not 1 <= value <= 255: raise ValueError("invalid report ID")
                report_id = value
            elif tag == 9: report_count = value
            elif tag in (10, 11): raise ValueError("unsupported global stack item")
        elif kind == 2 and tag == 0:
            usages.append(value)
        elif kind == 0:
            if tag in (8, 9, 11):
                name = {8: "input", 9: "output", 11: "feature"}[tag]
                key = name, report_id
                reports[key] = reports.get(key, 0) + report_size * report_count
            elif tag == 10:
                if value == 1:
                    if not usages: raise ValueError("application lacks usage")
                    applications.append((usage_page, usages[-1]))
                depth += 1
            elif tag == 12:
                depth -= 1
                if depth < 0: raise ValueError("unbalanced HID collection")
            usages.clear()
        elif kind == 3:
            raise ValueError("reserved HID item")
    if depth: raise ValueError("unterminated HID collection")
    return reports, applications


def verify_descriptor(data: bytes) -> None:
    if len(data) != 160 or hashlib.sha256(data).hexdigest() != DESCRIPTOR_SHA256:
        raise ValueError("descriptor differs from the pinned 160-byte upstream profile")
    reports, applications = hid_layout(data)
    expected = {("input", 1): 63*8, ("output", 5): 31*8, ("feature", 3): 47*8,
                ("feature", 0xf0): 63*8, ("feature", 0xf1): 63*8,
                ("feature", 0xf2): 15*8, ("feature", 0xf3): 7*8}
    if reports != expected or applications != [(1, 4), (0xfff0, 0x40)]:
        raise ValueError("unexpected HID report sizes or application usages")


def verify_uf2(binary: bytes, uf2: bytes) -> int:
    count = (len(binary)+255)//256
    if not binary or len(uf2) != count*512:
        raise ValueError("invalid UF2 length")
    for index in range(count):
        block = uf2[index*512:(index+1)*512]
        expected = (0x0a324655, 0x9e5d5157, 0x2000, 0x10000000+index*256,
                    256, index, count, 0xe48bff56)
        if struct.unpack_from("<8I", block) != expected or struct.unpack_from("<I", block, 508)[0] != 0x0ab16f30:
            raise ValueError(f"invalid UF2 header at block {index}")
        payload = binary[index*256:(index+1)*256].ljust(256, b"\0")
        if block[32:288] != payload or any(block[288:508]):
            raise ValueError(f"UF2 payload differs from BIN at block {index}")
    return count


def verify_binary(binary: bytes, descriptor: bytes, version: str = "0.3.0-experimental") -> None:
    for label, expected in (("report descriptor", descriptor), ("configuration descriptor", CONFIGURATION),
                            ("device descriptor", DEVICE)):
        if binary.count(expected) != 1:
            raise ValueError(f"compiled BIN does not contain exactly one expected {label}")
    if version.encode("ascii") not in binary:
        raise ValueError("compiled BIN lacks v3 version metadata")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", type=Path)
    parser.add_argument("--uf2", type=Path)
    parser.add_argument("--version", choices=("0.3.0-experimental", "0.3.1-outputs-experimental"), default="0.3.0-experimental")
    args = parser.parse_args()
    descriptor = descriptor_bytes(); verify_descriptor(descriptor)
    print(f"v3 descriptor verified: 160 bytes; sha256={DESCRIPTOR_SHA256}")
    if args.uf2 and not args.bin: parser.error("--uf2 requires --bin")
    if args.bin:
        binary = args.bin.read_bytes(); verify_binary(binary, descriptor, args.version)
        print("compiled USB descriptors verified: 046d:c260, OUT 03 / IN 84, 64-byte endpoints, 5 ms")
        if args.uf2:
            count = verify_uf2(binary, args.uf2.read_bytes())
            print(f"RP2040 UF2 verified: {count} blocks; flash base 0x10000000; payload matches BIN")


if __name__ == "__main__":
    main()
