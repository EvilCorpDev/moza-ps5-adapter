#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Keep a managed bridge from surviving an unexpected dashboard-parent death."""
import ctypes
import os
import signal
import sys


def main():
    if sys.platform != 'linux' or len(sys.argv) != 3:
        raise SystemExit('Bridge child guard requires Linux and a saved launcher.')
    expected_parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_PDEATHSIG. This runs in its own process, not Popen.preexec_fn.
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Could not install parent-death guard')
    if os.getppid() != expected_parent:
        raise SystemExit('Dashboard parent exited before bridge startup.')
    os.execv(sys.argv[2], [sys.argv[2]])


if __name__ == '__main__':
    main()
