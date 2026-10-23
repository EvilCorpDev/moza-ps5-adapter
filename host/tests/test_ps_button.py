import struct
import pytest
from moza_ps5.ps_button import PSButtonPulse, neutral_input
from moza_ps5.protocol import HELLO, INPUT, STATUS, Packet, StreamParser
from moza_ps5 import bridge, cli


def status(flags=3, profile=3, schema=1, descriptor=160):
    result = bytearray(64)
    result[0] = flags
    result[32:36] = struct.pack("<BBH", profile, schema, descriptor)
    return bytes(result)


def test_neutral_ps_wire_payload():
    assert neutral_input() == bytes.fromhex("00 80 ff ff ff ff ff ff 00 00 08 00")
    assert neutral_input(True) == bytes.fromhex("00 80 ff ff ff ff ff ff 00 10 08 00")
    wire = Packet(INPUT, 123, payload=neutral_input(True)).encode()
    assert StreamParser().feed(wire) == [Packet(INPUT, 123, payload=neutral_input(True))]


@pytest.mark.parametrize("payload", [b"", bytes(8), bytes(32), status(flags=0), status(flags=1),
                                   status(flags=2), status(profile=1), status(profile=2),
                                   status(schema=2), status(descriptor=156)])
def test_unverified_or_unready_profiles_never_receive_ps(payload):
    pulse = PSButtonPulse()
    assert not pulse.status(payload, 0)
    assert pulse.tick(1.1) is None


def test_single_press_repeated_refresh_and_release():
    pulse = PSButtonPulse()
    assert pulse.status(status(), 0)
    assert pulse.tick(.999) is None
    assert pulse.tick(1) == (neutral_input(True), "press")
    assert pulse.tick(1.01) is None
    assert pulse.tick(1.03) == (neutral_input(True), "press")
    assert not pulse.status(status(), 1.2)
    assert pulse.tick(1.5) == (neutral_input(), "release")
    assert pulse.tick(1.53) == (neutral_input(), "release")
    assert pulse.tick(1.61) is None
    for at in (2, 3, 4):
        assert not pulse.status(status(), at)
        assert pulse.tick(at) is None


def test_unmount_cancels_press_and_new_mount_rearms():
    pulse = PSButtonPulse(); pulse.status(status(), 0); pulse.tick(1)
    assert not pulse.status(status(flags=2), 1.1)
    assert pulse.tick(1.2) is None
    assert pulse.status(status(), 1.3)
    assert pulse.tick(2.29) is None
    assert pulse.tick(2.31) == (neutral_input(True), "press")


def test_stale_status_stops_writes_until_new_ready_status():
    pulse = PSButtonPulse(); pulse.status(status(), 0); pulse.tick(1)
    assert pulse.tick(3) is None
    assert pulse.tick(4) is None
    assert pulse.status(status(), 5)
    assert pulse.tick(6) == (neutral_input(True), "press")


def test_suspend_gates_press_until_resume_and_new_status():
    pulse = PSButtonPulse(); pulse.status(status(), 0); pulse.tick(1)
    pulse.event(4)
    assert not pulse.status(status(), 1.1)
    assert pulse.tick(1.2) is None
    pulse.event(5)
    assert pulse.status(status(), 1.3)
    assert pulse.tick(2.31) == (neutral_input(True), "press")


def test_completed_pulse_does_not_repeat_after_suspend_resume():
    pulse = PSButtonPulse(); pulse.status(status(), 0)
    pulse.tick(1); pulse.tick(1.5); pulse.tick(1.61)
    pulse.event(4); pulse.status(status(), 2); pulse.event(5)
    assert not pulse.status(status(), 3)
    assert pulse.tick(3) is None


@pytest.mark.parametrize("event", [1, 2])
def test_mount_events_and_boot_clear_stale_ready_state(event):
    pulse = PSButtonPulse(); pulse.status(status(), 0); pulse.tick(1)
    pulse.event(event)
    assert pulse.tick(1.2) is None
    assert pulse.status(status(), 1.3)
    pulse.new_boot()
    assert pulse.tick(3) is None


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_flag_is_opt_in(monkeypatch, enabled):
    calls = []
    monkeypatch.setattr(cli, "bridge", lambda *args, **kwargs: calls.append((args, kwargs)) or 0)
    args = ["bridge", "--donor-path", "1-1.4:1.3"] + (["--press-ps"] if enabled else [])
    assert cli.main(args) == 0
    assert calls == [(("/dev/serial0", "1-1.4:1.3", 1_000_000), {"press_ps": enabled})]


@pytest.mark.parametrize("enabled", [False, True])
def test_bridge_writes_the_controlled_pulse_only_with_flag(monkeypatch, capsys, enabled):
    class Clock:
        now = 0.0
        def sleep(self, duration):
            self.now += .25
            if self.now > 2.5: raise KeyboardInterrupt
    class Link:
        def __init__(self): self.writes = []; self.first = True; self.closed = False
        def write(self, data): self.writes.append(data); return len(data)
        def read(self, count):
            data = Packet(STATUS, 123, payload=status()).encode()
            if self.first:
                self.first = False
                data = Packet(HELLO, 123, payload=struct.pack("<IHHBH", 1_000_000, 1, 0x300, 3, 160)).encode()+data
            return data
        def close(self): self.closed = True
    class Donor:
        def close(self): pass
    clock = Clock(); link = Link()
    monkeypatch.setattr(bridge.serial, "Serial", lambda *args, **kwargs: link)
    monkeypatch.setattr(bridge, "HidDonor", lambda path: Donor())
    monkeypatch.setattr(bridge.secrets, "randbits", lambda bits: 123)
    monkeypatch.setattr(bridge.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(bridge.time, "sleep", clock.sleep)
    assert bridge.run("fake-serial", "fake-donor", press_ps=enabled) == 0
    packets = StreamParser().feed(b"".join(link.writes))
    inputs = [p.payload for p in packets if p.kind == INPUT]
    if enabled:
        assert neutral_input(True) in inputs
        assert inputs[-1] == neutral_input()
        assert all(p in (neutral_input(True), neutral_input()) for p in inputs)
        log = capsys.readouterr().out
        assert log.count("PS-button pressed (500 ms)") == 1
        assert log.count("PS-button released") == 1
    else:
        assert inputs == []
    assert link.closed
