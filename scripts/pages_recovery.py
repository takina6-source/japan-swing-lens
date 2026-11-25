"""Recover an unpublished independent generation from its original Pages artifact.

Preparation only: verifies every referenced file before a workflow may publish.
Never rebuilds market data, overwrites a Release, or falls back to an older run.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path, PurePosixPath
import subprocess
import shutil
import tarfile
import threading
import time
import zipfile

from engine.state_generation import validate_generation
from engine.state_generation import restore_generation
from engine.database import Database
from engine.state_io import StateError, _get, parse
from scripts.state_release import REPO, gh, select
from scripts.verify_state_publication import verify


def artifact_metadata(run_id, name):
    data = json.loads(gh('api', f'repos/{REPO}/actions/runs/{run_id}/artifacts?per_page=100'))
    return [item for item in data['artifacts'] if item['name'] == name and not item['expired']]


def wait_for_artifact(run_id, name, *, attempts=12):
    """Wait for uploaded artifact metadata to become visible to deploy-pages."""
    for attempt in range(attempts):
        items = artifact_metadata(run_id, name)
        if len(items) == 1:
            return items[0]
        if len(items) > 1:
            raise StateError('multiple active Pages artifacts; refusing ambiguous deployment')
        if attempt + 1 < attempts:
            time.sleep(5)
    raise StateError('uploaded Pages artifact is still unavailable; no deployment attempted')


def digest_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def extract_artifact(archive, metadata, target):
    expected = metadata.get('digest', '')
    if not expected.startswith('sha256:') or digest_file(archive) != expected[7:]:
        raise StateError('Pages artifact ZIP checksum mismatch or missing digest')
    if target.exists():
        raise StateError('recovery target must be new')
    with zipfile.ZipFile(archive) as bundle:
        if bundle.namelist() != ['artifact.tar']:
            raise StateError('unexpected Pages artifact ZIP contents')
        tarpath = archive.with_suffix('.tar')
        with bundle.open('artifact.tar') as source, tarpath.open('wb') as dest:
            shutil.copyfileobj(source, dest)
    with tarfile.open(tarpath) as bundle:
        names = set()
        size = 0
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or str(path) in names
                    or not (member.isfile() or member.isdir())):
                raise StateError('unsafe or duplicate Pages artifact entry')
            names.add(str(path))
            size += member.size
        if size > 1_000_000_000:
            raise StateError('recovery artifact exceeds the Pages budget')
        bundle.extractall(target, filter='data')


@contextmanager
def serve_tree(target):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(target)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def verify_tree(target, ledger):
    if parse((target / 'state-generation.json').read_bytes()) != ledger:
        raise StateError('artifact generation differs from independent Release')
    with serve_tree(target) as root:
        # The same restore, transport, bulk, date and display checks used after
        # production deployment, against the staged tree before publication.
        verify(root, ledger)


def check_cache(root, db_path, authority, history):
    if not db_path.is_file():
        raise StateError('requested cache database does not exist')
    db = Database(db_path)
    restore_generation(db, root, authority, history)
    from engine.research.storage import ResearchStore
    actual = len(ResearchStore(db).rows('research_control_history'))
    expected = authority['generation']['streams']['research']['tables']['research_control_history']['count']
    if actual != expected:
        raise StateError('restored cache contains unexpected research history rows')
    print('Verified actual cache recovery:', authority['generation']['generation_id'], actual)


def prepare(root_url, work):
    work.mkdir(parents=True, exist_ok=False)
    authority_path = work / 'authority.json'
    select(authority_path, Path('config/state-cutover.json'))
    authority = parse(authority_path.read_bytes())
    if authority['kind'] != 'compressed':
        return False
    expected = validate_generation(authority['generation'])
    current = validate_generation(parse(_get(root_url.rstrip('/') + '/state-generation.json')))
    if current == expected:
        print('Published generation matches independent authority; no recovery needed')
        return False
    if current['run_id'] >= expected['run_id'] or current['market_date'] > expected['market_date']:
        raise StateError('public generation is newer or conflicts; explicit reconciliation required')
    metadata = wait_for_artifact(expected['run_id'], 'github-pages')
    producer = metadata['workflow_run']
    if producer['id'] != expected['run_id'] or producer['head_sha'] != expected['producer_commit']:
        raise StateError('artifact producer differs from independent generation')
    archive = work / 'pages.zip'
    with archive.open('wb') as output:
        subprocess.run(['gh', 'api', f"repos/{REPO}/actions/artifacts/{metadata['id']}/zip"],
                       stdout=output, check=True)
    target = work / 'pages'
    extract_artifact(archive, metadata, target)
    verify_tree(target, expected)
    print('Verified pending publication:', expected['generation_id'], expected['market_date'])
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-run', type=int)
    parser.add_argument('--artifact-name', default='github-pages')
    parser.add_argument('--root-url')
    parser.add_argument('--work', type=Path)
    parser.add_argument('--github-output', type=Path)
    parser.add_argument('--cache-db', type=Path)
    args = parser.parse_args()
    if args.wait_run:
        item = wait_for_artifact(args.wait_run, args.artifact_name)
        print('Pages artifact is available:', item['id'])
        return
    if not args.root_url or not args.work or not args.github_output:
        parser.error('preparation requires --root-url, --work and --github-output')
    pending = prepare(args.root_url, args.work)
    if args.cache_db:
        authority = parse((args.work / 'authority.json').read_bytes())
        if pending:
            with serve_tree(args.work / 'pages') as root:
                check_cache(root, args.cache_db, authority, args.work / 'cache-history.json')
        else:
            check_cache(args.root_url, args.cache_db, authority, args.work / 'cache-history.json')
    with args.github_output.open('a') as output:
        print(f'pending={str(pending).lower()}', file=output)


if __name__ == '__main__':
    main()
