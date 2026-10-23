#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
for profile in baseline v2 v3 v3-outputs; do
  case "$profile" in
    baseline) set -- ;;
    v2) set -- -DMP_FIRMWARE_V2=1 ;;
    v3) set -- -DMP_FIRMWARE_V3=1 ;;
    v3-outputs) set -- -DMP_FIRMWARE_V3=1 -DMP_OUTPUT_STREAM=1 ;;
  esac
  output="$ROOT/build-host/$profile"
  mkdir -p "$output"
  for test in core_test usb_reports_test output_stream_test; do
    cc -std=c11 -Wall -Wextra -Werror "$@" -I"$ROOT/firmware/core" \
      "$ROOT/firmware/core/protocol.c" "$ROOT/firmware/core/auth.c" \
      "$ROOT/firmware/core/usb_reports.c" "$ROOT/firmware/core/diagnostics.c" \
      "$ROOT/firmware/core/output_stream.c" \
      "$ROOT/firmware/tests/$test.c" -o "$output/$test"
    "$output/$test"
  done
done
PYTHONPATH="$ROOT/host${PYTHONPATH:+:$PYTHONPATH}" "${PYTHON:-python3}" -m pytest -q "$ROOT/host/tests" "$ROOT/tools/tests"
