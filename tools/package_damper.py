#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Package the offline-verified damper experiment without replacing old builds."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from package_ffb_fix import PRESERVED as EARLIER

ROOT = Path(__file__).resolve().parents[1]
NAME = 'moza-ps5-r5-outputs-damper1'
DEST = ROOT / 'artifacts/r5-outputs-damper1'
PRESERVED = {**EARLIER,
    'artifacts/r5-outputs-ffbfix1/moza-ps5-r5-outputs-ffbfix1.zip':
    'fec74ae5792da4176982682e0ec236db5d051484bedc0b5325207fea10a7c840'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    for name, digest in PRESERVED.items():
        assert sha(ROOT / name) == digest, name
    results = ET.parse(ROOT / 'build/damper-tests.xml').getroot()
    suites = list(results.iter('testsuite'))
    assert suites and not any(int(s.attrib[key]) for s in suites for key in ('failures', 'errors', 'skipped'))
    tests = sum(int(s.attrib['tests']) for s in suites)
    assert tests >= 254
    wheel = ROOT / 'build/host-wheel-damper1/moza_ps5_diagnostics-0.1.0-py3-none-any.whl'
    abi = json.loads((ROOT / 'docs/hardware/r5-damper-native-abi-2026-10-20.json').read_text())
    assert abi['python_matches_c'] and not abi['device_opened']
    assert abi['source_sha256'] == sha(ROOT / 'tools/probe_ffb_abi.c')
    check = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from moza_ps5 import cli, linux_ffb, wheel_output, output_worker
from moza_ps5.r5_input import load_map, DEFAULT_MAP
assert Path(cli.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
assert Path(DEFAULT_MAP).resolve().is_relative_to(Path(sys.argv[1]).resolve())
assert load_map().axes['steering'].convert(-32652)==0
command=wheel_output.decode_g29(0,bytes.fromhex('30 81 0c 05 00 05 00 01')+bytes(24))
assert command.kind=='damper' and command.slots==8
condition=linux_ffb.scaled_damper(command.damper,.1)
assert condition==linux_ffb.DamperCondition(1024,1024,25)
assert linux_ffb.damper_bytes(linux_ffb.DamperCondition(123,-456,1024),7,8).hex()==sys.argv[2]
assert linux_ffb.scaled_force(127,.10)==-3251
worker=output_worker.OutputWorker(dry_run=True)
worker.set_ready(True,1); worker.submit(command,1)
assert worker.force is None and not worker.dampers and worker.thread is None
assert worker.counts['damper']==1
print('Isolated wheel imports, packaged input profile, damper decode/ABI, constant polarity and dry-run verified')
'''
    with tempfile.TemporaryDirectory(prefix='damper-wheel-', dir=ROOT / 'build') as directory:
        with zipfile.ZipFile(wheel) as archive:
            assert archive.testzip() is None
            archive.extractall(directory)
        checked = subprocess.run([sys.executable, '-I', '-c', check, directory, abi['damper_hex']],
                                 check=True, capture_output=True, text=True).stdout.strip()
    files = []
    for directory in ('host/moza_ps5', 'host/tests', 'firmware', 'tools', 'docs',
                      'research/outputs', 'research/led-retest', 'research/ffb-polarity',
                      'research/damper-2026-10-20'):
        files.extend(p for p in (ROOT / directory).rglob('*') if p.is_file()
                     and not any(part in {'__pycache__', '.pytest_cache', '.git'} for part in p.parts)
                     and p.suffix not in {'.pyc', '.obj', '.exe', '.pdf'})
    files.extend(ROOT / p for p in ('README.md', 'START_HERE.md', 'LICENSE',
                                   'host/pyproject.toml', 'host/requirements.txt'))
    firmware = ROOT / 'artifacts/r5-outputs-stage1/moza_ps5_diagnostic_v0.3.1_outputs_experimental.uf2'
    files.append(firmware)
    files = sorted(set(files), key=lambda p: p.relative_to(ROOT).as_posix())
    manifest = {'package': NAME, 'date': '2026-10-20', 'host_metadata_version': '0.1.0',
                'firmware': 'unchanged 0.3.1 outputs; no reflash required',
                'firmware_sha256': sha(firmware), 'python_tests_passed': tests,
                'damper_sample_reports': 48, 'damper_sample_logs': 10,
                'native_abi': abi, 'isolated_wheel_check': checked, 'wheel_sha256': sha(wheel),
                'damper_hardware_verified': False, 'pi_install_changed': False,
                'defaults': {'outputs': 'off', 'damper': 'off; --ffb-damper requires --ffb',
                             'gain': .10, 'maximum_gain': .25, 'native_duration_ms': 150},
                'limitations': ['Conservative coefficient-bin translation; physical force equivalence unmeasured',
                                'G29 PS4 clipping behavior, damper feel/release and frozen-process expiry unverified',
                                'EVENT fixture is sampled, not a full lifecycle trace',
                                'SDK/TinyUSB remain local under third_party, not duplicated in this Pi bundle',
                                'Manufacturer protocol PDF remains local; immutable URL/hash in research sources.json'],
                'preserved_artifacts': PRESERVED,
                'source_files': [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size,
                                  'sha256': sha(p)} for p in files]}
    DEST.mkdir(exist_ok=False)
    prefix = NAME + '/'
    bundle = DEST / (NAME + '.zip')
    with zipfile.ZipFile(bundle, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, prefix + path.relative_to(ROOT).as_posix())
        archive.write(wheel, prefix + 'dist/' + wheel.name)
        archive.writestr(prefix + 'DAMPER_MANIFEST.json', json.dumps(manifest, indent=2) + '\n')
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist())) == len(files) + 2
        for path in files:
            assert hashlib.sha256(archive.read(prefix + path.relative_to(ROOT).as_posix())).hexdigest() == sha(path)
    with (DEST / 'DAMPER_MANIFEST.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2)
        stream.write('\n')
    with (DEST / 'SHA256SUMS').open('x', encoding='utf-8') as stream:
        stream.write(f'{sha(bundle)}  {bundle.name}\n')
    for name, digest in PRESERVED.items():
        assert sha(ROOT / name) == digest, name
    print(json.dumps({'package': str(bundle), 'sha256': sha(bundle), 'bytes': bundle.stat().st_size,
                      'entries': len(files) + 2, 'tests': tests, 'isolated_wheel': checked,
                      'preserved_artifacts': len(PRESERVED), 'hardware_outputs_started': False}, indent=2))


if __name__ == '__main__':
    main()
