# Protocol references and provenance

Application logic is in this repository. External projects below informed compatibility research; they are not bundled application dependencies. Historical research checkouts and captured logs remain local.

## USB profile and donor behavior

- [OpenStickCommunity GP2040-CE](https://github.com/OpenStickCommunity/GP2040-CE/tree/3d1f32f7d02d418826b725b60208278d3be878c3), commit `3d1f32f7d02d418826b725b60208278d3be878c3`: source of the adapted report descriptor and wheel-definition constant. The upstream MIT license is retained in `third_party/GP2040-CE-LICENSE`.
- [jfedor2/wheel-adapter](https://github.com/jfedor2/wheel-adapter/tree/f31fd4706c5a21a79c14406f61776e09ca801fb0), commit `f31fd4706c5a21a79c14406f61776e09ca801fb0`: console-facing profile/control/report reference. This project's Pi/UART design does not port its PIO USB-host implementation.

The v3 HID descriptor is 160 bytes, SHA-256 `2e5a4573523b7f2d2c2ab22864d0c90b71d814b8b80c932f6a7c11144d50128d`. `tools/verify_v3_firmware.py` verifies its layout and compiled USB descriptors as well as UF2 family/address/payload consistency. A physical G29 descriptor was also supplied during investigation; this is an upstream-derived compatibility profile, not a retail firmware dump.

## Force feedback and LEDs

- [Linux Logitech driver](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/hid/hid-lg4ff.c): command interpretation reference.
- [new-lg4ff](https://github.com/berarma/new-lg4ff/blob/2092db19f7b40854e0427a1b2e39eda9f8d0c3cd/hid-lg4ff.c): damper coefficient/clipping quantization reference.
- [Linux universal PIDFF driver](https://github.com/torvalds/linux/blob/7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/hid/hid-universal-pidff.c): MOZA conditional-direction behavior reference.
- [Boxflat serial definitions](https://github.com/Lawstorant/boxflat/blob/d14ed0ed84ee2205d41df0f595af5f1709db7831/data/serial.yml): MOZA serial command reference.
- [moza-rev](https://github.com/francisdb/moza-rev/blob/6a3cf66c734dde29126e6d9b296adeab4479d005/src/moza.rs): serial LED telemetry reference.

Logitech force-feedback protocol V1.5 sections 3.1.1–4, 3.1.11 and 3.2.13/table 49 were checked during the damper investigation. Its parameter slopes are device-dependent. Current conversion is conservative encoding, not calibrated G29/R5 force equivalence.

Committed input/output fixtures are selected control/protocol samples from the test rig. Damper source hashes/line references preserve their original capture provenance without distributing complete authentication logs. An independent C probe of the Pi Linux ABI supplies a small native-layout fixture.

## Build dependencies

- [Pico SDK 2.1.1](https://github.com/raspberrypi/pico-sdk/tree/bddd20f928ce76142793bef434d4f75f4af6e433).
- [TinyUSB](https://github.com/hathach/tinyusb/tree/86ad6e56c1700e85f1c5678607a762cfe3aa2f47), the SDK-pinned submodule.

Their source trees and licenses are obtained with the pinned checkout commands in [the development guide](LOCAL_DEVELOPMENT.md). They are separate from this repository's MIT application license.
