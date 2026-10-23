# Architecture

The adapter separates console USB behavior from Linux device handling. It is not a transparent USB cable: the Pico emulates a G29-compatible wheel, and the Pi translates supported controls and output commands.

## Components and source ownership

| Component | Responsibility | Source |
| --- | --- | --- |
| Pico USB device | Descriptors, feature requests, IN/OUT reports and mount/suspend events | `firmware/main.c`, `usb_descriptors.c`, `usb_profile.h` |
| Portable firmware core | Framing, authentication state, neutral reports, diagnostics and output transport | `firmware/core/` |
| Pi bridge loop | UART sessions, status, input scheduling and donor-job dispatch | `host/moza_ps5/bridge.py` |
| Donor client | Raion HID feature reports and challenge-response pagination | `host/moza_ps5/hid.py`, `auth.py` |
| Input source | Read-only evdev, snapshots, resynchronization and JSON normalization | `host/moza_ps5/r5_input.py`, `profiles/` |
| Output decoder | Supported Logitech commands and independent damper slots | `host/moza_ps5/wheel_output.py` |
| Output worker | Freshness checks, latest force/LED state and shared force budget | `host/moza_ps5/output_worker.py` |
| Output backends | Finite Linux force effects; verified R5 USB serial LED framing | `linux_ffb.py`, `moza_leds.py` |
| Pit Control | Fixed HTTP actions, child process, status/log aggregation | `dashboard/server.py`, `controller.py`, `bridge_child.py` |

## Input path: wheel to console

1. The R5 exposes a Linux joystick event interface. `LinuxR5Source` verifies USB identity and configured axis/button capabilities.
2. Events commit at `SYN_REPORT` boundaries. `SYN_DROPPED` invalidates partial state; a fresh ioctl snapshot restores it. Periodic snapshots check liveness even when a pedal is held steady.
3. The JSON map converts raw controls into a 12-byte payload: four 16-bit axes, a 16-bit button mask, a hat and a reserved byte.
4. The bridge sends it every 20 ms when the verified v3 USB/link state is ready. Missing/invalid input becomes neutral.
5. The Pico expands it into the selected USB input report. In v3 this is ID 1 plus 63 payload bytes. Input becomes neutral after 200 ms without a fresh packet.

`--press-ps` supplies a neutral PS-button pulse per verified mount/link recovery: one-second settling, a 500 ms press, then release. It does not press PS continuously. The default map also assigns ESX Home to PS for later manual presses.

## Authentication: console to donor and back

The PS5 sends authentication feature requests to the Pico. Firmware collects challenge pages, tracks the current authentication generation and sends the challenge to the Pi. A single-worker authentication executor talks to the real Raion while UART/input servicing continues.

The host validates lengths, report IDs, nonce identity, busy/ready state, page order and timeouts. It returns the donor response for the current session/token; old-generation responses cannot satisfy a new challenge. Pico serves it back through USB feature reports.

Compatibility handling tolerates the observed Raion F2 status CRC mismatch and absent/zero F1 CRC fields; a nonzero F1 checksum still must match. Structural checks remain active. A completed donor exchange alone is not proof that the console accepted the wheel.

There is no embedded authentication private key or offline shortcut. The donor must remain connected and answer fresh challenges.

## Outputs: game to wheel

The v3 **output-stream** firmware forwards complete USB OUT reports with a sequence number and device timestamp. The host rejects malformed reports, sequence gaps and queued data estimated older than 50 ms. USB/link status gates outputs. Sampled `EVENT` records are diagnostic logs only.

The decoder accepts the captured ACC 32-byte transfer form and supported report-ID 5 form. It recognizes implemented constant force, four-slot damper lifecycle, play/stop operations and the five-bit LED mask. Unsupported force replacements release affected state rather than being interpreted as a different effect.

A worker thread handles native output separately from donor/UART work. Constant force becomes Linux `FF_CONSTANT`; damper coefficients/clipping become `FF_DAMPER` on the steering axis. Constant force and active dampers share the configured host force budget. This bounds encoding; it does not establish equivalent physical torque between G29 and R5.

The ESX bar uses the R5 USB CDC serial interface. A game mask becomes a verified literal LED pattern. The base's full-bar behavior supplies the shift blink. There is no absolute RPM to recover when the mask is zero.

## Freshness and release

| Layer | Behavior |
| --- | --- |
| Pico input watchdog | Neutral input after 200 ms without a new input packet |
| UART session | COBS framing, CRC32, protocol version, session and authentication-token checks |
| Complete output gate | Link/profile capability checks, sequence continuity, bounded length and device-time freshness |
| Worker heartbeat | Readiness expires after 100 ms without bridge renewal |
| Force state | Constant/damper commands expire after 150 ms without applicable refresh |
| Linux effects | Each played effect has a finite 150 ms duration; continued force requires renewal |
| LED state | Game mask expires after one second; invalid readiness clears it sooner |
| Normal bridge stop | Stop/erase effects, release LED control, close devices, attempt neutral input |

Unmount, suspend, parser errors, new boot/session and invalid output state invalidate the worker. A frozen bridge cannot renew readiness; a killed process cannot renew finite native effects. Base-configured friction/damping and idle LED animations may remain after game output ends.

This depends on the driver/base honoring effect lifetimes. The tested rig passed subjective 10% release checks; unit tests do not establish a universal physical stop-time guarantee.

## Dashboard lifecycle

The dashboard is Python standard-library HTTP with plain HTML/CSS/JavaScript. It launches the root `start-bridge.sh`, keeping paths and explicit output options outside HTTP requests. There is no Node build or cloud service.

A controller lock serializes starts. The saved launcher holds Linux `flock`, shared by browser and saved terminal launches. An externally started session is reported as EXTERNAL; direct CLI launches do not acquire this launcher lock.

Stop sends SIGINT, waits up to seven seconds, then uses bounded TERM/KILL fallbacks. A Linux parent-death SIGKILL guard prevents the managed child surviving a crashed dashboard parent. The user systemd unit adds process cleanup/restart behavior. Dashboard startup never starts wheel output automatically.

The page polls status, marks stale telemetry unknown, retains 180 concise messages and exports that recent view. Full managed stdout/stderr rotates separately on disk. Closing a browser does not terminate the bridge.

HTTP accepts fixed actions only. Host/origin/token checks reduce cross-site and rebinding requests, but do not authenticate users. The shipped listener is plain HTTP on the LAN; reachable clients can operate it. An optional exact HTTPS-origin allowlist supports a protected reverse proxy with a different external port. It does not infer trust from forwarded headers. Public hostnames require an authentication layer such as Cloudflare Access; see the [dashboard guide](WEB_DASHBOARD.md#https-hostname-through-cloudflare-tunnel).

## Firmware profiles

| Build | Selection | Purpose |
| --- | --- | --- |
| Baseline 0.1.0 | Profile options off | Early authentication diagnostics |
| v2 0.2.0 | `MP_FIRMWARE_V2=ON` | Earlier compact-report experiment |
| v3 0.3.0 | `MP_FIRMWARE_V3=ON` | Current profile and inputs; sampled output diagnostics |
| v3 outputs 0.3.1 | `MP_FIRMWARE_V3=ON`, `MP_OUTPUT_STREAM=ON` | Current FFB, damping and LEDs |

V3 uses `046d:c260`, a 160-byte HID descriptor, OUT endpoint `0x03`, IN `0x84`, 64-byte endpoints and a 5 ms interval. These compatibility descriptors do not change physical power wiring.
