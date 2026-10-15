"""Portable three-stream backup; no writer in the production export path."""
from __future__ import annotations

import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .state_io import StateError, compress, decode, describe, sha256

KINDS = ('validation', 'experimental', 'research')


def create_backup(pages: Path, archive: Path, *, generation: str, market_date: str):
    if archive.exists():
        raise StateError('refusing to overwrite a backup')
    manifest = {'backup_format': 'jsl-state-backup-v1', 'generation': generation,
                'market_date': market_date, 'captured_at': datetime.now(timezone.utc).isoformat(),
                'streams': {}}
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=archive.parent) as directory:
        staged = Path(directory) / 'backup.zip'
        with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_STORED) as bundle:
            for kind in KINDS:
                raw = (pages / kind / 'state.json').read_bytes()
                info = describe(raw, kind)
                packed = compress(raw)
                decode(packed, kind, compressed=True, expected=info)
                name = f'{kind}/state.json.gz'
                manifest['streams'][kind] = {'file': name, 'compressed_bytes': len(packed),
                                             'compressed_sha256': sha256(packed), 'state': info}
                bundle.writestr(name, packed)
            bundle.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        verify_backup(staged)
        # Atomic creation, never replace an existing independent backup.
        os.link(staged, archive)
    digest = sha256(archive.read_bytes())
    archive.with_suffix(archive.suffix + '.sha256').write_text(f'{digest}  {archive.name}\n')
    return manifest


def verify_backup(archive: Path, target: Path | None = None):
    if target is not None and target.exists():
        raise StateError('restore target must not exist (never overwrite live State)')
    try:
        with zipfile.ZipFile(archive) as bundle:
            expected_names = {'manifest.json', *(f'{k}/state.json.gz' for k in KINDS)}
            if set(bundle.namelist()) != expected_names or len(bundle.namelist()) != len(expected_names):
                raise StateError('unexpected/missing/duplicate backup members')
            manifest = json.loads(bundle.read('manifest.json'))
            if manifest.get('backup_format') != 'jsl-state-backup-v1' or set(manifest['streams']) != set(KINDS):
                raise StateError('unsupported/incomplete backup')
            # Validate every stream before making a restored directory visible.
            parent = target.parent if target else archive.parent
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=parent) as directory:
                staged = Path(directory) / 'restore'
                for kind in KINDS:
                    info = manifest['streams'][kind]
                    name = f'{kind}/state.json.gz'
                    if info['file'] != name:
                        raise StateError('invalid backup member path')
                    packed = bundle.read(name)
                    if len(packed) != info['compressed_bytes'] or sha256(packed) != info['compressed_sha256']:
                        raise StateError('backup compressed checksum mismatch')
                    decode(packed, kind, compressed=True, expected=info['state'])
                    if target:
                        import gzip
                        output = staged / kind / 'state.json'
                        output.parent.mkdir(parents=True)
                        output.write_bytes(gzip.decompress(packed))
                if target:
                    os.rename(staged, target)
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise StateError('invalid backup') from exc
    return manifest
