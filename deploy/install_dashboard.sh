#!/bin/bash
# SPDX-License-Identifier: MIT
# Install the dashboard add-on into the already configured current bridge.
set -euo pipefail
source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
bridge_root="$HOME/moza-ps5-current"
unit_root="$HOME/.config/systemd/user"
[[ -d "$bridge_root/host" && -x "$bridge_root/.venv/bin/python" && ! -L "$bridge_root" ]] || {
    echo 'The configured ~/moza-ps5-current installation is required.' >&2; exit 2;
}
# Refuse an update while a bridge owns the wheel, and block concurrent starts.
exec 9>"$bridge_root/.bridge.lock"
flock --nonblock 9 || { echo 'Stop the bridge before updating the dashboard.' >&2; exit 3; }
[[ ! -L "$bridge_root/dashboard" ]] || { echo 'Dashboard target must be a real directory.' >&2; exit 2; }
mkdir -p "$bridge_root/dashboard" "$bridge_root/.maintenance" "$unit_root"
if [[ ! -e "$bridge_root/.maintenance/start-bridge.before-dashboard.sh" ]]; then
    cp -p "$bridge_root/start-bridge.sh" "$bridge_root/.maintenance/start-bridge.before-dashboard.sh"
fi
if [[ "$source_root" != "$bridge_root" ]]; then
    cp -a "$source_root/dashboard/." "$bridge_root/dashboard/"
fi
cp "$source_root/tools/start_bridge_current.sh" "$bridge_root/start-bridge.sh"
chmod 755 "$bridge_root/start-bridge.sh"
bash -n "$bridge_root/start-bridge.sh"
cp "$source_root/deploy/moza-ps5-dashboard.service" "$unit_root/moza-ps5-dashboard.service"
loginctl --no-ask-password enable-linger "$(id -un)"
systemd-analyze --user verify "$unit_root/moza-ps5-dashboard.service"
systemctl --user daemon-reload
systemctl --user enable moza-ps5-dashboard.service
systemctl --user restart moza-ps5-dashboard.service
printf '%s\n' 'Dashboard installed on port 8080. The bridge waits for browser Start.'
