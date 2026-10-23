# Configuration and mappings

Stop the bridge before editing configuration. Input mapping changes are host-only. USB/report-layout changes require a firmware rebuild and Pico flash.

## Device paths and saved launch command

`tools/start_bridge_current.sh` is the source template for the root `start-bridge.sh`. Its paths describe the original test rig. Replace:

- `--donor-path`: exact Raion HID path from `list-donors`.
- `--r5-input`: event node from `list-r5`, preferably its `if02-event-joystick` by-id symlink.
- `--r5-leds`: R5 `if00` USB CDC serial by-id path.
- `--serial`: Pi GPIO UART, normally `/dev/serial0`.

Edit both the installed launcher and template if using the dashboard installer, which refreshes the launcher from the template. `./start-bridge.sh --check` prints arguments without opening devices. Keep it at the installation root so `.venv` and `.bridge.lock` resolve correctly.

Set `R5_INPUT` in `dashboard/controller.py` to the same event path for dashboard detection. That tile checks device existence; live input and PS5 state are separate telemetry. The controller/UI describe a fixed 25%/damper/LED profile. If you change it, update those labels too; they do not read back launcher arguments.

## Input profile

The default is [`r5-esx-srp-lite.json`](../host/moza_ps5/profiles/r5-esx-srp-lite.json), loaded by `r5_input.py`. Required schema: `moza-r5-input-map-v1`, USB identity `346e:0004`.

Keep personal mappings in a copy:

```sh
mkdir -p ~/.config/moza-ps5
cp host/moza_ps5/profiles/r5-esx-srp-lite.json ~/.config/moza-ps5/my-wheel.json
nano ~/.config/moza-ps5/my-wheel.json
.venv/bin/python -m moza_ps5 preview-r5 /dev/input/eventN \
  --r5-map "$HOME/.config/moza-ps5/my-wheel.json"
```

Then add `--r5-map "$HOME/.config/moza-ps5/my-wheel.json"` alongside `--r5-input` in the saved command. Restart the bridge to load it; no flash is needed.

### Buttons

JSON `buttons` maps Linux **key-code strings** to PlayStation target names. Game actions are assigned separately in ACC.

| ESX label | Linux key code | Default target |
| --- | --- | --- |
| A / B / X / Y | 288 / 289 / 290 / 291 | `cross` / `circle` / `square` / `triangle` |
| Shift down / Shift up | 300 / 301 | `L1` / `R1` |
| WIP / N | 707 / 706 | `L2` / `R2` |
| Box / Start / Menu | 720 / 723 / 725 | `share` / `options` / `options` |
| Radio / Cam | 710 / 709 | `L3` / `R3` |
| Home | 712 | `PS` |
| D-pad up / right / down / left | 292 / 293 / 294 / 295 | `hat` directions |
| S1 / P / PL / R / S2 | 711 / 719 / 721 / 722 / 724 | Unassigned |

Menu sends **Options** for the game's menu/pause action; Start is an alias for the same target. No physical button is assigned Touchpad in the default map.

The report has fourteen button targets. Accepted names, with exact case:

```text
square cross circle triangle L1 R1 L2 R2 share options L3 R3 PS touchpad
```

For example, make S1 an additional Options button by adding `"711": "options"` and removing `711` from `unassigned`. Update `physical_labels` metadata if labels change. Two physical keys may share one target, but the game cannot distinguish them. D-pad keys must be distinct from each other and from mapped buttons.

### Axes and calibration

All four axes are required, each with a distinct absolute-axis code. The supplied capture uses steering **0**, throttle **2**, brake **5**, clutch **6**. Advertised ranges are `-32768..32767`; the live source verifies them against the profile.

| Field | Meaning |
| --- | --- |
| `code` | Linux absolute-axis code, not a button code |
| `minimum`, `maximum` | Device-advertised raw range |
| `released`, `pressed` | Pedal values measured at rest/full travel; may differ from advertised bounds |
| `release_deadzone` | Raw noise tolerance near pedal release |
| `endpoint_deadzone` | Raw endpoint tolerance to reach full output despite residuals |

Steering normalizes to `0` left, about `32768` center, `65535` right. Pedals encode `65535` released, `0` pressed. The supplied peaks and 256-unit endpoint tolerances were measured on one rig; recheck after calibration/hardware changes.

```sh
.venv/bin/python -m moza_ps5 monitor-r5 /dev/input/eventN
.venv/bin/python tools/capture_r5.py --help
```

The capture helper has an interactive sequence. Move one control at a time, keep captures outside the public repo and preview changes before driving.

`hardware.steering_rotation_degrees` is descriptive. It does not configure the base or game. Match Pit House rotation and ACC steering lock manually; the tested setup uses 900° total.

## Output options

| CLI option | Meaning |
| --- | --- |
| No output flags | Inputs/authentication only; output diagnostics may be discarded |
| `--outputs-dry-run` | Decode complete outputs without opening force/LED devices; incompatible with live output flags |
| `--ffb` | Enable constant force; requires `--r5-input` |
| `--ffb-gain 0.10` | Default gain; accepted range `0.00..0.30` |
| `--ffb-damper` | Add supported damper translation; requires `--ffb` |
| `--ffb-invert` | Reverse tested constant-force polarity for diagnostics; does not invert dampers |
| `--r5-leds PATH` | Enable ESX bar through R5 serial; requires `--r5-input` |
| `--press-ps` | Neutral PS pulse per verified mount/link recovery |

The preferred launcher uses `--ffb --ffb-gain 0.25 --ffb-damper`. Start initial checks at 10%; host gain combines with game/base settings. The cap is not an absolute torque measurement. Do not raise it merely to make subtle damping more obvious.

`linux_ffb.py` owns the cap, polarity, ABI and finite duration. `wheel_output.py` owns game-command interpretation. `output_worker.py` owns lifetimes and combined force budget. Changes there require regression tests and staged physical checks.

## Rev LEDs

The game supplies a five-bit shift indicator. `moza_leds.py` sends verified literal patterns rather than a percentage mistaken for RPM. Normal fill and end-of-bar blinking were checked on ESX. A dark bar until late revs can be what ACC requested.

There is no car RPM/redline available while the mask is zero. Earlier RPM-based fill needs another telemetry source or an approximate animation policy. Current behavior follows the game without per-car configuration.

## Firmware and dashboard

USB identity, HID layout, endpoints and Pico report-packing changes require rebuild/BOOTSEL flash. Editing source alone does not update firmware. Verify the descriptor/BIN/UF2 for the selected version.

Dashboard assets are under `dashboard/static/`, served from disk without a frontend build. Reload after static edits. Python changes require a service restart, which stops the managed bridge, so park first. See [dashboard maintenance](WEB_DASHBOARD.md).
