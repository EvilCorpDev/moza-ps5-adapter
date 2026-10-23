from __future__ import annotations
from dataclasses import dataclass
import argparse


@dataclass(slots=True)
class HidInfo:
    path: str
    vendor_id: int
    product_id: int
    manufacturer: str
    product: str
    serial: str


def enumerate_hid() -> list[HidInfo]:
    try:
        import hid
    except ImportError as exc:
        raise RuntimeError("hidapi is not installed; run python3 -m pip install -r requirements.txt") from exc
    return [HidInfo(str(d["path"], "utf-8") if isinstance(d["path"], bytes) else str(d["path"]),
                    int(d.get("vendor_id", 0)), int(d.get("product_id", 0)),
                    str(d.get("manufacturer_string") or ""), str(d.get("product_string") or ""),
                    str(d.get("serial_number") or "")) for d in hid.enumerate()]


class HidDonor:
    def __init__(self, path: str):
        try:
            import hid
            self._dev = hid.device()
            self._dev.open_path(path.encode() if isinstance(path, str) else path)
            self._dev.set_nonblocking(False)
        except ImportError as exc:
            raise RuntimeError("hidapi is not installed") from exc

    def get_feature(self, report_id: int, length: int) -> bytes:
        return bytes(self._dev.get_feature_report(report_id, length))

    def set_feature(self, report: bytes) -> None:
        sent = self._dev.send_feature_report(report)
        if sent != len(report):
            raise OSError(f"short Raion feature write: {sent}/{len(report)}")

    def close(self) -> None:
        self._dev.close()


def select_path(path: str | None = None, vid: int = 0, pid: int = 0) -> HidInfo:
    devices = enumerate_hid()
    matches = [d for d in devices if (not path or d.path == path) and
               (not vid or d.vendor_id == vid) and (not pid or d.product_id == pid)]
    if len(matches) != 1:
        desc = "\n".join(f"{d.path}: {d.vendor_id:04x}:{d.product_id:04x} {d.product}" for d in matches)
        raise RuntimeError(f"expected one donor, found {len(matches)}\n{desc}")
    return matches[0]
