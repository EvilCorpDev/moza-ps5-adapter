# Development and reproducible builds

Run commands from the repository root. Runtime wheel access is intended for Linux on the Pi. Most unit tests run on Linux or Windows and use simulated hardware.

## Python

Python 3.10+ is required. The tested Pi used Python 3.13; Windows development used Python 3.14. Runtime dependencies are pinned; the development requirements also include pytest and cryptography for the standalone signature-verification diagnostic tests.

Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r host/requirements-dev.txt
.venv/bin/python -m pip install -e host
.venv/bin/python -m pytest -q host/tests tools/tests dashboard/tests
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r host/requirements-dev.txt
.venv\Scripts\python.exe -m pip install -e host
.venv\Scripts\python.exe -m pytest -q host/tests tools/tests dashboard/tests
```

Fixtures contain captured input/protocol samples, not controller private keys. Damper decoder and native ABI checks use committed fixtures. The optional raw-log provenance check requires the original local captures and skips on a normal clone. Linux-only signal, flock and parent-death tests skip on Windows.

## Portable C tests

The core tests use the machine's native C compiler, not ARM GCC. They cover framing/authentication, report bounds, neutral/input encoding and output-stream behavior in four profiles: baseline, v2, v3 and v3 outputs.

Linux, with a C11 compiler:

```sh
PYTHON=.venv/bin/python sh tools/run_tests.sh
.venv/bin/python -m pytest -q dashboard/tests
```

Windows, with MSVC Build Tools 2022:

```powershell
& tools/run_tests.ps1
.venv\Scripts\python.exe -m pytest -q dashboard/tests
```

`run_tests.ps1` accepts `-CompilerSetup` for a different `vcvars64.bat` and `-PythonPath` for a different environment. Both runners treat C warnings as errors. Their Python phase runs host/tools tests; the separate dashboard command includes its lifecycle/HTTP tests.

## Pico firmware

Dependencies:

| Dependency | Tested revision |
| --- | --- |
| Original Pico board | `PICO_BOARD=pico`, RP2040 |
| Pico SDK 2.1.1 | `bddd20f928ce76142793bef434d4f75f4af6e433` |
| TinyUSB | `86ad6e56c1700e85f1c5678607a762cfe3aa2f47` |
| Arm GNU Toolchain | 13.2.Rel1, GCC 13.2.1 |
| Build tools | CMake and Ninja; tested Windows versions 3.31.6 and 1.12.1 |

SDK and toolchain trees are ignored, not vendored into the application repository:

```sh
git clone --branch 2.1.1 --depth 1 https://github.com/raspberrypi/pico-sdk.git third_party/pico-sdk
git -C third_party/pico-sdk submodule update --init --depth 1 lib/tinyusb
git -C third_party/pico-sdk rev-parse HEAD
git -C third_party/pico-sdk/lib/tinyusb rev-parse HEAD
```

Compare the final two outputs with the table. Optional SDK wireless/network dependencies are unused.

On Linux, with `arm-none-eabi-gcc` available:

```sh
export PICO_SDK_PATH="$PWD/third_party/pico-sdk"
cmake -S firmware -B build-pico-v3-outputs -G Ninja \
  -DPICO_NO_PICOTOOL=1 -DMP_FIRMWARE_V3=ON -DMP_OUTPUT_STREAM=ON
cmake --build build-pico-v3-outputs
.venv/bin/python tools/bin_to_uf2.py \
  build-pico-v3-outputs/moza_ps5_diagnostic.bin \
  build-pico-v3-outputs/moza_ps5_diagnostic_v3-outputs.uf2
.venv/bin/python tools/verify_v3_firmware.py \
  --bin build-pico-v3-outputs/moza_ps5_diagnostic.bin \
  --uf2 build-pico-v3-outputs/moza_ps5_diagnostic_v3-outputs.uf2 \
  --version 0.3.1-outputs-experimental
```

Install CMake/Ninja and an ARM bare-metal C/C++ toolchain before configuring. If using an unpacked official Arm toolchain, add its `bin` directory to this shell's PATH or supply `-DPICO_TOOLCHAIN_PATH=/absolute/toolchain/root`.

On Windows, `tools/build_pico.ps1` defaults to a local `.tools/arm/arm-gnu-toolchain-13.2.Rel1-mingw-w64-i686-arm-none-eabi` and Visual Studio Build Tools 2022 CMake/Ninja. Supply explicit paths for your installation:

```powershell
& tools/build_pico.ps1 -Profile v3 -OutputStream `
  -ToolchainPath 'C:\tools\arm-gnu-toolchain-13.2.Rel1-mingw-w64-i686-arm-none-eabi' `
  -CMakePath 'C:\tools\cmake\bin\cmake.exe' `
  -NinjaPath 'C:\tools\ninja\ninja.exe'
```

The script converts UF2 and verifies v3 descriptors/version/payload automatically. The output is `build-pico-v3-outputs/moza_ps5_diagnostic_v3-outputs.uf2`.

| Profile | PowerShell | CMake flags |
| --- | --- | --- |
| Baseline | `-Profile baseline` | v2/v3/output flags OFF |
| v2 | `-Profile v2` | `MP_FIRMWARE_V2=ON` |
| v3 inputs | `-Profile v3` | `MP_FIRMWARE_V3=ON` |
| Current outputs | `-Profile v3 -OutputStream` | `MP_FIRMWARE_V3=ON`, `MP_OUTPUT_STREAM=ON` |

Use separate build directories. If reusing one, explicitly turn old flags OFF. V2/v3 are mutually exclusive; output streaming requires v3. Flash the resulting UF2 by BOOTSEL as described in the README. A successful build does not prove physical USB compatibility.

## Dashboard development

The production service uses the installation-root launcher. For a device-free UI acceptance server:

```sh
.venv/bin/python tools/probe_dashboard_ui.py --help
```

That helper uses a simulated bridge and separate acceptance port. Do not test Start/Stop against a physical output-enabled installation unless a driver is ready at the wheel. Static files require only reload; Python changes require a service restart.

## Publication boundary

`.gitignore` excludes raw logs, chat imports, local handoffs, historical packages, virtual environments, research clones, SDK sources and build outputs. Selected protocol fixtures and a dashboard screenshot are committed so tests/docs work on a fresh clone. No authentication keys are distributed.

`tools/package_*.py`, archive helpers and physical probes retain the project's experimental history. Some expect historical local files or test-rig paths; they are not required for normal setup. Use the README's source-based installation/build flow for a fresh machine.
