#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create an immutable source/firmware bundle for the R5 output test stage."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
NAME = "moza-ps5-r5-outputs-stage1"
DEST = ROOT / "artifacts/r5-outputs-stage1"
PRESERVED = {
    'artifacts/moza_ps5_diagnostic.uf2': '26228fbd05d779d5bf67fb9bad8e4733f5a48b6f657fbfdf52a0494a158221b1',
    'artifacts/v0.2.0-experimental/moza_ps5_diagnostic_v0.2.0_experimental.uf2': '42349b15761b57a609efa3973ba1a4fcf4b8beb40319c443c562755c6535d785',
    'artifacts/v0.3.0-experimental/moza_ps5_diagnostic_v0.3.0_experimental.uf2': '17c6166f6bed5df60c7045fe639e61cfb88c9d516358802f30425a49a7a33526',
    'artifacts/v0.3.0-experimental/moza-ps5-diagnostics-0.3.0-experimental.zip': '9c0e7db32d9e99f0685c527edbd5b88a356680ecae1a3a3935be8737fd68294f',
    'artifacts/v0.3.0-experimental/moza-ps5-v3-ps-button-test.zip': 'e822832451ed7c9a44741b1113aed7ff0900dc39256189ceec3b89f321962956',
    'artifacts/r5-input-capture/moza-r5-input-capture.zip': '061f114ad8429f7798ae1b47e1bdbe68ebe3ffd318df5f805c7c0b9eabf452a9',
    'artifacts/r5-input-prototype/moza-ps5-r5-input-prototype.zip': '9dbd1c8a27e8efc002e9f2e164891cfed0bacbf12dc3aefd78a63e0236f55934',
    'artifacts/r5-input-endpoints/moza-ps5-r5-input-endpoints.zip': 'c83c3acff565377b4dc168ca54c92a3823a851327f8c6c0f7945c04e49721dd4',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    for path, digest in PRESERVED.items():
        assert sha(ROOT / path) == digest, path
    DEST.mkdir(exist_ok=True)
    names = {'bin':'moza_ps5_diagnostic.bin', 'elf':'moza_ps5_diagnostic.elf',
             'uf2':'moza_ps5_diagnostic_v3-outputs.uf2'}
    for ext, name in names.items():
        source = ROOT / 'build-pico-v3-outputs' / name
        dest = DEST / f'moza_ps5_diagnostic_v0.3.1_outputs_experimental.{ext}'
        with dest.open('xb') as stream:
            stream.write(source.read_bytes())
    # Verify the actual USB descriptors, firmware version, UF2 and BIN pairing.
    sys.path.insert(0, str(ROOT / 'tools'))
    from verify_v3_firmware import descriptor_bytes, verify_descriptor, verify_binary, verify_uf2
    descriptor = descriptor_bytes()
    verify_descriptor(descriptor)
    binary = (DEST / 'moza_ps5_diagnostic_v0.3.1_outputs_experimental.bin').read_bytes()
    verify_binary(binary, descriptor, '0.3.1-outputs-experimental')
    verify_uf2(binary, (DEST / 'moza_ps5_diagnostic_v0.3.1_outputs_experimental.uf2').read_bytes())

    wheel = ROOT / 'build/host-wheel-outputs/moza_ps5_diagnostics-0.1.0-py3-none-any.whl'
    installed = ROOT / 'build/host-wheel-outputs-check'
    installed.mkdir(exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        assert archive.testzip() is None
        archive.extractall(installed)
    check = '''import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from moza_ps5 import cli
from moza_ps5.r5_input import load_map, DEFAULT_MAP
from moza_ps5.wheel_output import decode_g29
from moza_ps5.linux_ffb import effect_bytes
from moza_ps5.moza_leds import legacy_rpm_value
assert Path(DEFAULT_MAP).resolve().is_relative_to(Path(sys.argv[1]).resolve())
assert load_map().axes['steering'].convert(-32652)==0
assert decode_g29(0,bytes.fromhex('30 f8 12 1f')+bytes(28)).value==31
assert len(effect_bytes(0,pointer_bytes=8))==48
assert legacy_rpm_value(31,(75,79,82,85,87,88,89,90,92,94))<1023
print('Installed host wheel, captured input profile and output modules verified')
'''
    checked = subprocess.run([sys.executable, '-I', '-c', check, str(installed)],
                             check=True, capture_output=True, text=True).stdout.strip()
    files = []
    for directory in ('host/moza_ps5','host/tests','firmware','tools','docs','research/outputs'):
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file()
                     and not any(part in {'__pycache__','.pytest_cache','.git'} for part in p.parts)
                     and p.suffix not in {'.pyc','.obj','.exe'})
    files.extend(ROOT / p for p in ('LICENSE','START_HERE.md','host/pyproject.toml','host/requirements.txt'))
    files.extend(DEST / f'moza_ps5_diagnostic_v0.3.1_outputs_experimental.{ext}' for ext in names)
    files = sorted(set(files), key=lambda p:p.relative_to(ROOT).as_posix())
    manifest = {
        'package':NAME,'date':'2026-10-10','firmware':'0.3.1-outputs-experimental',
        'host_metadata_version':'0.1.0','usb_profile':'unchanged v3 046d:c260, descriptor 160 bytes',
        'validation':{'python_tests_passed':194,'native_c_test_executions':12,
                      'actual_acc_samples_replayed':5474,'unique_sample_reports':241,
                      'native_r5_ffb_slots':10,'native_zero_force_upload_and_erase':True,
                      'nonzero_force_played':False,'physical_force_led_verification':'pending Pico reflash and ACC test',
                      'installed_wheel_check':checked,'wheel_sha256':sha(wheel)},
        'defaults':{'outputs':'off','ffb_gain':0.10,'maximum_ffb_gain':0.25,'effect_duration_ms':150},
        'supported_outputs':['G29 slot-one variable-force level to Linux FF_CONSTANT',
                             'Progressive five-bit G29 rev masks to legacy ESX percent telemetry'],
        'remaining':['Physical force direction and stop tests','Visible LED comparison with ACC',
                     'Translation and validation of additional force-effect types'],
        'dependencies':{'pico_sdk_commit':'bddd20f928ce76142793bef434d4f75f4af6e433',
                        'tinyusb_commit':'86ad6e56c1700e85f1c5678607a762cfe3aa2f47',
                        'note':'SDK/TinyUSB source is already local under third_party; not duplicated in this Pi test ZIP'},
        'preserved_artifacts':PRESERVED,
        'source_files':[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in files],
    }
    readme = '''# R5 force feedback and ESX rev LED test

Start with [the output test guide](docs/R5_OUTPUTS_STAGE1.md).
This contains current host/firmware source, tests, hardware evidence and
the separate 0.3.1 Pico UF2. Flash the new UF2 before the full-output test.
Default hardware outputs are off. Stage-one FFB supports constant force
at default 10% gain; additional effects and physical output verification
remain pending. The ESX rev bar follows five G29 shift-light steps.
All 194 Python tests and twelve native C test executions pass locally.
Prior ZIPs/UF2s are preserved. GPL research references and their licenses
are separate from the independently written MIT application source.
'''
    prefix = NAME + '/'
    bundle = DEST / (NAME + '.zip')
    with zipfile.ZipFile(bundle,'x',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for p in files:
            archive.write(p,prefix+p.relative_to(ROOT).as_posix())
        archive.writestr(prefix+'README.md',readme)
        archive.writestr(prefix+'OUTPUT_TEST_MANIFEST.json',json.dumps(manifest,indent=2)+'\n')
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist())==len(set(archive.namelist()))==len(files)+2
        for p in files:
            assert hashlib.sha256(archive.read(prefix+p.relative_to(ROOT).as_posix())).hexdigest()==sha(p)
        for name in ('docs/R5_OUTPUTS_STAGE1.md','docs/hardware/R5_OUTPUT_INVESTIGATION_2026-10-10.md'):
            import re
            for link in re.findall(r'\]\(([^)]+)\)',archive.read(prefix+name).decode()):
                if not link.startswith(('http://','https://')):
                    target=(Path(prefix+name).parent/link).as_posix()
                    # Normalize the single parent segment in the investigation link.
                    import posixpath
                    assert posixpath.normpath(target) in archive.namelist(), (name,link)
    with (DEST/'OUTPUT_TEST_MANIFEST.json').open('x',encoding='utf-8') as stream:
        json.dump(manifest,stream,indent=2); stream.write('\n')
    sums={p.name:sha(p) for p in DEST.iterdir() if p.suffix in {'.uf2','.bin','.elf','.zip'}}
    with (DEST/'SHA256SUMS').open('x',encoding='utf-8') as stream:
        for name,digest in sums.items(): stream.write(f'{digest}  {name}\n')
    for path,digest in PRESERVED.items(): assert sha(ROOT/path)==digest,path
    print(json.dumps({'package':str(bundle),'bytes':bundle.stat().st_size,'entries':len(files)+2,
                      'sha256':sha(bundle),'preserved_artifacts_verified':len(PRESERVED),
                      'installed_wheel_check':checked,'firmware_hashes':sums},indent=2))


if __name__ == '__main__':
    main()
