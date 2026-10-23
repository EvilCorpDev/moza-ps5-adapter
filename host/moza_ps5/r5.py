"""Read-only Linux input diagnostics. No EV_FF writes or force-feedback ioctls."""
from __future__ import annotations
import glob
import os
import struct
from pathlib import Path

R5_VID, R5_PID = "346e", "0004"


def _sysfs(path: str, name: str) -> str:
    try:
        return Path(path, name).read_text().strip()
    except OSError:
        return ""


def list_r5() -> list[dict[str, str]]:
    found = []
    for event in glob.glob("/dev/input/event*"):
        name = _sysfs(f"/sys/class/input/{Path(event).name}/device", "name")
        uevent = Path(f"/sys/class/input/{Path(event).name}/device").resolve()
        parents = list(uevent.parents)
        vid = pid = ""
        for parent in parents:
            vid = vid or _sysfs(str(parent), "idVendor")
            pid = pid or _sysfs(str(parent), "idProduct")
        if vid.lower() == R5_VID and pid.lower() == R5_PID:
            found.append({"event": event, "name": name, "vid": vid, "pid": pid})
    return found


def monitor(path: str) -> None:
    # Linux input_event is timeval (two longs), type, code, value.
    # Linux input_event.value is __s32, including negative axes/hat values.
    fmt = "@llHHi"
    size = struct.calcsize(fmt)
    with open(path, "rb", buffering=0) as stream:
        while True:
            packet = stream.read(size)
            if len(packet) != size:
                break
            sec, usec, kind, code, value = struct.unpack(fmt, packet)
            print(f"{sec}.{usec:06d} type={kind} code={code} value={value}", flush=True)
