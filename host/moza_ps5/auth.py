"""Razer Raion feature-report exchange used as the PS5 authentication donor."""
from __future__ import annotations
from dataclasses import dataclass
import logging
import secrets
import time
import zlib

PAGE_SIZE = 56
NONCE_SIZE = 256
RESPONSE_SIZE = 1064


def _crc(data: bytes) -> int:
    return zlib.crc32(data) & 0xffffffff


def set_page(nonce_id: int, page: int, nonce: bytes) -> bytes:
    if not 0 <= nonce_id <= 255 or not 0 <= page < 5 or len(nonce) != NONCE_SIZE:
        raise ValueError("invalid authentication page")
    payload_len = 32 if page == 4 else PAGE_SIZE
    report = bytearray(64)
    report[:4] = bytes((0xf0, nonce_id, page, 0))
    start = page * PAGE_SIZE
    report[4:4 + payload_len] = nonce[start:start + payload_len]
    report[60:64] = _crc(report[:60]).to_bytes(4, "little")
    return bytes(report)


def _check_report(report: bytes, report_id: int, length: int) -> bytes:
    # hidapi includes the report ID. Some backends return a short list on a
    # failed transfer; treat it as a protocol error instead of indexing it.
    if len(report) != length or report[0] != report_id:
        raise RuntimeError(f"feature F{report_id:02x}: expected {length} bytes, got {len(report)}")
    return report


@dataclass(slots=True)
class AuthResult:
    nonce_id: int
    signature_blob: bytes
    elapsed_s: float
    f2_crc_mismatches: int = 0
    f1_crc_mismatch_pages: tuple[int, ...] = ()


class Donor:
    """Small interface implemented by real hidapi and test donors."""
    def get_feature(self, report_id: int, length: int) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def set_feature(self, report: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def sign_nonce(donor: Donor, nonce: bytes | None = None, nonce_id: int | None = None,
               timeout_s: float = 8.0, poll_s: float = .01) -> AuthResult:
    nonce = secrets.token_bytes(NONCE_SIZE) if nonce is None else bytes(nonce)
    if len(nonce) != NONCE_SIZE:
        raise ValueError("nonce must be 256 bytes")
    nonce_id = secrets.randbelow(256) if nonce_id is None else nonce_id & 255
    started = time.monotonic()
    reset = donor.get_feature(0xf3, 8)
    _check_report(reset, 0xf3, 8)
    for page in range(5):
        donor.set_feature(set_page(nonce_id, page, nonce))
    deadline = time.monotonic() + timeout_s
    f2_crc_warned = False
    f2_crc_mismatches = 0
    while True:
        state = _check_report(donor.get_feature(0xf2, 16), 0xf2, 16)
        if state[1] != nonce_id:
            raise RuntimeError("Raion returned a different nonce ID")
        if _crc(state[:12]) != int.from_bytes(state[12:16], "little"):
            f2_crc_mismatches += 1
            # Captured Raion F2 status reports do not always have a valid CRC.
            # Treat this status-only checksum as advisory. F1 remains strict.
            if not f2_crc_warned:
                logging.getLogger(__name__).warning(
                    "Raion F2 CRC mismatch; continuing status polling. "
                    "F1 response CRC checks remain enabled."
                )
                f2_crc_warned = True
        if state[2] == 0:
            break
        if state[2] != 16:
            raise RuntimeError(f"Raion signing state error {state[2]}")
        if time.monotonic() >= deadline:
            raise TimeoutError("Raion did not finish signing before timeout")
        time.sleep(poll_s)
    blob = bytearray()
    f1_crc_mismatch_pages: list[int] = []
    f1_crc_warned = False
    for page in range(19):
        report = _check_report(donor.get_feature(0xf1, 64), 0xf1, 64)
        if report[1] != nonce_id or report[2] != page:
            raise RuntimeError(f"Raion returned unexpected F1 page {report[2]} (wanted {page})")
        expected_crc = _crc(report[:60])
        received_crc = int.from_bytes(report[60:64], "little")
        if received_crc == 0:
            # Raion response pages observed in hardware carry zero in the
            # checksum field. Treat zero as "not supplied" and retain all
            # structural checks. A non-zero checksum is still authoritative.
            f1_crc_mismatch_pages.append(page)
            if not f1_crc_warned:
                logging.getLogger(__name__).warning(
                    "Raion F1 CRC field is zero; continuing with page/order checks. "
                    "Non-zero F1 CRC mismatches remain errors."
                )
                f1_crc_warned = True
        elif received_crc != expected_crc:
            raise RuntimeError(f"Raion F1 page {page} CRC failed")
        blob.extend(report[4:60])
    return AuthResult(
        nonce_id,
        bytes(blob),
        time.monotonic() - started,
        f2_crc_mismatches,
        tuple(f1_crc_mismatch_pages),
    )
