"""Versioned COBS framed link used between the Pi and Pico.

The format is deliberately small and boring: a CRC protects each frame, and
session/token fields make delayed USB authentication responses harmless.
"""
from __future__ import annotations
from dataclasses import dataclass
import struct

VERSION = 1
MAX_PAYLOAD = 1100

HELLO, OPEN, STATUS, HEARTBEAT = 1, 2, 3, 4
INPUT, CHALLENGE, RESPONSE, ACK, AUTH_ERROR, EVENT = 5, 10, 11, 12, 13, 20
OUTPUT, OUTPUT_CONTROL = 21, 22


def crc32(data: bytes) -> int:
    return __import__("binascii").crc32(data) & 0xffffffff


def cobs_encode(data: bytes) -> bytes:
    out = bytearray((0,))
    code_at = 0
    code = 1
    for byte in data:
        if byte == 0:
            out[code_at] = code
            code_at = len(out)
            out.append(0)
            code = 1
        else:
            out.append(byte)
            code += 1
            if code == 255:
                out[code_at] = code
                code_at = len(out)
                out.append(0)
                code = 1
    out[code_at] = code
    out.append(0)
    return bytes(out)


def cobs_decode(encoded: bytes) -> bytes:
    if not encoded or encoded[-1:] == b"\0":
        raise ValueError("COBS payload must exclude its delimiter")
    out = bytearray()
    at = 0
    while at < len(encoded):
        code = encoded[at]
        if code == 0:
            raise ValueError("zero code")
        at += 1
        end = at + code - 1
        if end > len(encoded):
            raise ValueError("truncated COBS block")
        out.extend(encoded[at:end])
        at = end
        if code < 255 and at < len(encoded):
            out.append(0)
    return bytes(out)


@dataclass(slots=True)
class Packet:
    kind: int
    session: int
    token: int = 0
    payload: bytes = b""

    def encode(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError("payload too large")
        body = struct.pack("<2sBBIIH", b"MP", VERSION, self.kind,
                           self.session & 0xffffffff, self.token & 0xffffffff,
                           len(self.payload)) + self.payload
        return cobs_encode(body + struct.pack("<I", crc32(body)))

    @classmethod
    def decode(cls, framed: bytes) -> "Packet":
        raw = cobs_decode(framed)
        if len(raw) < 18 or raw[:2] != b"MP" or raw[2] != VERSION:
            raise ValueError("bad packet header")
        length = struct.unpack_from("<H", raw, 12)[0]
        if length > MAX_PAYLOAD or len(raw) != 18 + length:
            raise ValueError("bad packet length")
        if crc32(raw[:-4]) != struct.unpack_from("<I", raw, len(raw) - 4)[0]:
            raise ValueError("bad packet CRC")
        return cls(raw[3], struct.unpack_from("<I", raw, 4)[0],
                   struct.unpack_from("<I", raw, 8)[0], raw[14:14 + length])


class StreamParser:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self.errors = 0

    def feed(self, data: bytes) -> list[Packet]:
        packets: list[Packet] = []
        for byte in data:
            if byte:
                if len(self._buffer) < 1200:
                    self._buffer.append(byte)
                else:
                    self._buffer.clear()
                    self.errors += 1
            elif self._buffer:
                try:
                    packets.append(Packet.decode(bytes(self._buffer)))
                except ValueError:
                    self.errors += 1
                self._buffer.clear()
        return packets
