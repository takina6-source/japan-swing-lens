"""Verify the approved generation after deployment; no rollback or publishing."""
import argparse
import gzip
import json
import tempfile
import time
from pathlib import Path

from engine.database import Database
from engine.state_generation import restore_generation
from engine.state_io import StateError, _get, parse, sha256


def verify(root_url, ledger):
    with tempfile.TemporaryDirectory() as directory:
        temp=Path(directory)
        restore_generation(Database(temp/'verify.db'),root_url,
                           {'kind':'compressed','generation':ledger},temp/'history.json')
        snapshot=parse(_get(root_url+'/data/snapshot.json'))
        if snapshot['as_of'] != ledger['market_date']:
            raise StateError('Core market day differs from the deployed generation')
        for name in ('index.html','app.js','research.js','research/index.json','research/storage_metrics.json'):
            _get(root_url+'/'+name)
        # Optional display adapters may be intentionally unavailable; required files
        # are checked against the staged availability in the generation ledger.
        for name in ledger.get('display_files',[]):
            _get(root_url+'/'+name)
        if ledger.get('bulk_downloads_sha256'):
            raw_manifest = _get(root_url+'/bulk-downloads.json')
            if sha256(raw_manifest) != ledger['bulk_downloads_sha256']:
                raise StateError('bulk manifest differs from published generation')
            from engine.bulk_downloads import NAMES
            allowed = {f'{k}/{s}.{e}' for k,stems in NAMES.items() for s in stems for e in ('json','csv')}
            for name,info in parse(raw_manifest)['files'].items():
                if name not in allowed or info['path'] != name+'.gz':
                    raise StateError('unexpected bulk download path')
                packed = _get(root_url+'/'+info['path'])
                try:
                    raw = gzip.decompress(packed)
                except (OSError,EOFError) as exc:
                    raise StateError('bulk gzip is corrupt') from exc
                if sha256(packed) != info['compressed_sha256'] or sha256(raw) != info['sha256'] or len(raw) != info['bytes']:
                    raise StateError('bulk download original bytes differ')
    print('Published generation, all three original State hashes, market day, history and display files verified')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root-url',required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    args=parser.parse_args()
    ledger=json.loads(args.ledger.read_bytes())
    for attempt in range(8):
        try:
            verify(args.root_url.rstrip('/'),ledger)
            return
        except StateError:
            if attempt==7: raise
            time.sleep(15)


if __name__=='__main__': main()
