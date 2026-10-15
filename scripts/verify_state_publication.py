"""Verify the approved generation after deployment; no rollback or publishing."""
import argparse
import json
import tempfile
import time
from pathlib import Path

from engine.database import Database
from engine.state_generation import restore_generation
from engine.state_io import StateError, _get, parse


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
