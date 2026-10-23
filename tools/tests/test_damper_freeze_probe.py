"""Freeze requires an armed post-play witness; the independent guard never resumes."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from moza_ps5.linux_ffb import DamperCondition

TOOLS = Path(__file__).resolve().parents[1]


def load(name, monkeypatch):
    monkeypatch.syspath_prepend(str(TOOLS))
    spec = importlib.util.spec_from_file_location(name + '_test', TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('armed,play,condition,deadline,freeze', [
    (False, 1, DamperCondition(204, 204, 25), 1.1, False),
    (True, 0, DamperCondition(204, 204, 25), 1.1, False),
    (True, 1, DamperCondition(0, 0, 25), 1.1, False),
    (True, 1, DamperCondition(204, 204, 0), 1.1, False),
    (True, 1, DamperCondition(204, 204, 25), .9, False),
    (True, 1, DamperCondition(204, 204, 25), 1.1, True),
])
def test_stop_requires_successful_fresh_nonzero_play(monkeypatch, armed, play, condition, deadline, freeze):
    probe = load('probe_damper_freeze', monkeypatch)
    actions = []
    monkeypatch.setattr(probe.LinuxDamperForce, '_play', lambda self, v: actions.append(('play', v)))
    monkeypatch.setattr(probe.time, 'monotonic', lambda: 1.)
    monkeypatch.setattr(probe.os, 'getpid', lambda: 123)
    monkeypatch.setattr(probe.os, 'write', lambda fd, data: actions.append(('witness', json.loads(data))) or len(data))
    monkeypatch.setattr(probe.os, 'kill', lambda pid, sig: actions.append(('signal', pid, sig)))
    monkeypatch.setattr(probe.signal, 'SIGSTOP', 19, raising=False)
    monkeypatch.setattr(probe, 'emit', lambda *a, **kw: actions.append(('log', a, kw)))
    probe.guard_fd = 99
    if armed:
        probe.armed.set()
    device = probe.FreezeDamper.__new__(probe.FreezeDamper)
    device.effect_id = 4
    device.witness_condition, device.witness_deadline = condition, deadline
    if freeze:
        with pytest.raises(RuntimeError, match='never resumed'):
            device._play(play)
        assert actions[1][0] == 'witness' and actions[1][1]['native_play_write_completed']
        assert actions[1][1]['effect_id'] == 4
        assert actions[-1] == ('signal', 123, 19)
    else:
        device._play(play)
        assert actions == [('play', play)]


def test_failed_native_play_never_stops_process(monkeypatch):
    probe = load('probe_damper_freeze', monkeypatch)
    def fail(self, value):
        raise OSError('write failed')
    monkeypatch.setattr(probe.LinuxDamperForce, '_play', fail)
    monkeypatch.setattr(probe.os, 'kill', lambda *args: pytest.fail('must not signal'))
    device = probe.FreezeDamper.__new__(probe.FreezeDamper)
    probe.armed.set()
    with pytest.raises(OSError, match='write failed'):
        device._play(1)


def test_guard_rejects_zero_stale_wrong_pid_and_missing_witness(monkeypatch):
    guard = load('guard_damper_freeze', monkeypatch)
    good = {'pid': 123, 'native_play_write_completed': True, 'effect_id': 0,
            'left_coeff': 204, 'right_coeff': 204, 'saturation': 25,
            'played_at': 1., 'valid_until': 1.1}
    assert guard.valid_witness(good, 123, 1.01)
    for patch in ({'pid': 999}, {'saturation': 0}, {'native_play_write_completed': False},
                  {'valid_until': .9}, {'left_coeff': 0, 'right_coeff': 0}, {'played_at': .5}):
        assert not guard.valid_witness({**good, **patch}, 123, 1.01)
    assert not guard.valid_witness({}, 123, 1.01)


def test_guard_holds_all_threads_stopped_for_eight_seconds_then_kills_without_resume(monkeypatch):
    guard = load('guard_damper_freeze', monkeypatch)
    clock, actions = SimpleNamespace(now=1.01), []
    child = SimpleNamespace(pid=123, code=None)
    child.poll = lambda: child.code
    child.wait = lambda timeout: child.code
    def signal_child(fd, signum):
        actions.append(('signal', signum, clock.now))
        child.code = -9
    def select_tick(*args):
        clock.now += .05
        return [], [], []
    monkeypatch.setattr(guard.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(guard.select, 'select', select_tick)
    monkeypatch.setattr(guard, 'thread_states', lambda pid: {'123': 'T', '124': 'T'})
    monkeypatch.setattr(guard, 'emit', lambda action, **kw: actions.append((action, kw)))
    monkeypatch.setattr(guard.signal, 'pidfd_send_signal', signal_child, raising=False)
    monkeypatch.setattr(guard.signal, 'SIGKILL', 9, raising=False)
    assert guard.observe_frozen(child, 55, {'played_at': 1.}) == -9
    signals = [a for a in actions if a[0] == 'signal']
    assert len(signals) == 1 and signals[0][1] == 9 and signals[0][2] >= 9.01
    assert len([a for a in actions if a[0] == 'frozen-checkpoint']) == 3


def test_fixed_damper_only_renews_on_accepted_ps5_led_traffic(monkeypatch):
    monkeypatch.syspath_prepend(str(TOOLS))
    from damper_test_support import FixedDamperWorker, TEST_PARAMETERS
    from moza_ps5.wheel_output import WheelCommand
    worker = FixedDamperWorker(dry_run=True)
    worker.set_ready(True, 1)
    worker.submit(WheelCommand('constant', 127), 1)
    assert worker.force_value == 0 and not worker.damper_state.active()
    worker.submit(WheelCommand('leds', 0), 1.01)
    assert worker.damper_state.active() == {8: TEST_PARAMETERS}
    worker.set_ready(True, 1.10)
    worker.submit(WheelCommand('constant', 127), 1.10)
    worker.set_ready(True, 1.18)
    worker.tick(1.18)
    assert not worker.damper_state.active()  # LED traffic stopped; game force cannot renew it.
    worker.set_ready(False, 1.19)
    worker.submit(WheelCommand('leds', 0), 1.19)
    assert not worker.damper_state.active()


def test_fixed_test_condition_stays_within_ten_percent_and_has_no_constant_component(monkeypatch):
    monkeypatch.syspath_prepend(str(TOOLS))
    from damper_test_support import TEST_PARAMETERS
    from moza_ps5.linux_ffb import scaled_damper
    condition = scaled_damper(TEST_PARAMETERS, .1)
    assert condition == DamperCondition(3072, 3072, 6528)
    assert condition.saturation / 65535 < .1
