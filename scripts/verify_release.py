#!/usr/bin/env python3
"""Verify published content hashes and reject unsafe manifest paths."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    records = json.loads((ROOT / 'SHA256SUMS.json').read_text())
    errors = []
    provenance = json.loads((ROOT / 'PROVENANCE.json').read_text())
    required = [row['path'] for key in ('files', 'supplemental_aggregates')
                for row in provenance.get(key, [])]
    for name in required:
        if name not in records:
            errors.append(f'Source inventory missing from release: {name}')
    for name, expected in records.items():
        relative = Path(name)
        path = ROOT / relative
        if relative.is_absolute() or '..' in relative.parts or path.is_symlink():
            errors.append(f'Unsafe path: {name}')
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f'Missing or changed: {name}')
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Verified {len(records)} released files.')


if __name__ == '__main__':
    main()
