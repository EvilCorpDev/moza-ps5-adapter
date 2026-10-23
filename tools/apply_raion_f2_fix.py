"""Apply the Raion F2 hotfix to an unmodified 0.1.0 auth.py from the project root."""
from pathlib import Path
import hashlib

p = Path("host/moza_ps5/auth.py")
old = p.read_text()
marker = "Raion F2 CRC mismatch; continuing status polling. "
if marker in old and 'raise RuntimeError("Raion F2 CRC failed")' not in old:
    print("F2 fix is already installed.")
    raise SystemExit(0)
if hashlib.sha256(p.read_bytes()).hexdigest() != "bb0776c936c4c00d95cc80aa9eccf296e8b14d35893a2a75ad592d940ab235f4":
    raise SystemExit("auth.py differs from the original 0.1.0; no changes made.")
new = old.replace("import secrets\n", "import logging\nimport secrets\n")
new = new.replace(
    "    deadline = time.monotonic() + timeout_s\n",
    "    deadline = time.monotonic() + timeout_s\n    f2_crc_warned = False\n",
)
new = new.replace('            raise RuntimeError("Raion F2 CRC failed")', '''            # Captured Raion F2 status reports do not always have a valid CRC.
            # Treat this status-only checksum as advisory. F1 remains strict.
            if not f2_crc_warned:
                logging.getLogger(__name__).warning(
                    "Raion F2 CRC mismatch; continuing status polling. "
                    "F1 response CRC checks remain enabled."
                )
                f2_crc_warned = True''')
compile(new, str(p), "exec")
backup = p.with_name("auth.py.before-f2-fix")
if not backup.exists():
    backup.write_text(old)
p.write_text(new)
print(f"F2 fix installed. Backup: {backup}")
