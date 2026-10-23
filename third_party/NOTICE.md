# Third-party material

Application code is MIT licensed; this does not relicense its build dependencies or external research references.

`firmware/ps_report_descriptor.h` contains a PS4/G29 report descriptor adapted from OpenStickCommunity GP2040-CE at commit `3d1f32f7d02d418826b725b60208278d3be878c3`. The wheel-definition constant in `firmware/core/usb_reports.c` comes from the same source. Its original MIT license is retained in `GP2040-CE-LICENSE`.

`firmware/ps_report_descriptor_v3.h` is a subset of that licensed descriptor with Joystick as top-level usage. Its protocol bytes were checked against jfedor2/wheel-adapter commit `f31fd4706c5a21a79c14406f61776e09ca801fb0`. This project uses a separate Pi/UART architecture; no upstream PIO USB-host implementation is included.

Firmware builds use Pico SDK 2.1.1, commit `bddd20f928ce76142793bef434d4f75f4af6e433`, and TinyUSB submodule commit `86ad6e56c1700e85f1c5678607a762cfe3aa2f47`. These trees are fetched locally, not committed here. Their licenses remain with their respective repositories. See `docs/LOCAL_DEVELOPMENT.md` for pinned checkout/build instructions.

Linux, new-lg4ff, Boxflat and moza-rev were protocol/ABI research references. Historical research checkouts are not redistributed. Primary source links and scope are in `docs/PROTOCOL_REFERENCES.md`.

The firmware presents Logitech G29 USB identifiers for compatibility testing. This independent prototype is not produced or endorsed by Logitech, Sony, Razer or MOZA.
