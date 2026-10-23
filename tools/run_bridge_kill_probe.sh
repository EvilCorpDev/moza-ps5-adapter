#!/bin/bash
# SPDX-License-Identifier: MIT
# Keep the shell alive to record the child/tee exit codes after SIGKILL.
set -u
set -o pipefail
python_path=$1
probe_path=$2
capture_prefix=$3
shift 3
if [[ -e "$capture_prefix.log" || -e "$capture_prefix.exit.json" ]]; then
    echo 'Existing kill-test evidence preserved; choose a new capture prefix.' >&2
    exit 2
fi
"$python_path" -u "$probe_path" "$@" 2>&1 | tee "$capture_prefix.log"
results=("${PIPESTATUS[@]}")
python3 - "$capture_prefix.exit.json" "${results[0]}" "${results[1]}" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
result = {'utc': datetime.now(timezone.utc).isoformat(),
          'child_shell_exit_code': int(sys.argv[2]), 'tee_exit_code': int(sys.argv[3]),
          'sigkill_exit_status_observed': int(sys.argv[2]) == 137 and int(sys.argv[3]) == 0,
          'note': 'Exit status must be paired with the nonzero-play witness and physical observation; release timing is not measured.'}
with Path(sys.argv[1]).open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
    stream.write('\n')
print(json.dumps(result), flush=True)
PY
exit "${results[0]}"
