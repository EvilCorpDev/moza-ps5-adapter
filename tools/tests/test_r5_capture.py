import importlib.util
from pathlib import Path
import struct

import pytest
from moza_ps5 import r5

module_path = Path(__file__).resolve().parents[1] / "capture_r5.py"
spec = importlib.util.spec_from_file_location("capture_r5", module_path)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


@pytest.mark.parametrize("fmt,raw", [
    ("<iiHHi", "01000000 02000000 0300 1000 ffffffff"),
    ("<qqHHi", "0100000000000000 0200000000000000 0300 1000 ffffffff"),
])
def test_linux_abi_signed_hat_and_fragmented_reads(fmt, raw):
    # Fixed UAPI bytes: t=1.000002, EV_ABS, ABS_HAT0X, value=-1.
    decoder = capture.EventDecoder(fmt)
    packet = bytes.fromhex(raw)
    assert decoder.feed(packet[:3]) == []
    assert decoder.feed(packet[3:-1]) == []
    assert decoder.feed(packet[-1:]) == [[1, 2, 3, 16, -1]]
    assert not decoder.pending


@pytest.mark.parametrize("value", [-32768, -1, 0, 32767])
def test_existing_monitor_preserves_signed_values(tmp_path, capsys, value):
    path = tmp_path / "events.bin"
    path.write_bytes(struct.pack("@llHHi", 12, 34, 3, 0, value))
    r5.monitor(str(path))
    assert capsys.readouterr().out == f"12.000034 type=3 code=0 value={value}\n"


def test_capabilities_use_read_queries_and_signed_absinfo():
    keys = bytes(36) + b"\x01" + bytes(59)  # BTN_JOYSTICK, code 0x120
    replies = {
        0x80084502: bytes.fromhex("0300 6e34 0400 0001"),
        0x81004506: b"Gudsen MOZA R5 Base\0".ljust(256, b"\0"),
        0x80044520: bytes.fromhex("0b000000"),  # SYN, KEY, ABS
        0x80084523: bytes.fromhex("0100010000000000"),  # ABS_X, ABS_HAT0X
        0x80184540: bytes.fromhex("85ffffff 0080ffff ff7f0000 00000000 00000000 00000000"),
        0x80184550: bytes.fromhex("00000000 ffffffff 01000000 00000000 00000000 00000000"),
        0x80604521: keys,
        0x80604518: keys,
    }
    requests = []

    def ioctl(fd, request, buffer, mutate):
        assert fd == 42 and mutate
        assert request >> 30 == 2
        requests.append(request)
        buffer[:] = replies[request]
        return len(buffer)

    result = capture.capabilities(42, ioctl)
    assert set(requests) == set(replies)
    assert (result["vid"], result["pid"]) == ("346e", "0004")
    assert result["name"] == "Gudsen MOZA R5 Base"
    assert result["absolute_axes"]["0"] == {"value": -123, "minimum": -32768, "maximum": 32767, "fuzz": 0, "flat": 0, "resolution": 0}
    assert result["absolute_axes"]["16"]["minimum"] == -1
    assert result["key_codes"] == [288]
    assert result["pressed_keys"] == [288]
    assert result["relative_codes"] == []


def test_non_r5_identity_is_rejected_before_other_queries():
    def ioctl(fd, request, buffer, mutate):
        assert request == 0x80084502
        buffer[:] = bytes.fromhex("0300 6d04 60c2 0001")
    with pytest.raises(ValueError, match="selected device is 046d:c260"):
        capture.capabilities(42, ioctl)


def fake_reader(chunks):
    state = {"time": 0.0}
    chunks = list(chunks)

    def wait(readers, writers, errors, timeout):
        state["time"] += timeout
        return (readers if chunks else [], [], [])

    def read(fd, length):
        return chunks.pop(0)

    return lambda: state["time"], wait, read


def test_capture_quiet_window_finishes_without_events():
    phase = {}
    clock, wait, read = fake_reader([])
    capture.capture_window(42, .25, phase, clock=clock, wait=wait, read=read)
    assert phase == {"status": "complete", "events": [], "syn_dropped": 0}
    assert clock() == pytest.approx(.25)


def test_capture_keeps_signed_values_and_marks_sync_loss():
    raw = bytes.fromhex("01000000 02000000 0300 1000 ffffffff 01000000 03000000 0000 0300 00000000")
    clock, wait, read = fake_reader([raw[:7], raw[7:]])
    phase = {}
    capture.capture_window(42, .25, phase, clock=clock, wait=wait, read=read, event_format="<iiHHi")
    assert phase["events"] == [[1, 2, 3, 16, -1], [1, 3, 0, 3, 0]]
    assert phase["syn_dropped"] == 1
    assert phase["status"] == "repeat-required"


@pytest.mark.parametrize("data, message", [(b"", "disconnected"), (b"\x01", "partial input_event")])
def test_capture_errors_preserve_incomplete_phase(data, message):
    clock, wait, read = fake_reader([data])
    phase = {}
    with pytest.raises(OSError, match=message):
        capture.capture_window(42, .25, phase, clock=clock, wait=wait, read=read)
    assert phase["status"] == "incomplete"


def test_control_summary_includes_rest_values_and_direction():
    phase = {
        "before": {"absolute_axes": {"0": {"value": 0}, "16": {"value": 0}}},
        "events": [[1, 0, 3, 0, -32768], [1, 1, 3, 0, 32767], [1, 2, 3, 0, 0],
                   [1, 3, 1, 288, 1], [1, 4, 1, 288, 0], [1, 5, 0, 0, 0]],
    }
    result = capture.event_summary(phase)
    assert result["3:0"] == {"initial": 0, "minimum": -32768, "maximum": 32767, "last": 0, "events": 3}
    assert result["3:16"]["events"] == 0
    assert result["1:288"] == {"minimum": 0, "maximum": 1, "last": 0, "events": 2}
    assert "0:0" not in result


@pytest.mark.parametrize("value", ["0", "-1", "61", "nan", "inf"])
def test_capture_duration_cannot_be_unbounded(value):
    with pytest.raises(capture.argparse.ArgumentTypeError):
        capture.positive_seconds(value)


def test_single_phase_output_is_preserved_and_device_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(capture.sys, "platform", "linux")
    monkeypatch.setattr(capture.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(capture.os, "O_NONBLOCK", 2048, raising=False)
    opened, closed = [], []
    monkeypatch.setattr(capture.os, "open", lambda path, flags: opened.append((path, flags)) or 42)
    monkeypatch.setattr(capture.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(capture, "capabilities", lambda fd: {"name": "R5", "vid": "346e", "pid": "0004", "absolute_axes": {"0": {"value": 0}}})
    monkeypatch.setattr(capture, "discard_queued", lambda fd: None)

    def window(fd, duration, phase):
        phase.update(status="complete", events=[[1, 2, 3, 0, -123]], syn_dropped=0)
    monkeypatch.setattr(capture, "capture_window", window)
    output = tmp_path / "capture.json"
    assert capture.run("/dev/input/event5", output, label="steering", seconds=1) == 0
    document = capture.json.loads(output.read_text())
    assert document["phases"][0]["observed_controls"]["3:0"]["minimum"] == -123
    assert document["status"] == "complete"
    assert opened == [("/dev/input/event5", capture.os.O_RDONLY | 2048)]
    assert closed == [42]
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        capture.run("/dev/input/event5", output, label="steering", seconds=1)
    assert output.read_bytes() == original
    assert closed == [42, 42]
