"""Regression tests for the Raion status reply captured on 2026-10-09."""
import logging

import pytest

from moza_ps5 import auth
from test_protocol_auth import FakeDonor


CAPTURED_F2 = bytes.fromhex("f2 e4 10 00 00 00 00 00 86 26 56 ac 7f 5d 86 7d")


class CapturedStatusDonor(FakeDonor):
    def __init__(self, status=CAPTURED_F2):
        super().__init__()
        self.status = status
        self.f1_reads = 0

    def get_feature(self, report_id, length):
        if report_id == 0xf1:
            self.f1_reads += 1
        report = super().get_feature(report_id, length)
        if report_id == 0xf2 and self.polls <= 2:
            return self.status
        return report


def test_captured_busy_reply_warns_once_then_reads_full_response(caplog):
    donor = CapturedStatusDonor()
    with caplog.at_level(logging.WARNING, logger="moza_ps5.auth"):
        result = auth.sign_nonce(donor, nonce_id=0xe4, poll_s=0)
    assert result.signature_blob == donor.response
    assert donor.f1_reads == 19
    assert donor.polls == 3
    assert result.f2_crc_mismatches == 2
    assert result.f1_crc_mismatch_pages == ()
    assert sum("F2 CRC mismatch" in r.message for r in caplog.records) == 1


def test_ready_status_crc_is_also_advisory():
    # Policy coverage, not an additional hardware capture: mark it ready.
    donor = CapturedStatusDonor(CAPTURED_F2[:2] + b"\x00" + CAPTURED_F2[3:])
    result = auth.sign_nonce(donor, nonce_id=0xe4, poll_s=0)
    assert result.signature_blob == donor.response
    assert donor.polls == 1
    assert result.f2_crc_mismatches == 1


def test_zero_f1_crc_fields_are_advisory(caplog):
    class ZeroF1CrcDonor(CapturedStatusDonor):
        def get_feature(self, report_id, length):
            report = bytearray(super().get_feature(report_id, length))
            if report_id == 0xf1:
                report[60:64] = bytes(4)
            return bytes(report)

    with caplog.at_level(logging.WARNING, logger="moza_ps5.auth"):
        result = auth.sign_nonce(ZeroF1CrcDonor(), nonce_id=0xe4, poll_s=0)
    assert result.signature_blob
    assert result.f1_crc_mismatch_pages == tuple(range(19))
    assert sum("F1 CRC field is zero" in r.message for r in caplog.records) == 1


@pytest.mark.parametrize("status, message", [
    (b"", "expected 16 bytes"),
    (CAPTURED_F2[:-1], "expected 16 bytes"),
    (b"\xf1" + CAPTURED_F2[1:], "feature"),
    (CAPTURED_F2[:1] + b"\xe5" + CAPTURED_F2[2:], "different nonce ID"),
    (CAPTURED_F2[:2] + b"\x01" + CAPTURED_F2[3:], "signing state error 1"),
    (CAPTURED_F2[:2] + b"\xff" + CAPTURED_F2[3:], "signing state error 255"),
])
def test_malformed_or_error_status_is_still_rejected(status, message):
    donor = CapturedStatusDonor(status)
    with pytest.raises(RuntimeError, match=message):
        auth.sign_nonce(donor, nonce_id=0xe4, poll_s=0)
    assert donor.f1_reads == 0


@pytest.mark.parametrize("byte_index, message", [
    (1, "unexpected F1 page"),
    (2, "unexpected F1 page"),
    (4, "F1 page 0 CRC failed"),
    (60, "F1 page 0 CRC failed"),
])
def test_bad_response_is_still_rejected_after_status_warning(byte_index, message):
    class BadResponseDonor(CapturedStatusDonor):
        def get_feature(self, report_id, length):
            report = bytearray(super().get_feature(report_id, length))
            if report_id == 0xf1:
                report[byte_index] ^= 1
            return bytes(report)

    with pytest.raises(RuntimeError, match=message):
        auth.sign_nonce(BadResponseDonor(), nonce_id=0xe4, poll_s=0)


def test_busy_status_crc_mismatch_does_not_disable_timeout(monkeypatch):
    ticks = iter([0.0, 0.0, 0.2])
    # Patch only auth's clock so log-record timestamps cannot consume ticks.
    class Clock:
        @staticmethod
        def monotonic():
            return next(ticks)

    monkeypatch.setattr(auth, "time", Clock)
    donor = CapturedStatusDonor()
    with pytest.raises(TimeoutError, match="did not finish signing"):
        auth.sign_nonce(donor, nonce_id=0xe4, timeout_s=0.1, poll_s=0)
    assert donor.f1_reads == 0
