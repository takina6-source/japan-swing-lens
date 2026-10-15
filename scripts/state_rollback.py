"""Prepare an unpublished legacy-format recovery tree from the SAME generation."""
import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from engine.state_backup import KINDS,verify_backup
from engine.state_generation import json_bytes,read_plain
from engine.state_io import StateError,sha256
from engine.storage_metrics import LIMIT_BYTES,tree_bytes


def prepare(source: Path,archive: Path,target: Path,expected_sha: str):
    if sha256(archive.read_bytes()) != expected_sha:
        raise StateError('rollback archive checksum mismatch')
    if target.exists() or source.resolve()==target.resolve() or source.resolve() in target.resolve().parents:
        raise StateError('rollback target must be new and separate')
    if any(p.is_symlink() for p in source.rglob('*')):
        raise StateError('symlink in rollback source')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as directory:
        temp=Path(directory)
        manifest=verify_backup(archive,temp/'restored')
        # An older backup must never discard any data produced after cutover.
        for kind in KINDS:
            if sha256(read_plain(source,kind)) != manifest['streams'][kind]['state']['sha256']:
                raise StateError('backup differs from the current generation; reconcile newer data first')
        staged=temp/'legacy'
        shutil.copytree(source,staged)
        from engine.bulk_downloads import restore_downloads
        restore_downloads(staged)
        for kind in KINDS:
            shutil.copy2(temp/'restored'/kind/'state.json',staged/kind/'state.json')
            for name in ('state.json.gz','state.manifest.json'):
                (staged/kind/name).unlink(missing_ok=True)
        # These are operational transport/measurement files, not research history.
        (staged/'state-generation.json').unlink(missing_ok=True)
        if (staged/'storage').exists(): shutil.rmtree(staged/'storage')
        research=json.loads((staged/'research/state.json').read_bytes())
        telemetry=sorted(research['research_telemetry'],key=lambda row:(row['run_date'],row['run_id']))
        legacy_metrics=telemetry[-1]['payload']['storage'] if telemetry else {}
        (staged/'research/storage_metrics.json').write_bytes(json_bytes(legacy_metrics))
        size=tree_bytes(staged)
        if size>=LIMIT_BYTES:
            raise StateError('legacy recovery would exceed Pages capacity; keep compressed data and request recovery decision')
        os.rename(staged,target)
    return {'prepared':True,'deployed':False,'bytes':size,'original_state_sha256':
            {k:v['state']['sha256'] for k,v in manifest['streams'].items()}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('source','archive','target'): parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--archive-sha256',required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.source,args.archive,args.target,args.archive_sha256),indent=2))


if __name__=='__main__': main()
