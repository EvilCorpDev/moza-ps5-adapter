#!/bin/bash
# SPDX-License-Identifier: MIT
# Install at the root of the current Pi bridge directory.
set -euo pipefail
bridge_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$bridge_root"
command=(
    "$bridge_root/.venv/bin/python" -u -m moza_ps5 bridge
    --serial /dev/serial0
    --donor-path 1-1.3:1.3
    --press-ps
    --r5-input /dev/input/by-id/usb-Gudsen_MOZA_R5_Base_3F001C000651333031333639-if02-event-joystick
    --ffb --ffb-gain 0.25 --ffb-damper
    --r5-leds /dev/serial/by-id/usb-Gudsen_MOZA_R5_Base_3F001C000651333031333639-if00
)
case "${1:-}" in
    --check)
        [[ $# == 1 ]] || { echo 'Usage: start-bridge.sh [--check]' >&2; exit 2; }
        printf '%q ' "${command[@]}"
        printf '\n'
        exit 0
        ;;
    '') [[ $# == 0 ]] || exit 2 ;;
    *) echo 'Usage: start-bridge.sh [--check]' >&2; exit 2 ;;
esac
# Share one lock with browser and terminal launches. Keep the process PID.
exec flock --nonblock --no-fork --conflict-exit-code 75 "$bridge_root/.bridge.lock" "${command[@]}"
