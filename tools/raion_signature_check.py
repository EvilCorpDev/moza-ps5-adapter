#!/usr/bin/env python3
"""Standalone Raion diagnostic: inspect report CRCs, verify the nonce signature.

Run with the existing project's Python environment, hidapi, and cryptography.
This does not modify the installed bridge or send data to a PlayStation.
The returned public key is untrusted: verifying the nonce does not verify
Sony's controller certificate or the certificate bytes in the response.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import zlib


def crc_bytes(data: bytes) -> bytes:
    return (zlib.crc32(data) & 0xffffffff).to_bytes(4, "little")


def collect_response(device, nonce: bytes, nonce_id: int, timeout_s=8.0, emit=print):
    """Collect without trusting report CRCs; structure/sequence remain strict."""
    if len(nonce) != 256 or not 0 <= nonce_id <= 255:
        raise ValueError("Expected a 256-byte nonce and one-byte nonce ID")
    started = time.monotonic()
    deadline = started + timeout_s

    def read(report_id, length):
        if time.monotonic() >= deadline:
            raise TimeoutError("Raion exchange exceeded its time limit")
        data = bytes(device.get_feature_report(report_id, length))
        if len(data) != length or data[0] != report_id:
            raise RuntimeError(
                f"Report 0x{report_id:02x}: expected {length} bytes with matching ID; "
                f"received {len(data)} bytes, header={data[:4].hex(' ')}"
            )
        return data

    reset = read(0xf3, 8)
    emit(f"F3: {reset.hex(' ')}")
    if reset[2:4] != b"\x38\x38":
        raise RuntimeError("Raion reports unsupported challenge/response page sizes")

    for page in range(5):
        data = bytes((0xf0, nonce_id, page, 0))
        data += nonce[page * 56:(page + 1) * 56].ljust(56, b"\0")
        report = data + crc_bytes(data)
        written = device.send_feature_report(report)
        if written != 64:
            raise RuntimeError(f"F0 page {page}: short write {written}/64")

    polls = f2_bad_crc = 0
    previous_status = None
    while True:
        state = read(0xf2, 16)
        if state[1] != nonce_id:
            raise RuntimeError("F2 has the wrong nonce ID")
        crc_ok = state[12:16] == crc_bytes(state[:12])
        polls += 1
        f2_bad_crc += not crc_ok
        status = (state[2], crc_ok)
        if status != previous_status:
            emit(f"F2: state=0x{state[2]:02x}, CRC={'OK' if crc_ok else 'MISMATCH'}")
            previous_status = status
        if state[2] == 0:
            break
        if state[2] != 0x10:
            raise RuntimeError(f"F2 reports signing error/state 0x{state[2]:02x}")
        time.sleep(0.01)

    response = bytearray()
    mismatches = []
    for page in range(19):
        data = read(0xf1, 64)
        if data[1] != nonce_id or data[2] != page:
            raise RuntimeError(
                f"F1 header mismatch: expected nonce={nonce_id:02x}, page={page}; "
                f"received nonce={data[1]:02x}, page={data[2]}"
            )
        expected = crc_bytes(data[:60])
        crc_ok = data[60:64] == expected
        if not crc_ok:
            mismatches.append(page)
        # Avoid printing the nonce, response payload, serial or public key.
        emit(
            f"F1 page {page:02d}: header={data[:4].hex(' ')}, "
            f"CRC={'OK' if crc_ok else 'MISMATCH'}, "
            f"received={data[60:64].hex(' ')}, calculated={expected.hex(' ')}"
        )
        response.extend(data[4:60])
    return bytes(response), {
        "response_bytes": len(response),
        "f2_polls": polls,
        "f2_crc_mismatches": f2_bad_crc,
        "f1_crc_mismatch_pages": mismatches,
        "exchange_elapsed_s": round(time.monotonic() - started, 3),
    }


def verify_nonce_signature(nonce: bytes, response: bytes) -> bool:
    """Verify RSA-PSS/SHA-256 using the public key supplied by the donor.

    Layout: signature[256], serial[16], modulus[256], exponent[256],
    certificate-signature[256], padding[24]. RSA integers are big endian.
    This check covers the nonce signature, not the identity/certificate block.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    if len(nonce) != 256 or len(response) != 1064:
        raise ValueError("Unexpected nonce or response length")
    modulus = int.from_bytes(response[272:528], "big")
    exponent = int.from_bytes(response[528:784], "big")
    if modulus.bit_length() != 2048 or not 3 <= exponent <= 0xffffffff or exponent % 2 == 0:
        raise ValueError(
            "Unexpected RSA public-key layout: "
            f"modulus_bits={modulus.bit_length()}, exponent_bits={exponent.bit_length()}"
        )
    public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
    try:
        public_key.verify(
            response[:256], nonce,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
    except InvalidSignature:
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="Raion path from list-donors")
    args = parser.parse_args(argv)
    try:
        import hid
        import cryptography  # Fail before touching the donor if missing.
    except ImportError as exc:
        raise RuntimeError(
            "Install dependencies with .venv/bin/python -m pip install hidapi 'cryptography>=42'"
        ) from exc

    path = args.path.encode()
    matches = [d for d in hid.enumerate(0x1532, 0x1100)
               if d["path"] == path or d["path"] == args.path]
    if len(matches) != 1:
        raise RuntimeError("No unique Razer Raion 1532:1100 at that path; rerun list-donors")

    device = hid.device()
    device.open_path(path)
    try:
        device.set_nonblocking(False)
        nonce, nonce_id = secrets.token_bytes(256), secrets.randbelow(256)
        print(f"Raion 1532:1100, nonce_id={nonce_id:02x}, cryptography={cryptography.__version__}", flush=True)
        response, summary = collect_response(device, nonce, nonce_id)
        verified = verify_nonce_signature(nonce, response)
        summary.update({
            "nonce_signature_verified": verified,
            "controller_certificate_verified": None,
            "ps5_acceptance": None,
        })
        print(json.dumps(summary, indent=2), flush=True)
        if not verified:
            print("Nonce signature did not verify with the expected RSA-PSS/SHA-256 parameters.", file=sys.stderr)
        return 0 if verified else 1
    finally:
        device.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
