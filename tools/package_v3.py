#!/usr/bin/env python3
"""Package the locally built v3 experiment without replacing older releases."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

from verify_v3_firmware import descriptor_bytes, verify_descriptor, verify_binary, verify_uf2

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "artifacts/v0.3.0-experimental"
PACKAGE = "moza-ps5-diagnostics-0.3.0-experimental"
SDK_COMMIT = "bddd20f928ce76142793bef434d4f75f4af6e433"
TINYUSB_COMMIT = "86ad6e56c1700e85f1c5678607a762cfe3aa2f47"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned_checkout(path: Path, expected: str) -> None:
    actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    changes = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
    if actual != expected or changes:
        raise ValueError(f"Dependency differs from clean pinned checkout: {path}")


def project_files() -> list[Path]:
    files = [ROOT / name for name in ("README.md", "START_HERE.md", "LICENSE", ".gitignore")]
    for directory in ("firmware", "host", "tools", "docs"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and not any(part in {".venv", "__pycache__", ".pytest_cache", ".git"} or part.endswith(".egg-info") for part in path.parts):
                if path.suffix in (".c", ".h", ".txt", ".py", ".toml", ".md", ".json", ".sh", ".ps1"):
                    files.append(path)
    files += [ROOT / "third_party/NOTICE.md", ROOT / "third_party/GP2040-CE-LICENSE"]
    return sorted(set(files), key=lambda path: path.relative_to(ROOT).as_posix())


def main() -> None:
    pinned_checkout(ROOT / "third_party/pico-sdk", SDK_COMMIT)
    pinned_checkout(ROOT / "third_party/pico-sdk/lib/tinyusb", TINYUSB_COMMIT)
    binary_path = ROOT / "build-pico-v3/moza_ps5_diagnostic.bin"
    uf2_path = ROOT / "build-pico-v3/moza_ps5_diagnostic_v3.uf2"
    firmware_sources = [p for p in (ROOT / "firmware").rglob("*") if p.is_file() and p.suffix in {".c", ".h", ".txt"}]
    if any(p.stat().st_mtime_ns > binary_path.stat().st_mtime_ns for p in firmware_sources):
        raise ValueError("Firmware source changed after the BIN build; run tools/build_pico.ps1 -Profile v3 first")
    descriptor = descriptor_bytes(); verify_descriptor(descriptor)
    binary = binary_path.read_bytes(); verify_binary(binary, descriptor)
    blocks = verify_uf2(binary, uf2_path.read_bytes())
    originals = {
        "artifacts/moza_ps5_diagnostic.uf2": "26228fbd05d779d5bf67fb9bad8e4733f5a48b6f657fbfdf52a0494a158221b1",
        "artifacts/v0.2.0-experimental/moza_ps5_diagnostic_v0.2.0_experimental.uf2": "42349b15761b57a609efa3973ba1a4fcf4b8beb40319c443c562755c6535d785",
    }
    for relative, expected in originals.items():
        if sha256(ROOT / relative) != expected: raise ValueError(f"Imported image changed: {relative}")
    RELEASE.mkdir(parents=True, exist_ok=True)
    release_files = []
    stem = "moza_ps5_diagnostic_v0.3.0_experimental"
    for extension, source in ((".bin", binary_path), (".uf2", uf2_path),
                              (".elf", ROOT / "build-pico-v3/moza_ps5_diagnostic.elf")):
        destination = RELEASE / (stem+extension)
        shutil.copyfile(source, destination); release_files.append(destination)
    descriptor_path = RELEASE / "g29_v3_report_descriptor.bin"
    descriptor_path.write_bytes(descriptor); release_files.append(descriptor_path)
    sources = project_files()
    manifest = {
        "version": "0.3.0-experimental", "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "board": "pico (original RP2040 Pico / Pico H)", "usb_profile": 3,
        "pico_sdk_commit": SDK_COMMIT, "tinyusb_commit": TINYUSB_COMMIT,
        "reference_commit": "f31fd4706c5a21a79c14406f61776e09ca801fb0",
        "toolchain": "Arm GNU Toolchain 13.2.Rel1; arm-none-eabi-gcc 13.2.1 20231009",
        "official_toolchain_zip_sha256": "51d933f00578aa28016c5e3c84f94403274ea7915539f8e56c13e2196437d18f",
        "cmake": "3.31.6-msvc6", "ninja": "1.12.1", "build_type": "Release",
        "build_command": "tools/build_pico.ps1 -Profile v3",
        "compile_warnings": "-Wall -Wextra -Werror", "uf2_blocks": blocks,
        "verification": ["42 Python tests passed", "6 MSVC native C test executions passed with /W4 /WX",
                         "baseline/v2/v3 RP2040 firmware builds passed", "compiled device/config/report descriptor bytes verified",
                         "UF2 RP2040 headers, addresses and payload match BIN"],
        "hardware_verification": "Pending: Linux enumeration and Pi/Pico/Raion/PS5 test; no PS5 acceptance claim",
        "source_files": [{"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in sources],
        "artifacts": [{"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in release_files],
        "preserved_original_images": originals,
        "package_exclusions": ["local SDK/TinyUSB source checkouts (pins above)", "local toolchain", "build directories",
                               "Python environment/caches", "historical ZIP archives", "upstream research checkout"],
    }
    manifest_path = RELEASE / "BUILD_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8"); release_files.append(manifest_path)
    sums_path = RELEASE / "SHA256SUMS"
    sums_path.write_text("".join(f"{sha256(p)}  {p.relative_to(ROOT).as_posix()}\n" for p in release_files), encoding="utf-8")
    release_files.append(sums_path)
    originals_files = [ROOT / relative for relative in originals] + [ROOT / "artifacts/BUILD_MANIFEST.txt", ROOT / "artifacts/SHA256SUMS"]
    packaged_files = sources + originals_files + release_files
    package_path = RELEASE / (PACKAGE+".zip")
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in packaged_files:
            archive.write(path, f"{PACKAGE}/{path.relative_to(ROOT).as_posix()}")
    with zipfile.ZipFile(package_path) as archive:
        if archive.testzip(): raise ValueError("ZIP CRC verification failed")
        if len(archive.namelist()) != len(set(archive.namelist())): raise ValueError("Duplicate ZIP entries")
        for path in packaged_files:
            packaged = archive.read(f"{PACKAGE}/{path.relative_to(ROOT).as_posix()}")
            if hashlib.sha256(packaged).hexdigest() != sha256(path):
                raise ValueError(f"ZIP source differs: {path}")
    zip_sha = sha256(package_path)
    (RELEASE / (PACKAGE+".zip.sha256")).write_text(f"{zip_sha}  {package_path.name}\n", encoding="utf-8")
    print(f"Packaged and SHA-256 verified {len(packaged_files)} files: {package_path}")
    print(f"ZIP sha256={zip_sha}")
    print(f"UF2 sha256={sha256(RELEASE / (stem+'.uf2'))}; {blocks} RP2040 blocks")
    print("Original baseline/v2 UF2 hashes remain unchanged")


if __name__ == "__main__":
    main()
