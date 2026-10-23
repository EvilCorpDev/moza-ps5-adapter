#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Extract sampled damper evidence; these EVENT lines are not a full stream."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r'name=output report_id=(0x[0-9a-f]+) length=(\d+) data=((?:[0-9a-f]{2} ?)+)')


def collect(directory: Path):
    sources, samples = [], []
    for path in sorted(directory.glob('*.log')):
        raw = path.read_bytes()
        rows = []
        for number, line in enumerate(raw.decode('utf-8-sig').splitlines(), 1):
            match = PATTERN.search(line)
            if not match:
                continue
            data = bytes.fromhex(match[3])
            if len(data) == int(match[2]) == 32 and data[0] == 0x30 and data[2] == 0x0c:
                rows.append({'line': number, 'report_id': int(match[1], 16), 'hex': data.hex()})
        if rows:
            index = len(sources)
            sources.append({'file': path.name, 'sha256': hashlib.sha256(raw).hexdigest()})
            samples.extend({'source': index, **row} for row in rows)
    return {'schema': 'g29-sampled-dampers-v1',
            'limitation': 'Sampled EVENT records; not a complete ordered output/effect lifecycle.',
            'sources': sources, 'samples': samples}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--logs', type=Path, default=ROOT / 'docs/hardware')
    ap.add_argument('--fixture', type=Path)
    args = ap.parse_args()
    result = collect(args.logs)
    if args.fixture:
        args.fixture.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    counts = Counter(bytes.fromhex(row['hex'])[:8].hex(' ') for row in result['samples'])
    print(json.dumps({'sources': len(result['sources']), 'samples': len(result['samples']),
                      'prefix_counts': counts, 'limitation': result['limitation']}, indent=2))


if __name__ == '__main__':
    main()
