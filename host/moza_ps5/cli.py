from __future__ import annotations
import argparse
import json
import sys

from .auth import sign_nonce
from .bridge import run as bridge
from .hid import HidDonor, enumerate_hid, select_path
from .r5 import list_r5, monitor
from .r5_input import preview
from .linux_ffb import MAX_FFB_GAIN


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="moza-ps5", description="MOZA R5 / Pico / Raion diagnostics")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("list-donors")
    p = sub.add_parser("probe-raion", help="perform one complete donor feature-report exchange")
    p.add_argument("--path"); p.add_argument("--vid", type=lambda x: int(x, 0), default=0); p.add_argument("--pid", type=lambda x: int(x, 0), default=0)
    p = sub.add_parser("bridge"); p.add_argument("--serial", default="/dev/serial0"); p.add_argument("--donor-path", required=True); p.add_argument("--baud", type=int, default=1_000_000)
    p.add_argument("--press-ps", action="store_true", help="experimental 500 ms neutral PS-button pulse on each verified v3 USB mount")
    p.add_argument("--r5-input", help="opt-in R5 evdev path, for example /dev/input/event5")
    p.add_argument("--r5-map", help="custom captured input-map JSON; requires --r5-input")
    p.add_argument("--outputs-dry-run", action="store_true", help="decode complete G29 output; no hardware output")
    p.add_argument("--ffb", action="store_true", help="opt-in stage-one R5 constant force; default gain 10 percent")
    p.add_argument("--ffb-gain", type=float, default=.10, help=f"force scale 0..{MAX_FFB_GAIN:.2f}")
    p.add_argument("--ffb-invert", action="store_true", help="diagnostic override: reverse the R5-verified default polarity")
    p.add_argument("--ffb-damper", action="store_true", help="experimental G29 damper translation; requires --ffb")
    p.add_argument("--r5-leds", help="R5 USB CDC serial path for the ESX rev bar")
    p = sub.add_parser("list-r5")
    p = sub.add_parser("monitor-r5"); p.add_argument("path")
    p = sub.add_parser("preview-r5", help="preview mapped R5 inputs without sending them to the Pico")
    p.add_argument("path"); p.add_argument("--r5-map")
    args = ap.parse_args(argv)
    if args.command == "doctor":
        print(json.dumps({"python": sys.version.split()[0], "hid_devices": len(enumerate_hid()), "r5_event_devices": list_r5()}, indent=2))
    elif args.command == "list-donors":
        for d in enumerate_hid(): print(f"{d.path}\t{d.vendor_id:04x}:{d.product_id:04x}\t{d.manufacturer} {d.product}")
    elif args.command == "probe-raion":
        info = select_path(args.path, args.vid, args.pid); donor = HidDonor(info.path)
        try:
            result = sign_nonce(donor); print(json.dumps({"path": info.path, "vid": f"{info.vendor_id:04x}", "pid": f"{info.product_id:04x}", "nonce_id": result.nonce_id, "signature_bytes": len(result.signature_blob), "elapsed_s": result.elapsed_s, "f2_crc_mismatches": result.f2_crc_mismatches, "f1_crc_mismatch_pages": list(result.f1_crc_mismatch_pages)}, indent=2))
        finally: donor.close()
    elif args.command == "bridge":
        if args.ffb_damper and not args.ffb: ap.error("--ffb-damper requires --ffb")
        if args.r5_map and not args.r5_input: ap.error("--r5-map requires --r5-input")
        options = {"press_ps": args.press_ps}
        if args.r5_input is not None: options.update(r5_path=args.r5_input, r5_map=args.r5_map)
        if args.ffb or args.r5_leds or args.outputs_dry_run:
            if (args.ffb or args.r5_leds) and not args.r5_input: ap.error("hardware outputs require --r5-input")
            if args.outputs_dry_run and (args.ffb or args.r5_leds): ap.error("dry-run cannot open hardware outputs")
            options.update(ffb=args.ffb, ffb_gain=args.ffb_gain, ffb_invert=args.ffb_invert,
                           led_path=args.r5_leds, outputs_dry_run=args.outputs_dry_run)
            if args.ffb_damper: options['ffb_damper'] = True
        return bridge(args.serial, args.donor_path, args.baud, **options)
    elif args.command == "list-r5": print(json.dumps(list_r5(), indent=2))
    elif args.command == "monitor-r5": monitor(args.path)
    elif args.command == "preview-r5": return preview(args.path, args.r5_map)
    return 0
