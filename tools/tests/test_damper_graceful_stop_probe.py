"""Graceful shutdown must be armed and follow a successful live nonzero play."""
import importlib.util
from pathlib import Path

import pytest
from moza_ps5.linux_ffb import DamperCondition


def load(monkeypatch):
    directory = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location('graceful_probe_test', directory / 'probe_damper_graceful_stop.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('armed,value,condition,deadline,stops', [
    (False, 1, DamperCondition(3072, 3072, 6528), 1.1, False),
    (True, 0, DamperCondition(3072, 3072, 6528), 1.1, False),
    (True, 1, DamperCondition(0, 0, 6528), 1.1, False),
    (True, 1, DamperCondition(3072, 3072, 0), 1.1, False),
    (True, 1, None, 1.1, False),
    (True, 1, DamperCondition(3072, 3072, 6528), None, False),
    (True, 1, DamperCondition(3072, 3072, 6528), float('inf'), False),
    (True, 1, DamperCondition(3072, 3072, 6528), .9, False),
    (True, 1, DamperCondition(3072, 3072, 6528), 1.1, True),
])
def test_signal_only_after_armed_fresh_nonzero_native_play(monkeypatch, armed, value, condition, deadline, stops):
    probe = load(monkeypatch)
    actions = []
    monkeypatch.setattr(probe.cue.CueDamper, '_play', lambda self, value: actions.append(('play', value)))
    monkeypatch.setattr(probe.time, 'monotonic', lambda: 1.)
    monkeypatch.setattr(probe.os, 'getpid', lambda: 123)
    monkeypatch.setattr(probe.os, 'kill', lambda pid, sig: actions.append(('signal', pid, sig)))
    monkeypatch.setattr(probe.session, 'emit', lambda action, **fields: actions.append(('log', action, fields)))
    device = probe.GracefulStopDamper.__new__(probe.GracefulStopDamper)
    device.witness_condition, device.witness_deadline, device.effect_id = condition, deadline, 4
    if armed:
        probe.armed.set()
    device._play(value)
    assert actions[0] == ('play', value)
    if stops:
        assert actions[1] == ('signal', 123, probe.signal.SIGINT)
        assert actions[2][2]['native_play_write_completed']
        assert actions[2][2]['signal_requested_at'] < actions[2][2]['valid_until']
        assert not probe.armed.is_set()
    else:
        assert actions == [('play', value)]


def test_failed_play_cannot_request_shutdown(monkeypatch):
    probe = load(monkeypatch)
    def fail(self, value):
        raise OSError('native play failed')
    monkeypatch.setattr(probe.cue.CueDamper, '_play', fail)
    monkeypatch.setattr(probe.os, 'kill', lambda *args: pytest.fail('must not signal'))
    device = probe.GracefulStopDamper.__new__(probe.GracefulStopDamper)
    probe.armed.set()
    with pytest.raises(OSError, match='native play failed'):
        device._play(1)


def test_deadline_crossed_before_signal_keeps_trigger_armed(monkeypatch):
    probe = load(monkeypatch)
    ticks = iter([1., 1.2])
    monkeypatch.setattr(probe.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(probe.cue.CueDamper, '_play', lambda *args: None)
    monkeypatch.setattr(probe.os, 'kill', lambda *args: pytest.fail('must not signal stale force'))
    device = probe.GracefulStopDamper.__new__(probe.GracefulStopDamper)
    device.witness_condition = DamperCondition(3072, 3072, 6528)
    device.witness_deadline = 1.1
    probe.armed.set()
    device._play(1)
    assert probe.armed.is_set()
