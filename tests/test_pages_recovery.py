from contextlib import contextmanager
import copy
import io
import json
from pathlib import Path
import tarfile
from unittest.mock import Mock
import zipfile

import pytest
import yaml

from engine.database import Database
from engine.state_generation import finalize, restore_generation
from engine.state_io import StateError, sha256
from scripts import pages_recovery as recovery
from test_state_research import snapshot


@pytest.fixture
def staged(tmp_path):
    source = snapshot(tmp_path)
    (source / 'data').mkdir()
    (source / 'data/snapshot.json').write_text('{"as_of":"2026-10-14"}')
    # Required display adapters are real files even in the small fixture.
    for name in ('index.html', 'app.js', 'research.js'):
        (source / name).write_text('fixture')
    target = tmp_path / 'staged'
    ledger, _ = finalize(source, target, run_id=100, commit='a' * 40, market_date='2026-10-14')
    return target, ledger


def fake_transport(monkeypatch, target, *, forbid_legacy=False):
    calls = []
    def get(url, **kwargs):
        path = target / url.removeprefix('https://test/')
        calls.append(url)
        if forbid_legacy and url.endswith('/state.json'):
            raise AssertionError('retired legacy endpoint must not be requested')
        result = Mock(status_code=200 if path.exists() else 404)
        result.content = path.read_bytes() if path.exists() else b''
        return result
    monkeypatch.setattr('engine.state_io.requests.get', get)
    return calls


def test_verified_restore_does_not_depend_on_retired_legacy_endpoints(staged, monkeypatch, tmp_path):
    target, ledger = staged
    calls = fake_transport(monkeypatch, target, forbid_legacy=True)
    restore_generation(Database(tmp_path / 'restored.db'), 'https://test',
                       {'kind': 'compressed', 'generation': ledger}, tmp_path / 'history.json')
    assert any(url.endswith('/research/state.json.gz') for url in calls)
    # The existing compressed checksum guard remains mandatory.
    (target / 'research/state.json.gz').write_bytes(b'corrupt')
    with pytest.raises(StateError, match='checksum'):
        restore_generation(Database(tmp_path / 'corrupt.db'), 'https://test',
                           {'kind': 'compressed', 'generation': ledger}, tmp_path / 'never.json')


def artifact_zip(tmp_path, entries=None, tree=None):
    tarbytes = io.BytesIO()
    with tarfile.open(fileobj=tarbytes, mode='w') as bundle:
        if tree:
            bundle.add(tree, arcname='.')
        else:
            for name, kind in entries or [('index.html', 'file')]:
                item = tarfile.TarInfo(name)
                if kind == 'symlink':
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/tmp/outside'
                    bundle.addfile(item)
                else:
                    item.size = 7
                    bundle.addfile(item, io.BytesIO(b'fixture'))
    archive = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('artifact.tar', tarbytes.getvalue())
    return archive, {'id': 99, 'name': 'github-pages', 'expired': False,
                     'digest': 'sha256:' + sha256(archive.read_bytes()),
                     'workflow_run': {'id': 100, 'head_sha': 'a' * 40}}


@pytest.mark.parametrize('entries', [[('../escape', 'file')], [('/absolute', 'file')],
                                    [('link', 'symlink')], [('a', 'file'), ('a', 'file')]])
def test_recovery_rejects_unsafe_archive_before_extracting(tmp_path, entries):
    archive, meta = artifact_zip(tmp_path, entries)
    target = tmp_path / 'out'
    with pytest.raises(StateError, match='unsafe'):
        recovery.extract_artifact(archive, meta, target)
    assert not target.exists()


def test_recovery_rejects_tampered_archive_and_existing_target(tmp_path):
    archive, meta = artifact_zip(tmp_path)
    with pytest.raises(StateError, match='checksum'):
        recovery.extract_artifact(archive, {**meta, 'digest': 'sha256:' + '0' * 64}, tmp_path / 'bad')
    target = tmp_path / 'exists'
    target.mkdir()
    with pytest.raises(StateError, match='must be new'):
        recovery.extract_artifact(archive, meta, target)
    assert list(target.iterdir()) == []


def test_artifact_visibility_is_bounded_and_ambiguity_fails(monkeypatch):
    results = iter([[], [], [{'id': 99}]])
    sleeps = []
    monkeypatch.setattr(recovery, 'artifact_metadata', lambda *args: next(results))
    monkeypatch.setattr(recovery.time, 'sleep', sleeps.append)
    assert recovery.wait_for_artifact(100, 'github-pages') == {'id': 99}
    assert sleeps == [5, 5]
    monkeypatch.setattr(recovery, 'artifact_metadata', lambda *args: [])
    with pytest.raises(StateError, match='unavailable'):
        recovery.wait_for_artifact(100, 'github-pages', attempts=2)
    monkeypatch.setattr(recovery, 'artifact_metadata', lambda *args: [{}, {}])
    with pytest.raises(StateError, match='ambiguous'):
        recovery.wait_for_artifact(100, 'github-pages')


@pytest.mark.parametrize('state', ['pending', 'current', 'newer', 'same-run-conflict', 'wrong-producer'])
def test_prepare_never_rebuilds_or_rolls_back_a_generation(staged, monkeypatch, tmp_path, state):
    target, ledger = staged
    authority = {'kind': 'compressed', 'generation': ledger}
    current = copy.deepcopy(ledger)
    if state in {'pending', 'wrong-producer'}:
        current.update(run_id=99, generation_id='gh-99')
    elif state == 'newer':
        current.update(run_id=101, generation_id='gh-101')
    elif state == 'same-run-conflict':
        current['storage_history_sha256'] = '0' * 64
    monkeypatch.setattr(recovery, '_get', lambda url: json.dumps(current).encode())
    monkeypatch.setattr(recovery, 'select', lambda path, _: path.write_text(json.dumps(authority)))
    archive, metadata = artifact_zip(tmp_path, tree=target)
    if state == 'wrong-producer':
        metadata['workflow_run']['head_sha'] = 'b' * 40
    monkeypatch.setattr(recovery, 'wait_for_artifact', lambda *args: metadata)
    downloads, checks = [], []
    def download(command, stdout, check):
        downloads.append(command)
        stdout.write(archive.read_bytes())
    monkeypatch.setattr(recovery.subprocess, 'run', download)
    monkeypatch.setattr(recovery, 'verify_tree', lambda path, actual: checks.append((path, actual)))
    work = tmp_path / 'recovery'
    if state in {'newer', 'same-run-conflict', 'wrong-producer'}:
        with pytest.raises(StateError):
            recovery.prepare('https://test', work)
        assert not downloads and not checks
    elif state == 'current':
        assert not recovery.prepare('https://test', work)
        assert not downloads and not checks
    else:
        assert recovery.prepare('https://test', work)
        assert json.loads((work / 'pages/state-generation.json').read_bytes()) == ledger
        assert len(downloads) == 1 and checks == [(work / 'pages', ledger)]


def test_tree_verification_uses_exact_release_ledger(staged, monkeypatch):
    target, ledger = staged
    @contextmanager
    def serve(path):
        assert path == target
        yield 'https://test'
    monkeypatch.setattr(recovery, 'serve_tree', serve)
    monkeypatch.setattr(recovery, 'verify', Mock())
    recovery.verify_tree(target, ledger)
    recovery.verify.assert_called_once_with('https://test', ledger)
    with pytest.raises(StateError, match='differs'):
        recovery.verify_tree(target, {**ledger, 'run_id': 101})


def test_live_recovery_verification_has_no_publication_permissions():
    workflow = yaml.safe_load(Path('.github/workflows/verify-research-recovery.yml').read_text())
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'read'}
    raw = Path('.github/workflows/verify-research-recovery.yml').read_text()
    assert 'deploy-pages' not in raw and 'upload-pages-artifact' not in raw
    assert '--cache-db' in raw
    production = yaml.safe_load(Path('.github/workflows/update-dashboard.yml').read_text())
    assert 'recover-pending-publication' in production['jobs']['analyze-and-publish']['needs']
