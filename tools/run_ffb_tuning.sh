#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
set -o pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 2
capture=${1:?capture prefix required}
gain=${2:?gain required}
donor=${3:?verified donor path required}
if [[ -e "$capture.log" || -e "$capture.exit.json" ]]; then
    echo 'Existing evidence preserved; choose a new prefix.' >&2
    exit 2
fi
.venv/bin/python -u probes/probe_ffb_tuning.py \
    --gain "$gain" \
    --event /dev/input/by-id/usb-Gudsen_MOZA_R5_Base_3F001C000651333031333639-if02-event-joystick \
    --led /dev/serial/by-id/usb-Gudsen_MOZA_R5_Base_3F001C000651333031333639-if00 \
    --donor "$donor" 2>&1 | tee "$capture.log"
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
