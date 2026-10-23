"""The hardware kill trigger must require arming and a successful force write."""
import importlib.util
from pathlib import Path

import pytest


def load_probe(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'probe_bridge_kill.py'
    spec = importlib.util.spec_from_file_location('bridge_kill_probe_test', path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    events = []
    monkeypatch.setattr(probe.LinuxConstantForce, '_play', lambda self, value: events.append(('write', value)))
    monkeypatch.setattr(probe, 'emit', lambda action, **fields: events.append((action, fields)))
    monkeypatch.setattr(probe.os, 'getpid', lambda: 1234)
    monkeypatch.setattr(probe.os, 'kill', lambda pid, sig: events.append(('signal', pid, sig)))
    # The real probe runs on Linux; this suite also runs on Windows.
    monkeypatch.setattr(probe.signal, 'SIGKILL', 9, raising=False)
    device = probe.KillWitness.__new__(probe.KillWitness)
    device.effect_id = 0
    device.witness_deadline = 1.15
    return probe, device, events


@pytest.mark.parametrize('armed,level,play,should_kill', [
    (False, 800, 1, False),
    (True, 0, 1, False),
    (True, 800, 0, False),
    (True, -800, 1, True),
])
def test_sigkill_requires_arming_and_completed_nonzero_play(monkeypatch, armed, level, play, should_kill):
    probe, device, events = load_probe(monkeypatch)
    device.witness_level = level
    if armed:
        probe.armed.set()
    device._play(play)
    assert events[0] == ('write', play)
    if should_kill:
        assert events[1][0] == 'sigkill-after-nonzero-play'
        assert events[1][1]['native_play_write_completed'] is True
        assert events[1][1]['level'] == level
        assert events[2] == ('signal', 1234, probe.signal.SIGKILL)
    else:
        assert events == [('write', play)]


def test_failed_native_write_cannot_claim_witness_or_trigger_kill(monkeypatch):
    probe, device, events = load_probe(monkeypatch)
    device.witness_level = 800
    probe.armed.set()
    def fail(self, value):
        raise OSError('native write failed')
    monkeypatch.setattr(probe.LinuxConstantForce, '_play', fail)
    with pytest.raises(OSError, match='native write failed'):
        device._play(1)
    assert events == []
