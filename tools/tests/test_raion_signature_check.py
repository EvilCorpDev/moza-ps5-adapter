import importlib.util
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

spec = importlib.util.spec_from_file_location("diagnostic", Path(__file__).parents[1] / "raion_signature_check.py")
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


@pytest.fixture(scope="module")
def signing_key():
    # This is a new software-test key, not a controller key; never saved.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def signed_response(key, nonce):
    public = key.public_key().public_numbers()
    signature = key.sign(nonce, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
    return signature + bytes(16) + public.n.to_bytes(256, "big") + public.e.to_bytes(256, "big") + bytes(280)


class SimulatedRaion:
    def __init__(self, key, bad_crc=False):
        self.key = key
        self.bad_crc = bad_crc
        self.nonce = bytearray()
        self.nonce_id = None
        self.page = self.polls = 0

    def send_feature_report(self, report):
        assert len(report) == 64 and report[0] == 0xf0
        assert report[-4:] == diagnostic.crc_bytes(report[:-4])
        self.nonce_id = report[1]
        if report[2] == 4:
            assert report[36:60] == bytes(24)
            self.nonce.extend(report[4:36])
            self.response = signed_response(self.key, bytes(self.nonce))
        else:
            self.nonce.extend(report[4:60])
        return 64

    def get_feature_report(self, report_id, length):
        if report_id == 0xf3:
            return bytes.fromhex("f3 00 38 38 00 00 00 00")
        if report_id == 0xf2:
            self.polls += 1
            if self.bad_crc and self.polls == 1:
                assert self.nonce_id == 0xe4
                return bytes.fromhex("f2 e4 10 00 00 00 00 00 86 26 56 ac 7f 5d 86 7d")
            data = bytes((0xf2, self.nonce_id, 0)) + bytes(9)
        else:
            assert report_id == 0xf1
            data = bytes((0xf1, self.nonce_id, self.page, 0)) + self.response[self.page*56:(self.page+1)*56]
            self.page += 1
        crc = bytearray(diagnostic.crc_bytes(data))
        if self.bad_crc:
            crc[0] ^= 1
        return data + bytes(crc)


@pytest.mark.parametrize("bad_crc", [False, True])
def test_complete_exchange_and_signature_check(signing_key, bad_crc):
    nonce = bytes(range(256))
    donor = SimulatedRaion(signing_key, bad_crc)
    response, summary = diagnostic.collect_response(donor, nonce, 0xe4, emit=lambda _: None)
    assert bytes(donor.nonce) == nonce
    assert summary["response_bytes"] == 1064
    assert summary["f1_crc_mismatch_pages"] == (list(range(19)) if bad_crc else [])
    assert diagnostic.verify_nonce_signature(nonce, response) is True


def test_changed_nonce_fails_verification(signing_key):
    nonce = bytes(range(256))
    response = signed_response(signing_key, nonce)
    assert diagnostic.verify_nonce_signature(bytes(256), response) is False


def test_corrupt_signature_fails_even_if_transport_crc_was_ignored(signing_key):
    nonce = bytes(range(256))
    response = bytearray(signed_response(signing_key, nonce))
    response[17] ^= 1
    assert diagnostic.verify_nonce_signature(nonce, bytes(response)) is False


def test_invalid_public_key_is_rejected(signing_key):
    nonce = bytes(range(256))
    response = bytearray(signed_response(signing_key, nonce))
    response[528:784] = bytes(256)
    with pytest.raises(ValueError, match="public-key layout"):
        diagnostic.verify_nonce_signature(nonce, bytes(response))


def test_certificate_bytes_are_not_claimed_to_be_verified(signing_key):
    nonce = bytes(range(256))
    response = bytearray(signed_response(signing_key, nonce))
    response[800] ^= 1
    assert diagnostic.verify_nonce_signature(nonce, bytes(response)) is True


@pytest.mark.parametrize("report_id, byte_index, message", [
    (0xf2, 0, "matching ID"),
    (0xf2, 1, "wrong nonce ID"),
    (0xf2, 2, "signing error"),
    (0xf1, 0, "matching ID"),
    (0xf1, 1, "header mismatch"),
    (0xf1, 2, "header mismatch"),
])
def test_malformed_replies_are_rejected(signing_key, report_id, byte_index, message):
    class BadHeaderDonor(SimulatedRaion):
        def get_feature_report(self, requested, length):
            report = bytearray(super().get_feature_report(requested, length))
            if requested == report_id:
                report[byte_index] ^= 1
            return bytes(report)
    with pytest.raises(RuntimeError, match=message):
        diagnostic.collect_response(BadHeaderDonor(signing_key), bytes(256), 0xe4, emit=lambda _: None)


def test_short_f1_is_rejected(signing_key):
    class ShortDonor(SimulatedRaion):
        def get_feature_report(self, requested, length):
            report = super().get_feature_report(requested, length)
            return report[:-1] if requested == 0xf1 else report
    with pytest.raises(RuntimeError, match="expected 64 bytes"):
        diagnostic.collect_response(ShortDonor(signing_key), bytes(256), 0xe4, emit=lambda _: None)


def test_timeout_still_applies(signing_key, monkeypatch):
    class Clock:
        ticks = 0
        @classmethod
        def monotonic(cls):
            cls.ticks += 1
            return cls.ticks * 0.1
    monkeypatch.setattr(diagnostic, "time", Clock)
    with pytest.raises(TimeoutError):
        diagnostic.collect_response(SimulatedRaion(signing_key), bytes(256), 0xe4, timeout_s=0.25, emit=lambda _: None)
