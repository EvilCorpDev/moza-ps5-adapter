"""Capture evidence, effect lifecycle and bounded native damper regression tests."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

from moza_ps5 import linux_ffb, output_worker
from moza_ps5.linux_ffb import DamperCondition, damper_bytes, scaled_damper, scaled_force
from moza_ps5.wheel_output import DamperParameters, DamperSlots, decode_g29

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((Path(__file__).with_name('fixtures') / 'g29_acc_dampers.json').read_text())


def command(body):
    return decode_g29(0, b'\x30' + body.ljust(31, b'\0'))


def damper(op=1, slots=8, left=5, right=5, clip=1, signs=(0, 0)):
    return command(bytes(((slots << 4) | op, 12, left, signs[0], right, signs[1], clip)))


def test_all_actual_damper_samples_preserve_independent_counts():
    assert len(FIXTURE['sources']) == 10 and len(FIXTURE['samples']) == 48
    counts = Counter()
    for row in FIXTURE['samples']:
        data = bytes.fromhex(row['hex'])
        result = decode_g29(row['report_id'], data)
        assert result.kind == 'damper' and result.operation == 1
        assert result.damper.clip == 1 and result.damper.left == result.damper.right
        assert result.slots in (4, 8)
        counts[result.slots, result.damper.left] += 1
    assert counts == {(4, 0): 9, (8, 0): 12, (8, 1): 2, (8, 2): 3, (8, 3): 3,
                      (8, 4): 3, (8, 5): 4, (8, 6): 5, (8, 7): 3, (8, 12): 4}


def test_optional_local_damper_capture_provenance():
    """Audit original captures when present; never require private logs in a clone."""
    paths = [ROOT / 'docs/hardware' / source['file'] for source in FIXTURE['sources']]
    if not all(path.is_file() for path in paths):
        pytest.skip('Original local hardware logs are not distributed')
    lines = []
    for source, path in zip(FIXTURE['sources'], paths):
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source['sha256']
        lines.append(raw.decode('utf-8-sig').splitlines())
    for row in FIXTURE['samples']:
        assert bytes.fromhex(row['hex']).hex(' ') in lines[row['source']][row['line'] - 1]


@pytest.mark.parametrize('operation', [0, 1, 12])
def test_damper_layout_distinguishes_left_right_signs_and_report_id(operation):
    result = damper(operation, slots=12, left=3, right=9, clip=254, signs=(1, 0))
    assert result.damper == DamperParameters(-3, 9, 254)
    assert result.slots == 12 and result.operation == operation
    normal = decode_g29(5, result.raw)
    assert normal == result


@pytest.mark.parametrize('body', [
    b'\x81\x0c\x10\0\x05\0\x01',
    b'\x81\x0c\x05\x02\x05\0\x01',
    b'\x81\x0c\x05\0\xff\0\x01',
    b'\x81\x0c\x05\0\x05\x80\x01',
    b'\x81\x0b\0\0\0\0\xff',
])
def test_reserved_fields_or_unsupported_effects_release_selected_slot(body):
    state = DamperSlots()
    state.submit(damper(), 1, .15)
    result = command(body)
    assert result.kind == 'unsupported-slots'
    state.submit(result, 1.01, .15)
    assert not state.active() and not state.downloaded


def test_nonzero_padding_cannot_create_force_and_releases_old_slot():
    data = b'\x30\x81\x0c\x05\0\x05\0\x01' + bytes(23) + b'\x01'
    result = decode_g29(0, data)
    assert result.kind == 'unknown' and result.slots == 8
    state = DamperSlots()
    state.submit(damper(), 1, .15)
    state.submit(result, 1.01, .15)
    assert not state.active()


def test_download_play_refresh_stop_and_multiple_masks_do_not_alias_native_ids():
    state = DamperSlots()
    state.submit(damper(op=0, slots=12), 1, .15)
    assert not state.active() and set(state.downloaded) == {4, 8}
    state.submit(command(b'\xc2'), 1.01, .15)
    assert set(state.active()) == {4, 8}
    state.submit(damper(op=12, slots=8, left=7), 1.02, .15)
    assert state.active()[4].left == 5 and state.active()[8].left == 7
    state.submit(command(b'\x83'), 1.03, .15)
    assert set(state.active()) == {4}
    state.submit(command(b'\x82'), 1.04, .15)
    assert set(state.active()) == {4, 8}
    state.submit(command(b'\xf3'), 1.05, .15)
    assert not state.active() and set(state.downloaded) == {4, 8}


def test_refresh_without_matching_playing_effect_does_not_start_or_remember_it():
    state = DamperSlots()
    state.submit(damper(op=12), 1, .15)
    state.submit(command(b'\x82'), 1.01, .15)
    assert not state.active() and not state.downloaded
    state.submit(damper(op=0), 1.02, .15)
    state.submit(damper(op=12), 1.03, .15)
    assert not state.active() and not state.downloaded


def test_leds_and_resumed_force_do_not_revive_expired_damper():
    state = DamperSlots()
    state.submit(damper(), 1, .15)
    state.submit(command(b'\xf8\x12\x1f'), 1.10, .15)
    state.submit(command(b'\x11\x08\x90\x01'), 1.16, .15)
    assert not state.active() and not state.downloaded
    state.submit(command(b'\x82'), 1.17, .15)
    assert not state.active()


@pytest.mark.parametrize('pointer,size', [(4, 44), (8, 48)])
def test_native_condition_layout_direction_axis_and_finite_duration(pointer, size):
    data = damper_bytes(DamperCondition(123, -456, 1024), 7, pointer)
    assert len(data) == size
    assert data[:16].hex() == '55000700004000000000960000000000'
    assert data[16:28].hex() == '0004000438fe7b0000000000'
    assert data[28:] == bytes(size - 28)  # No Y-axis damping or custom pointers.


def test_native_structure_matches_independent_pi_c_probe():
    path = Path(__file__).with_name('fixtures') / 'r5_damper_native_abi.json'
    probe = json.loads(path.read_text())
    assert probe['ff_effect_bytes'] == 48 and probe['condition_bytes'] == 12
    assert probe['condition_right_coeff_offset'] == 4 and probe['condition_left_coeff_offset'] == 6
    assert damper_bytes(DamperCondition(123, -456, 1024), 7, 8).hex() == probe['damper_hex']


def test_actual_small_clip_and_signed_coefficients_are_never_promoted_to_full_strength():
    assert scaled_damper(DamperParameters(5, 5, 1), .1) == DamperCondition(1024, 1024, 25)
    assert scaled_damper(DamperParameters(-12, 7, 255), .1) == DamperCondition(-2457, 1433, 6528)
    assert scaled_damper(DamperParameters(15, 15, 255), 0) == DamperCondition(0, 0, 0)


@pytest.mark.parametrize('gain', [0, .10, .25, .30])
@pytest.mark.parametrize('value', [-128, -70, 0, 80, 127])
def test_combined_constant_and_four_dampers_respect_total_force_budget(gain, value):
    level = scaled_force(value, gain)
    active = {bit: DamperParameters(15, -15, 255) for bit in (1, 2, 4, 8)}
    results = output_worker.bounded_dampers(active, gain, level)
    assert abs(level) / 32767 + sum(p.saturation / 65535 for p in results.values()) <= gain + 1 / 32767
    assert all(abs(p.left_coeff) <= int(15 * 2048 * gain) for p in results.values())


@pytest.fixture
def native(monkeypatch):
    state = SimpleNamespace(now=1., fds=[], closed=[], uploads=[], writes=[], erased=[],
                            allocated={}, effects={}, playing=set(), max_gain=None,
                            delay=False, damper=True, slots=10, fail_damper=False)
    def check_force_limit():
        total = 0.
        for effect_id in state.playing:
            data = state.effects[effect_id]
            if struct.unpack_from('<H', data)[0] == 82:
                total += abs(struct.unpack_from('<h', data, 16)[0]) / 32767
            else:
                total += struct.unpack_from('<H', data, 16)[0] / 65535
        if state.max_gain is not None:
            assert total <= state.max_gain + 1 / 32767
    monkeypatch.setattr(linux_ffb.sys, 'platform', 'linux')
    monkeypatch.setattr(linux_ffb.platform, 'machine', lambda: 'aarch64')
    monkeypatch.setattr(linux_ffb.os, 'O_NONBLOCK', 2048, raising=False)
    monkeypatch.setattr(linux_ffb.time, 'monotonic', lambda: state.now)
    def open_fd(path, flags):
        fd = 40 + len(state.fds)
        state.fds.append(fd)
        return fd
    def ioctl(fd, code, data, mutate=True):
        number = code & 255
        if number == 2:
            data[:] = struct.pack('@HHHH', 3, 0x346e, 4, 273)
        elif number == 0x35:
            data[:] = ((1 << 82) | ((1 << 85) if state.damper else 0)).to_bytes(16, 'little')
        elif number == 0x84:
            data[:] = struct.pack('@i', state.slots)
        elif number == 0x80:
            effect_type, effect_id = struct.unpack_from('<Hh', data)
            if state.fail_damper and effect_type == 85:
                raise OSError('damper upload failed')
            state.uploads.append((fd, bytes(data)))
            if effect_id == -1:
                effect_id = next(i for i in range(state.slots) if i not in state.allocated)
                state.allocated[effect_id] = fd
            assert state.allocated[effect_id] == fd
            struct.pack_into('<h', data, 2, effect_id)
            state.effects[effect_id] = bytes(data)
            check_force_limit()
            if state.delay:
                state.now += .2
        elif number == 0x81:
            assert state.allocated.pop(data) == fd
            state.playing.discard(data)
            state.erased.append((fd, data))
        else:
            raise AssertionError(code)
    monkeypatch.setattr(linux_ffb.os, 'open', open_fd)
    monkeypatch.setattr(linux_ffb.os, 'close', lambda fd: state.closed.append(fd))
    def write(fd, data):
        event = struct.unpack('@llHHi', data)[2:]
        _, effect_id, play = event
        state.writes.append((fd, event))
        if play:
            state.playing.add(effect_id)
        else:
            state.playing.discard(effect_id)
        check_force_limit()
        return len(data)
    monkeypatch.setattr(linux_ffb.os, 'write', write)
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(ioctl=ioctl))
    monkeypatch.setattr(output_worker.Thread, 'start', lambda self: None)
    return state


def test_native_damper_reuses_id_and_stops_erases_only_its_owned_effect(native):
    device = linux_ffb.LinuxDamperForce('r5')
    device.apply(DamperCondition(1024, 2048, 25), valid_until=1.1)
    device.apply(DamperCondition(300, 500, 25), valid_until=1.1)
    assert [struct.unpack_from('<h', data, 2)[0] for _, data in native.uploads] == [-1, 0]
    assert all(struct.unpack_from('<H', data, 0)[0] == 85 for _, data in native.uploads)
    device.close()
    assert native.writes == [(40, (21, 0, 1)), (40, (21, 0, 1)), (40, (21, 0, 0))]
    assert native.erased == [(40, 0)] and native.closed == [40]


def test_slow_damper_upload_is_erased_without_playback(native):
    native.delay = True
    device = linux_ffb.LinuxDamperForce('r5')
    device.apply(DamperCondition(1024, 1024, 25), valid_until=1.1)
    assert len(native.uploads) == 1 and not native.writes
    assert not native.allocated and not device.playing
    device.close()


def test_missing_damper_capability_closes_both_handles_before_any_upload(native):
    native.damper = False
    with pytest.raises(ValueError, match='0x55'):
        output_worker.OutputWorker(ffb_path='r5', damper=True)
    assert sorted(native.closed) == native.fds and not native.uploads


def test_insufficient_effect_slots_fail_before_output(native):
    native.slots = 3
    with pytest.raises(ValueError, match='four native effect slots'):
        output_worker.OutputWorker(ffb_path='r5', damper=True)
    assert native.closed == [40] and not native.uploads


def live_tick(worker, native, now):
    native.now = now
    worker.set_ready(True, now)
    worker.tick(now)


def test_damper_is_explicitly_opt_in_and_decoding_alone_cannot_play_it(native):
    worker = output_worker.OutputWorker(ffb_path='r5')
    worker.set_ready(True, 1)
    worker.submit(damper(), 1)
    worker.tick(1)
    assert native.fds == [40] and not native.uploads
    worker._close_devices()


def test_constant_updates_retain_damping_independent_slots_and_stop_masks(native):
    worker = output_worker.OutputWorker(ffb_path='r5', damper=True)
    worker.set_ready(True, 1)
    worker.submit(damper(slots=12), 1)
    worker.submit(command(b'\x11\x08\x90\x01'), 1)
    live_tick(worker, native, 1.01)
    assert worker.force.playing and set(worker.applied_dampers) == {4, 8}
    assert len(set([worker.force.effect_id, *worker.applied_dampers.values()])) == 3
    worker.submit(command(b'\x83'), 1.02)
    live_tick(worker, native, 1.03)
    assert set(worker.applied_dampers) == {4} and worker.force.playing
    worker.submit(command(b'\x13'), 1.04)
    live_tick(worker, native, 1.05)
    assert not worker.force.playing and set(worker.applied_dampers) == {4}
    worker.submit(command(b'\xf3'), 1.06)
    live_tick(worker, native, 1.07)
    assert not worker.applied_dampers and not native.allocated
    worker._close_devices()


def test_slot_zero_damper_replaces_constant_and_fresh_constant_replaces_it(native):
    worker = output_worker.OutputWorker(ffb_path='r5', damper=True)
    worker.set_ready(True, 1)
    worker.submit(command(b'\x11\x08\x90\x01'), 1)
    live_tick(worker, native, 1.01)
    worker.submit(damper(slots=1), 1.02)
    live_tick(worker, native, 1.03)
    assert not worker.force.playing and set(worker.applied_dampers) == {1}
    worker.submit(command(b'\x11\x08\x90\x01'), 1.04)
    live_tick(worker, native, 1.05)
    assert worker.force.playing and not worker.applied_dampers
    worker._close_devices()


def test_replacing_one_of_four_dampers_frees_slot_before_constant_allocation(native):
    native.slots = 4
    worker = output_worker.OutputWorker(ffb_path='r5', damper=True)
    worker.set_ready(True, 1)
    worker.submit(damper(slots=15), 1)
    live_tick(worker, native, 1.01)
    assert len(native.allocated) == 4
    worker.submit(command(b'\x11\x08\x90\x01'), 1.02)
    live_tick(worker, native, 1.03)
    assert worker.force.playing and set(worker.applied_dampers) == {2, 4, 8}
    assert len(native.allocated) == 4
    worker._close_devices()


@pytest.mark.parametrize('gain', [.10, .30])
def test_native_updates_respect_combined_limit_even_during_force_increases(native, gain):
    native.max_gain = gain
    worker = output_worker.OutputWorker(ffb_path='r5', damper=True, gain=gain)
    worker.set_ready(True, 1)
    worker.submit(damper(slots=14, left=15, right=15, clip=255), 1)
    live_tick(worker, native, 1.01)
    for now, encoded in [(1.02, 170), (1.04, 238), (1.06, 130), (1.08, 255), (1.10, 0)]:
        worker.submit(command(bytes((0x11, 0x08, encoded, 1))), now)
        live_tick(worker, native, now)
    assert worker.force.playing
    worker._close_devices()


@pytest.mark.parametrize('cause', ['disconnect', 'stale-bridge', 'stale-force'])
@pytest.mark.parametrize('gain', [.10, .30])
def test_each_release_path_erases_dampers_and_reconnection_cannot_restore_them(native, cause, gain):
    worker = output_worker.OutputWorker(ffb_path='r5', damper=True, gain=gain)
    worker.set_ready(True, 1)
    worker.submit(damper(slots=12), 1)
    live_tick(worker, native, 1.01)
    assert len(native.allocated) == 2
    native.now = 1.2
    if cause == 'disconnect':
        worker.set_ready(False, native.now)
    elif cause == 'stale-force':
        worker.set_ready(True, native.now)
    worker.tick(native.now)
    assert not native.allocated and not worker.damper_state.downloaded
    worker.set_ready(True, 1.21)
    worker.submit(command(b'\x11\x08\x80\x01'), 1.21)
    live_tick(worker, native, 1.22)
    assert not native.allocated
    worker.submit(command(b'\x82'), 1.23)
    live_tick(worker, native, 1.24)
    assert not native.allocated
    worker._close_devices()


def test_held_damper_is_refreshed_at_20hz_while_constant_force_keeps_stream_live(native):
    worker = output_worker.OutputWorker(ffb_path='r5', damper=True)
    worker.set_ready(True, 1)
    worker.submit(damper(), 1)
    live_tick(worker, native, 1.01)
    for now in (1.02, 1.08, 1.14, 1.20, 1.26):
        worker.submit(command(b'\x11\x08\x80\x01'), now)
        live_tick(worker, native, now)
    assert set(worker.applied_dampers) == {8}
    assert len(native.uploads) == 5
    assert all(struct.unpack_from('<H', data, 10)[0] == 150 for _, data in native.uploads)
    worker._close_devices()
    assert not native.allocated and sorted(native.closed) == native.fds


def test_worker_failure_releases_existing_constant_and_all_output_handles(native):
    worker = output_worker.OutputWorker(ffb_path='r5', damper=True)
    worker.set_ready(True, 1)
    worker.submit(command(b'\x11\x08\x90\x01'), 1)
    worker.submit(damper(), 1)
    native.fail_damper = True
    worker.shutdown = SimpleNamespace(wait=lambda duration: False)
    worker._run()
    assert worker.failed and not worker.ready and not native.allocated
    assert sorted(native.closed) == native.fds
    assert native.writes[-1][1] == (21, 0, 0)


def test_main_loop_recovery_cannot_revive_damper_if_output_thread_also_stalled(native):
    worker = output_worker.OutputWorker(ffb_path='r5', damper=True)
    worker.set_ready(True, 1)
    worker.submit(damper(), 1)
    live_tick(worker, native, 1.01)
    assert native.playing
    # No worker tick during the gap; model the main loop receiving data before
    # the worker resumes. Even though FORCE_TTL has not elapsed, BRIDGE_TTL has.
    worker.submit(command(b'\x11\x08\x80\x01'), 1.13)
    live_tick(worker, native, 1.13)
    assert not native.playing and not worker.damper_state.downloaded
    worker._close_devices()


def test_damper_option_requires_ffb_before_opening_devices():
    from moza_ps5 import bridge, cli
    with pytest.raises(ValueError, match='requires --ffb'):
        bridge.run('unused', 'unused', ffb_damper=True)
    with pytest.raises(SystemExit):
        cli.main(['bridge', '--donor-path', 'unused', '--ffb-damper'])
    with pytest.raises(ValueError, match='requires constant-force'):
        output_worker.OutputWorker(damper=True)
