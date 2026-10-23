#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Temporary browser acceptance server with a simulated process, never hardware."""
from pathlib import Path
import signal
import sys
import tempfile
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.controller import BridgeController
from dashboard.server import DashboardServer

DUMMY = """import signal,time,sys
signal.signal(signal.SIGINT,lambda *a:(print('Simulated bridge released; clean stop.',flush=True),sys.exit(0)))
print('bridge session UI-test; SIMULATED BRIDGE — no hardware access',flush=True)
print('R5 input live=True steering=32768',flush=True)
print('Pico status mounted=True donor_live=True',flush=True)
print('R5 outputs ready=True failed=False',flush=True)
while True:
 time.sleep(1)
 print('Pico status mounted=True donor_live=True',flush=True)
 print('R5 input live=True steering=32768',flush=True)
 print('R5 outputs ready=True failed=False',flush=True)
"""

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='pit-browser-test-') as folder:
        controller = BridgeController(folder, command=[sys.executable, '-u', '-c', DUMMY])
        controller.record('BROWSER ACCEPTANCE TEST: this port runs a simulated bridge only.', 'system')
        if '--external-transition' in sys.argv:
            observations = [0]
            def simulated_external_lock():
                observations[0] += 1
                return observations[0] <= 10
            controller.externally_running = simulated_external_lock
        server = DashboardServer(('0.0.0.0', 8081), controller)
        signal.signal(signal.SIGTERM, lambda *_: Thread(target=server.shutdown, daemon=True).start())
        print('Simulated dashboard test on port 8081', flush=True)
        try: server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt: pass
        finally: server.server_close(); controller.close()
