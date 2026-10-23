#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Archive the specifically inventoried wheelpi installations; never start output.

Run --check first. --apply creates a current install with a fresh offline venv,
validates its launcher through a mocked bridge, then moves the previous folders
and ZIPs into an archive. No old file or log is deleted.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import venv

VERSIONS = (
    'moza-ps5-diagnostics-0.1.0', 'moza-ps5-diagnostics-0.2.0-experimental',
    'moza-ps5-diagnostics-0.3.0-experimental', 'moza-ps5-r5-input-endpoints',
    'moza-ps5-r5-input-prototype', 'moza-ps5-r5-outputs-damper1',
    'moza-ps5-r5-outputs-ffbfix1', 'moza-ps5-r5-outputs-gain30',
    'moza-ps5-r5-outputs-ledfix1', 'moza-ps5-r5-outputs-stage1',
    'moza-ps5-v3-ps-button-test',
)
PACKAGES = tuple(n + '.zip' for n in VERSIONS if n != 'moza-ps5-r5-outputs-gain30') + (
    'moza-ps5-diagnostics-0.1.0-logging-clean.zip',)
SOURCE = 'moza-ps5-r5-outputs-gain30'
CURRENT = 'moza-ps5-current'
WHEEL = 'moza_ps5_diagnostics-0.1.0-py3-none-any.whl'
WHEEL_SHA = 'fa01f03a9a0361aceb22b66a5eb8c3ac546351dc29ab61ec7cbe4012e7f2947a'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def tree(path):
    result = {}
    for p in sorted(path.rglob('*')):
        rel = p.relative_to(path).as_posix()
        mode = stat.S_IMODE(p.lstat().st_mode)
        if p.is_symlink(): result[rel] = {'type': 'symlink', 'target': os.readlink(p), 'mode': mode}
        elif p.is_file(): result[rel] = {'type': 'file', 'bytes': p.stat().st_size, 'sha256': sha(p), 'mode': mode}
        elif p.is_dir(): result[rel] = {'type': 'directory', 'mode': mode}
        else: raise RuntimeError('Unexpected special file: ' + str(p))
    return result


def no_outputs():
    rows = subprocess.check_output(['ps', '-eo', 'pid,comm,args'], text=True).splitlines()
    active = [s for s in rows if 'python' in s.lower() and any(t in s for t in ('moza_ps5', 'probe_ffb', 'probe_damper', 'guard_damper'))]
    assert not active, active
    result = subprocess.run(['fuser', '/dev/input/by-id/usb-Gudsen_MOZA_R5_Base_3F001C000651333031333639-if02-event-joystick',
        '/dev/serial/by-id/usb-Gudsen_MOZA_R5_Base_3F001C000651333031333639-if00', '/dev/serial0'], capture_output=True, text=True)
    assert not result.stdout.strip(), result.stdout


def write_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--launcher', required=True, type=Path)
    args = ap.parse_args()
    assert args.apply != args.check, 'Choose --check or --apply'
    home = Path('/home/racer')
    assert sys.platform == 'linux' and Path.home().resolve() == home
    current = home / CURRENT
    source = home / SOURCE
    archive = home / 'bridge-archives' / '2026-10-23'
    assert not current.exists() and not current.is_symlink(), current
    assert not archive.exists() and not archive.is_symlink(), archive
    assert not archive.parent.is_symlink() and archive.parent.resolve() == home / 'bridge-archives', archive.parent
    assert args.launcher.is_file()
    found = {p.name for p in home.glob('moza-ps5*')}
    assert found == set(VERSIONS) | set(PACKAGES), sorted(found)
    for name in VERSIONS + PACKAGES:
        p = home / name
        assert p.parent.resolve() == home and p.resolve() == p and not p.is_symlink(), p
        assert p.is_dir() if name in VERSIONS else p.is_file()
    assert sha(source / WHEEL) == WHEEL_SHA
    no_outputs()
    info_code = '''import importlib.metadata as m,json,sysconfig
rows={}
for name,version in (("pyserial","3.5"),("hidapi","0.15.0")):
 d=m.distribution(name);assert d.version==version
 rows[name]={"roots":sorted({str(p).split("/")[0] for p in d.files if str(p).split("/")[0] not in ("..",".")}),"scripts":[e.name for e in d.entry_points if e.group=="console_scripts"]}
print(json.dumps({"site":sysconfig.get_paths()["purelib"],"dependencies":rows}))'''
    dependencies = json.loads(subprocess.check_output([str(source / '.venv/bin/python'), '-I', '-c', info_code], text=True))
    plan = {'current': str(current), 'archive': str(archive), 'versions': list(VERSIONS), 'packages': list(PACKAGES),
            'gain': .25, 'damper': True, 'leds': True, 'fresh_venv': True, 'hardware_output_started': False}
    if args.check:
        print(json.dumps(plan, indent=2)); return 0
    snapshots = {name: tree(home / name) for name in VERSIONS}
    packages = {name: {'sha256': sha(home / name), 'bytes': (home / name).stat().st_size} for name in PACKAGES}
    print('Inventoried every old file and symlink; preparing current installation.', flush=True)
    current.mkdir()
    for child in source.iterdir():
        if child.name in ('.venv', WHEEL): continue
        dest = current / child.name
        if child.is_dir(): shutil.copytree(child, dest, symlinks=True)
        else: shutil.copy2(child, dest, follow_symlinks=False)
    (current / 'dist').mkdir()
    shutil.copy2(source / WHEEL, current / 'dist' / WHEEL)
    venv.create(current / '.venv', with_pip=True, symlinks=True)
    python = current / '.venv/bin/python'
    newsite = Path(subprocess.check_output([str(python), '-I', '-c', 'import sysconfig;print(sysconfig.get_paths()["purelib"])'], text=True).strip())
    oldsite = Path(dependencies['site'])
    assert oldsite.resolve().is_relative_to(source) and newsite.resolve().is_relative_to(current)
    for name, item in dependencies['dependencies'].items():
        for part in item['roots']:
            src, dst = oldsite / part, newsite / part
            assert src.resolve().is_relative_to(source) and not dst.exists()
            if src.is_dir(): shutil.copytree(src, dst, symlinks=True)
            else: shutil.copy2(src, dst)
        for command in item['scripts']:
            assert command in ('pyserial-miniterm', 'pyserial-ports')
            script = current / '.venv/bin' / command
            script.write_text(f'#!{python}\nimport sys\nfrom importlib.metadata import distribution\nsys.exit(next(e for e in distribution({name!r}).entry_points if e.group == "console_scripts" and e.name == {command!r}).load()())\n', encoding='utf-8')
            script.chmod(0o755)
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', str(current / 'dist' / WHEEL)], check=True)
    launcher = current / 'start-bridge.sh'
    shutil.copy2(args.launcher, launcher)
    launcher.chmod(0o755)
    subprocess.run(['bash', '-n', str(launcher)], check=True)
    command = shlex.split(subprocess.check_output([str(launcher), '--check'], text=True))
    assert command[:5] == [str(python), '-u', '-m', 'moza_ps5', 'bridge']
    verify = '''import importlib.metadata as m,json,pathlib,sys,serial,hid
from moza_ps5 import cli,linux_ffb
from moza_ps5.r5_input import DEFAULT_MAP,load_map
root=pathlib.Path(sys.argv[1]);command=json.loads(sys.argv[2]);called=[]
assert pathlib.Path(sys.prefix)==root/".venv"
for module in (cli,serial,hid): assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
assert linux_ffb.MAX_FFB_GAIN==.30 and linux_ffb.EFFECT_MS==150
assert pathlib.Path(DEFAULT_MAP).resolve().is_relative_to(root)
assert load_map().axes["steering"].convert(-32652)==0
cli.bridge=lambda *a,**kw: called.append((a,kw)) or 0
assert cli.main(command[4:])==0 and len(called)==1
pos,kw=called[0]
assert pos[0]=="/dev/serial0" and pos[1]=="1-1.3:1.3"
assert kw["ffb"] and kw["ffb_gain"]==.25 and kw["ffb_damper"] and kw["press_ps"]
assert not kw["ffb_invert"] and kw["led_path"].endswith("-if00") and kw["r5_path"].endswith("-if02-event-joystick")
print(json.dumps({"module":cli.__file__,"serial_module":serial.__file__,"hid_module":hid.__file__,"gain":kw["ffb_gain"],"damper":kw["ffb_damper"],"hardware_bridge_mocked":True,"pyserial":m.version("pyserial"),"hidapi":m.version("hidapi")}))'''
    verified = json.loads(subprocess.check_output([str(python), '-I', '-c', verify, str(current), json.dumps(command)], text=True))
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    subprocess.run([str(current / '.venv/bin/moza-ps5'), 'bridge', '--help'], check=True, stdout=subprocess.DEVNULL)
    (current / 'README_CURRENT.md').write_text('''# Current PS5 wheel bridge

Run `cd ~/moza-ps5-current` then `./start-bridge.sh` when ready at the wheel.
Leave Pico USB disconnected for startup, then reconnect it and select your user.
Use Ctrl+C to stop normally. Nothing starts automatically.

`./start-bridge.sh --check` prints the command without opening devices.
The launcher saves host gain 25%, damping enabled, normal rev LEDs, accepted
polarity, stable R5 device paths and current Raion path 1-1.3:1.3.
The generic Python CLI default remains 10%; use this launcher for the tested 25%.
Comparison baseline: Porsche 911 at Zandvoort, ACC gain 100%, damping 100%,
minimum force 0%. Check wheel control again after using gamepad menus.

Current logs remain under `logs/`. Older versions, packages and readable logs
are archived under `~/bridge-archives/2026-10-23/`; see its manifest and README.
Pico firmware is unchanged. This cleanup did not start any wheel output.
''', encoding='utf-8')
    no_outputs()
    archive.mkdir(parents=True)
    for name in ('versions', 'source-packages', 'logs'): (archive / name).mkdir()
    manifest = {**plan, 'created_utc': datetime.now(timezone.utc).isoformat(), 'status': 'prepared',
                'archive_method': 'atomic directory/file moves; originals kept intact, no deletion',
                'file_manifests': snapshots, 'package_manifests': packages,
                'current_verification': verified, 'launcher_command': command, 'launcher_sha256': sha(launcher),
                'moves_completed': [], 'readable_logs': []}
    manifest_path = archive / 'ARCHIVE_MANIFEST.json'
    write_json(manifest_path, manifest)
    for name in VERSIONS:
        for rel, item in snapshots[name].items():
            if item['type'] != 'file' or Path(rel).parts[0] != 'logs': continue
            src, dst = home / name / rel, archive / 'logs' / name / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            assert sha(dst) == item['sha256']
            manifest['readable_logs'].append({'original_version': name, 'path': str(dst.relative_to(archive)), 'sha256': item['sha256']})
    for name in VERSIONS + PACKAGES:
        src = home / name
        dst = archive / ('versions' if name in VERSIONS else 'source-packages') / name
        assert src.resolve() == home / name and not src.is_symlink() and not dst.exists()
        assert dst.parent.resolve().is_relative_to(archive)
        src.rename(dst)
        manifest['moves_completed'].append(name)
        write_json(manifest_path, manifest)
        print('Archived ' + name, flush=True)
    for name in VERSIONS:
        assert tree(archive / 'versions' / name) == snapshots[name], name
    for name, item in packages.items():
        assert sha(archive / 'source-packages' / name) == item['sha256'], name
    assert {p.name for p in home.glob('moza-ps5*')} == {CURRENT}
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    subprocess.run([str(current / '.venv/bin/moza-ps5'), 'bridge', '--help'], check=True, stdout=subprocess.DEVNULL)
    no_outputs()
    manifest.update(status='complete', verified_utc=datetime.now(timezone.utc).isoformat(),
        all_archived_files_and_symlinks_verified=True, remaining_home_installations=[CURRENT],
        current_runtime_sha256={p.relative_to(current / 'host').as_posix(): sha(p) for p in sorted((current / 'host/moza_ps5').rglob('*')) if p.is_file() and '__pycache__' not in p.parts},
        output_processes_running=False, output_device_handles_open=False)
    write_json(manifest_path, manifest)
    (archive / 'README.md').write_text('''# Archived Pi bridge versions, 2026-10-23

`versions/` contains all eleven previous directories, including their complete
source, environments and logs. `source-packages/` contains all eleven ZIPs.
`logs/` provides separately indexed readable copies of the original log folders.
ARCHIVE_MANIFEST.json records every original file hash, symlink, permissions,
package hash, completed move and current-launcher verification. No old file was
deleted or compressed. Archived environments retain their old absolute paths;
restore an archived version to its original /home/racer/<name> path before use.

Use /home/racer/moza-ps5-current/start-bridge.sh for the current 25% setup.
No wheel output or autostart was enabled by this cleanup.
''', encoding='utf-8')
    print(json.dumps({'status':'complete','current':str(current),'archive':str(archive),'archived_versions':len(VERSIONS),
        'archived_packages':len(PACKAGES),'readable_log_copies':len(manifest['readable_logs']),
        'manifest_sha256':sha(manifest_path),'launcher_sha256':sha(launcher),'gain':.25,'outputs_started':False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
