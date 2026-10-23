#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
set -o pipefail
cd /home/racer/moza-ps5-r5-outputs-damper1 || exit 2
mode=$1
capture=$2
case "$mode" in on|off|on-cue|freeze|fixed-unplug|fixed-freeze) ;; *) exit 2;; esac
if [[ -e "$capture.log" || -e "$capture.exit.json" ]]; then
    echo 'Existing evidence preserved; choose a new prefix.' >&2
    exit 2
fi
options=()
probe=probe_damper_session.py
if [[ "$mode" == on || "$mode" == on-cue ]]; then options+=(--damper); fi
if [[ "$mode" == on-cue ]]; then probe=probe_damper_unplug_cue.py; fi
if [[ "$mode" == freeze ]]; then probe=guard_damper_freeze.py; fi
if [[ "$mode" == fixed-unplug ]]; then probe=probe_fixed_damper_unplug.py; options+=(--damper); fi
if [[ "$mode" == fixed-freeze ]]; then probe=guard_damper_freeze.py; options+=(--fixed-damper); fi
.venv/bin/python -u "probes/$probe" \
    --event /dev/input/by-id/usb-Gudsen_MOZA_R5_Base_3F001C000651333031333639-if02-event-joystick \
    --led /dev/serial/by-id/usb-Gudsen_MOZA_R5_Base_3F001C000651333031333639-if00 \
    --donor 1-1.3:1.3 "${options[@]}" 2>&1 | tee "$capture.log"
results=("${PIPESTATUS[@]}")
python3 - "$capture.exit.json" "${results[0]}" "${results[1]}" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
result = {'utc': datetime.now(timezone.utc).isoformat(),
          'child_exit_code': int(sys.argv[2]), 'tee_exit_code': int(sys.argv[3])}
with Path(sys.argv[1]).open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
    stream.write('\n')
print(json.dumps(result), flush=True)
PY
exit "${results[0]}"
