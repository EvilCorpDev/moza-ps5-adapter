#!/bin/sh
# SPDX-License-Identifier: MIT
# Run manually with sudo once to grant the existing plugdev group donor access.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run this helper with sudo on the Pi.' >&2; exit 1; }
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
rule=/etc/udev/rules.d/70-moza-ps5-raion.rules
if [ -e "$rule" ] && ! cmp -s "$root/tools/70-moza-ps5-raion.rules" "$rule"; then
  echo "Existing rule differs: $rule. Review it before replacing it." >&2
  exit 1
fi
install -m 0644 "$root/tools/70-moza-ps5-raion.rules" "$rule"
udevadm control --reload-rules
udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=1532 --attr-match=idProduct=1100
udevadm settle
echo 'Raion 1532:1100 USB access is configured for the plugdev group.'
