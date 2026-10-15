"""GitHub Release authority for continuity; never deploys Pages."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from engine.state_backup import verify_backup
from engine.state_generation import json_bytes, validate_generation
from engine.state_io import StateError, sha256

REPO = 'takina6-source/japan-swing-lens'
PREFIX = 'jsl-state-generation-'


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def select(output: Path, bootstrap: Path):
    releases = json.loads(gh('api', '--paginate', '--slurp', f'repos/{REPO}/releases?per_page=100'))
    candidates = [r for page in releases for r in page if not r['draft']
                  and r['tag_name'].startswith(PREFIX)]
    if candidates:
        # Ignore GitHub's mutable "latest release" label and publication timestamp.
        for release in candidates:
            if not release['tag_name'][len(PREFIX):].isdigit():
                raise StateError('invalid continuity release identity')
        selected = max(candidates, key=lambda r: int(r['tag_name'][len(PREFIX):]))
        with tempfile.TemporaryDirectory() as directory:
            gh('release', 'download', selected['tag_name'], '--repo', REPO,
               '--pattern', 'generation.json', '--dir', directory)
            generation = validate_generation(json.loads((Path(directory)/'generation.json').read_bytes()))
        if str(generation['run_id']) != selected['tag_name'][len(PREFIX):]:
            raise StateError('Release and generation producer mismatch')
        authority = {'kind': 'compressed', 'release': selected['tag_name'], 'generation': generation}
    else:
        approval = json.loads(bootstrap.read_bytes())
        digest = approval.get('archive_sha256')
        if (approval.get('format') != 'jsl-cutover-approval-v1'
                or approval.get('approved') is not True
                or approval.get('approved_for_merge_and_deploy') is not True
                or not isinstance(digest, str) or len(digest) != 64
                or approval.get('local_sha256_confirmed') != digest):
            raise StateError('cutover is locked: latest local backup verification and explicit merge/deploy approval required')
        latest = json.loads(gh('api', f'repos/{REPO}/actions/workflows/update-dashboard.yml/runs?status=success&per_page=1'))
        if latest['workflow_runs'][0]['id'] != approval['source_run_id']:
            raise StateError('backup is stale: a newer production run exists; refresh backup and local confirmation')
        tag = approval['backup_release']
        if not isinstance(tag, str) or not tag.startswith('jsl-state-backup-run-'):
            raise StateError('bootstrap must use the dedicated three-stream backup Release')
        archive_name = f"jsl-state-backup-run-{approval['source_run_id']}.zip"
        with tempfile.TemporaryDirectory() as directory:
            gh('release', 'download', tag, '--repo', REPO, '--pattern', archive_name, '--dir', directory)
            archive = Path(directory)/archive_name
            if sha256(archive.read_bytes()) != digest:
                raise StateError('approved backup archive SHA mismatch')
            manifest = verify_backup(archive)
        if manifest['generation'] != f"github-actions-run-{approval['source_run_id']}":
            raise StateError('approved backup generation mismatch')
        authority = {'kind': 'bootstrap', 'source_run_id': approval['source_run_id'],
                     'market_date': manifest['market_date'], 'archive_sha256': digest,
                     'streams': {k: v['state'] for k, v in manifest['streams'].items()}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json_bytes(authority))
    print('Independent continuity authority selected and verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bootstrap', type=Path, default=Path('config/state-cutover.json'))
    args = parser.parse_args()
    select(args.output, args.bootstrap)


if __name__ == '__main__':
    main()
