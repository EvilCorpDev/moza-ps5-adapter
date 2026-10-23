#!/usr/bin/env python3
"""Install the observed-Raion CRC compatibility behavior into 0.1.0.

Run from the project root. The script is source-guarded and creates a backup
before changing auth.py.
"""
from pathlib import Path


p = Path("host/moza_ps5/auth.py")
if not p.is_file():
    raise SystemExit("Run this from the moza-ps5-diagnostics-0.1.0 project root.")
old = p.read_text()

if "f1_crc_mismatch_pages" in old:
    print("Raion CRC compatibility fix is already installed.")
    raise SystemExit(0)

required = [
    "class AuthResult:",
    "f2_crc_warned = False",
    'raise RuntimeError(f"Raion F1 page {page} CRC failed")',
]
missing = [needle for needle in required if needle not in old]
if missing:
    raise SystemExit("Unexpected auth.py version; no changes made. Missing: " + ", ".join(missing))

new = old.replace(
    "    elapsed_s: float\n",
    "    elapsed_s: float\n    f2_crc_mismatches: int = 0\n    f1_crc_mismatch_pages: tuple[int, ...] = ()\n",
    1,
)
new = new.replace(
    "    f2_crc_warned = False\n",
    "    f2_crc_warned = False\n    f2_crc_mismatches = 0\n",
    1,
)
new = new.replace(
    '        if _crc(state[:12]) != int.from_bytes(state[12:16], "little"):\n'
    '            # Captured Raion F2 status reports do not always have a valid CRC.\n',
    '        if _crc(state[:12]) != int.from_bytes(state[12:16], "little"):\n'
    '            f2_crc_mismatches += 1\n'
    '            # Captured Raion F2 status reports do not always have a valid CRC.\n',
    1,
)
old_f1 = '''    blob = bytearray()
    for page in range(19):
        report = _check_report(donor.get_feature(0xf1, 64), 0xf1, 64)
        if report[1] != nonce_id or report[2] != page:
            raise RuntimeError(f"Raion returned unexpected F1 page {report[2]} (wanted {page})")
        if _crc(report[:60]) != int.from_bytes(report[60:64], "little"):
            raise RuntimeError(f"Raion F1 page {page} CRC failed")
        blob.extend(report[4:60])
    return AuthResult(nonce_id, bytes(blob), time.monotonic() - started)
'''
new_f1 = '''    blob = bytearray()
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
'''
if old_f1 not in new:
    raise SystemExit("Could not locate the expected F1 block; no changes made.")
new = new.replace(old_f1, new_f1, 1)
compile(new, str(p), "exec")
backup = p.with_name("auth.py.before-raion-crc-fix")
if not backup.exists():
    backup.write_text(old)
p.write_text(new)
print(f"Raion CRC compatibility fix installed. Backup: {backup}")
