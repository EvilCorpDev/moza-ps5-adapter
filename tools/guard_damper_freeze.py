#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Independent eight-second observation/kill guard; never resumes the bridge."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

OBSERVATION_SECONDS = 8.
TOTAL_SECONDS = 300.


def emit(action, **fields):
    record = {'utc': datetime.now(timezone.utc).isoformat(), 'monotonic': time.monotonic(),
              'freeze_guard': action, **fields}
    os.write(1, ('\n' + json.dumps(record) + '\n').encode())


def thread_states(pid):
    result = {}
    for path in Path(f'/proc/{pid}/task').glob('*/stat'):
        value = path.read_text()
        result[path.parent.name] = value[value.rfind(')') + 2:].split()[0]
    return result


def valid_witness(record, pid, now):
    return (record.get('pid') == pid and record.get('native_play_write_completed') is True
            and isinstance(record.get('effect_id'), int) and record['effect_id'] >= 0
            and record.get('saturation', 0) > 0
            and bool(record.get('left_coeff', 0) or record.get('right_coeff', 0))
            and record.get('played_at', now + 1) <= now < record.get('valid_until', 0)
            and now - record['played_at'] < .15)


def kill_child(child, pidfd, reason):
    if child.poll() is None:
        emit('sigkill', bridge_pid=child.pid, reason=reason)
        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
    code = child.wait(timeout=3)
    emit('bridge-exited', bridge_pid=child.pid, returncode=code)
    return code


def observe_frozen(child, pidfd, witness):
    deadline = time.monotonic() + .15
    while True:
        states = thread_states(child.pid)
        if states and all(state == 'T' for state in states.values()):
            break
        if child.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError('Could not verify every bridge thread stopped')
        time.sleep(.002)
    stopped_at = time.monotonic()
    delay = stopped_at - witness['played_at']
    emit('all-threads-stopped', bridge_pid=child.pid, threads=states,
         after_play_ms=delay * 1000, witness=witness,
         stopped_before_requested_effect_expiry=delay < .15)
    if delay >= .15:
        raise RuntimeError('Stop happened after the requested effect duration; invalid test')
    checkpoints = [1., 3., 7.]
    while time.monotonic() - stopped_at < OBSERVATION_SECONDS:
        readable, _, _ = select.select([sys.stdin], [], [], .05)
        if readable:
            command = sys.stdin.readline().strip()
            emit('operator-while-frozen', command=command)
            if command in ('', 'quit', 'stop'):
                return kill_child(child, pidfd, 'operator-stop-during-freeze')
        elapsed = time.monotonic() - stopped_at
        if checkpoints and elapsed >= checkpoints[0]:
            states = thread_states(child.pid)
            emit('frozen-checkpoint', elapsed_seconds=elapsed, threads=states)
            if not states or not all(state == 'T' for state in states.values()):
                raise RuntimeError('Bridge no longer fully stopped during observation')
            checkpoints.pop(0)
    return kill_child(child, pidfd, 'eight-second-frozen-observation-complete')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--event', required=True)
    ap.add_argument('--led', required=True)
    ap.add_argument('--donor', required=True)
    ap.add_argument('--fixed-damper', action='store_true')
    args = ap.parse_args()
    # Require PID handles so a stale/reused numeric PID can never be signalled.
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise RuntimeError('This guardian requires Linux pidfd support')
    read_fd, write_fd = os.pipe()
    child = pidfd = None
    def interrupted(signum, frame):
        raise SystemExit(f'Guardian interrupted by signal {signum}')
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        command = [sys.executable, '-u', str(Path(__file__).with_name('probe_damper_freeze.py')),
                                  '--guard-fd', str(write_fd), '--guardian-pid', str(os.getpid()),
                                  '--event', args.event, '--led', args.led, '--donor', args.donor]
        if args.fixed_damper:
            command.append('--fixed-damper')
        child = subprocess.Popen(command, pass_fds=(write_fd,))
        os.close(write_fd)
        write_fd = None
        pidfd = os.pidfd_open(child.pid)
        emit('ready', guardian_pid=os.getpid(), bridge_pid=child.pid,
             observation_seconds=OBSERVATION_SECONDS, total_timeout_seconds=TOTAL_SECONDS)
        deadline = time.monotonic() + TOTAL_SECONDS
        pending = b''
        while child.poll() is None:
            if time.monotonic() >= deadline:
                return kill_child(child, pidfd, 'independent-total-timeout')
            readable, _, _ = select.select([read_fd, pidfd], [], [], .1)
            if read_fd in readable:
                data = os.read(read_fd, 4096)
                if not data:
                    break
                pending += data
                if b'\n' in pending:
                    witness = json.loads(pending.split(b'\n', 1)[0])
                    if not valid_witness(witness, child.pid, time.monotonic()):
                        raise RuntimeError('Invalid or stale native play witness')
                    return 0 if observe_frozen(child, pidfd, witness) == -signal.SIGKILL else 3
        code = child.wait(timeout=3)
        emit('bridge-exited-without-freeze', returncode=code)
        return code
    finally:
        if child is not None and child.poll() is None:
            if pidfd is not None:
                kill_child(child, pidfd, 'guardian-finally-cleanup')
            else:
                child.kill()
                child.wait(timeout=3)
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if pidfd is not None:
            os.close(pidfd)
        # Only after the bridge has exited: mark the end of the frozen window.
        # Never touch serial or force devices during the eight-second hold.
        if child is not None and child.poll() is not None:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
            from moza_ps5.moza_leds import LegacyLedBar
            try:
                bar = LegacyLedBar(args.led)
                try:
                    bar.apply(0)
                finally:
                    bar.close()
                emit('cue-dark-after-bridge-exit')
            except Exception as exc:
                emit('cue-cleanup-error', error=str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
