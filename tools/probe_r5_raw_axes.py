#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite R5 absolute-axis capture; only O_RDONLY and EVIOCG read ioctls."""
import argparse
import json
import os
import struct
import sys
import time


def emit(**fields):
    print(json.dumps(fields), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('path')
    ap.add_argument('--seconds', type=float, default=30)
    args = ap.parse_args()
    if not 1 <= args.seconds <= 60:
        ap.error('duration must be between 1 and 60 seconds')
    if sys.platform != 'linux':
        ap.error('Linux input ABI required')
    import fcntl
    fd = os.open(args.path, os.O_RDONLY | os.O_NONBLOCK)
    def query(number, size):
        data = bytearray(size)
        fcntl.ioctl(fd, (2 << 30) | (size << 16) | (ord('E') << 8) | number, data, True)
        return data
    try:
        identity = struct.unpack('@HHHH', query(2, 8))
        if identity[1:3] != (0x346e, 4):
            raise ValueError('Expected MOZA R5 346e:0004')
        bits = query(0x23, 8)
        codes = [code for code in range(64) if bits[code // 8] & (1 << (code % 8))]
        info = {code: struct.unpack('@iiiiii', query(0x40 + code, 24)) for code in codes}
        emit(kind='metadata', readonly=True, identity=identity, seconds=args.seconds,
             fields=['value', 'min', 'max', 'fuzz', 'flat', 'resolution'], axes=info)
        low = {code: row[0] for code, row in info.items()}
        high, previous, samples = dict(low), None, 0
        started = time.monotonic()
        while time.monotonic() - started < args.seconds:
            values = {code: struct.unpack('@iiiiii', query(0x40 + code, 24))[0] for code in codes}
            for code, value in values.items():
                low[code], high[code] = min(low[code], value), max(high[code], value)
            samples += 1
            if values != previous:
                emit(kind='raw-values', elapsed=time.monotonic() - started, axes=values)
                previous = values
            time.sleep(.02)
        emit(kind='summary', samples=samples, elapsed=time.monotonic() - started,
             ranges={code: [low[code], high[code]] for code in codes})
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
