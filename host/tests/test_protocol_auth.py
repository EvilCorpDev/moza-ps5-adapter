from __future__ import annotations
import pathlib
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
from moza_ps5.auth import Donor, sign_nonce, set_page
from moza_ps5.bridge import challenge_nonce
from moza_ps5.protocol import Packet, StreamParser


def test_frame_roundtrip_and_crc_rejection():
    p = Packet(10, 0x1234, 0x5678, bytes(range(256)))
    wire = p.encode()
    assert StreamParser().feed(wire) == [p]
    bad = bytearray(wire); bad[4] ^= 1
    parser = StreamParser(); assert parser.feed(bytes(bad)) == []; assert parser.errors == 1


class FakeDonor(Donor):
    def __init__(self):
        self.id = None; self.nonce = bytearray(256); self.polls = 0; self.page = 0
        self.response = bytes((i * 7) & 255 for i in range(1064))

    def get_feature(self, report_id, length):
        if report_id == 0xf3: return bytes([0xf3, 0, 56, 56, 0, 0, 0, 0])
        if report_id == 0xf2:
            self.polls += 1; ready = self.polls > 2
            r = bytearray([0xf2, self.id or 0, 0 if ready else 16] + [0] * 9)
            r += zlib.crc32(r).to_bytes(4, 'little'); return bytes(r)
        assert report_id == 0xf1
        r = bytearray([0xf1, self.id, self.page, 0]) + bytearray(self.response[56*self.page:56*self.page+56])
        self.page = (self.page + 1) % 19
        r += zlib.crc32(r).to_bytes(4, 'little'); return bytes(r)

    def set_feature(self, report):
        assert len(report) == 64 and report[0] == 0xf0
        self.id = report[1]
        if report[2] < 4: self.nonce[56*report[2]:56*report[2]+56] = report[4:60]
        else: self.nonce[224:] = report[4:36]


def test_raion_exchange_checks_pages_and_crc():
    donor = FakeDonor(); result = sign_nonce(donor, bytes(range(256)), nonce_id=9, poll_s=0)
    assert result.nonce_id == 9 and len(result.signature_blob) == 1064
    assert donor.nonce == bytes(range(256))


def test_page_four_is_zero_padded():
    report = set_page(4, 4, bytes(range(256)))
    assert report[36:60] == b'\0' * 24
    assert int.from_bytes(report[60:], 'little') == zlib.crc32(report[:60]) & 0xffffffff


def test_pico_nonce_padding_is_rejected_or_removed():
    payload = bytes([9]) + bytes(range(256)) + bytes(24)
    assert challenge_nonce(payload) == (9, bytes(range(256)))
    bad = bytearray(payload); bad[-1] = 1
    try:
        challenge_nonce(bytes(bad))
    except ValueError:
        pass
    else:
        raise AssertionError("non-zero page padding was accepted")
