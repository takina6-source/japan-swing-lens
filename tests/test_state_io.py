import gzip
import json
from unittest.mock import Mock

import pytest
import requests

from engine.database import Database
from engine.state_backup import create_backup, verify_backup
from engine.state_io import (StateError, compress, decode, describe, import_seed,
                             load_remote, parse)


def raw_state():
    return json.dumps({'signals': [{'signal_id': 's1', 'code': '1000', 'signal_date': '2026-10-14',
                                   'close': 100.12345678901234, 'schema_version': '3.1', 'strategy_version': 'v1'}],
                       'history': [{'signal_id': 's1', 'date': '2026-10-14', 'session_offset': 0, 'close': 100.0, 'return_abs': 0.0}],
                       'controls': [], 'control_history': []}, ensure_ascii=False, indent=2).encode()


def response(data=None, status=200):
    result = Mock(status_code=status, content=data)
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(str(status))
    return result


def remote(monkeypatch, mapping):
    def get(url, **kwargs):
        name = url.rsplit('/', 1)[-1]
        if name == 'index.json' and name not in mapping:
            return response(b'{"signal_count":1,"history_count":1,"control_count":0}')
        item = mapping.get(name, response(status=404))
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr('engine.state_io.requests.get', get)


def test_byte_exact_and_deterministic():
    raw = raw_state()
    packed = compress(raw)
    assert compress(raw) == packed
    assert packed[3] == 0  # no filename flag
    assert packed[4:8] == b'\0' * 4
    assert gzip.decompress(packed) == raw
    assert describe(raw, 'validation') == describe(gzip.decompress(packed), 'validation')
    assert decode(packed, 'validation', compressed=True, expected=describe(raw, 'validation')) == parse(raw)


def test_legacy_and_gzip_and_identical_mixed(monkeypatch):
    raw = raw_state()
    remote(monkeypatch, {'state.json': response(raw)})
    assert load_remote('https://test', 'validation') == parse(raw)
    remote(monkeypatch, {'state.json.gz': response(compress(raw)),
                         'state.manifest.json': response(json.dumps(describe(raw, 'validation')).encode())})
    assert load_remote('https://test', 'validation') == parse(raw)
    remote(monkeypatch, {'state.json.gz': response(compress(raw)), 'state.json': response(raw),
                         'state.manifest.json': response(json.dumps(describe(raw, 'validation')).encode())})
    with pytest.warns(RuntimeWarning, match='coexist'):
        assert load_remote('https://test', 'validation') == parse(raw)


@pytest.mark.parametrize('bad', [b'not gzip', compress(raw_state())[:-9], compress(b'{broken')])
def test_corrupt_gzip_never_falls_back(monkeypatch, bad):
    remote(monkeypatch, {'state.json.gz': response(bad), 'state.json': response(raw_state()),
                         'state.manifest.json': response(json.dumps(describe(raw_state(), 'validation')).encode())})
    with pytest.raises(StateError):
        load_remote('https://test', 'validation')


@pytest.mark.parametrize('failure', [requests.Timeout(), requests.ConnectionError(),
                                     requests.exceptions.ChunkedEncodingError(), response(status=500)])
def test_transport_failure_stops(monkeypatch, failure):
    remote(monkeypatch, {'state.json.gz': failure, 'state.json': response(raw_state())})
    with pytest.raises(StateError):
        load_remote('https://test', 'validation')


def test_404_and_missing_manifest_stop(monkeypatch):
    remote(monkeypatch, {})
    with pytest.raises(StateError):
        load_remote('https://test', 'validation')
    remote(monkeypatch, {'state.json.gz': response(compress(raw_state()))})
    with pytest.raises(StateError, match='requires'):
        load_remote('https://test', 'validation')


def test_mixed_generations_and_checksum_stop(monkeypatch):
    raw = raw_state()
    remote(monkeypatch, {'state.json.gz': response(compress(raw)), 'state.json': response(raw + b' '),
                         'state.manifest.json': response(json.dumps(describe(raw, 'validation')).encode())})
    with pytest.raises(StateError, match='mixed'):
        load_remote('https://test', 'validation')
    expected = describe(raw, 'validation'); expected['sha256'] = '0' * 64
    with pytest.raises(StateError, match='checksum'):
        decode(compress(raw), 'validation', compressed=True, expected=expected)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'orphan', 'schema', 'json'])
def test_partial_invalid_or_duplicate_states(change):
    value = parse(raw_state())
    if change == 'missing': del value['controls']
    if change == 'duplicate': value['signals'] *= 2
    if change == 'orphan': value['history'][0]['signal_id'] = 'missing'
    if change == 'schema': value['signals'][0]['signal_id'] = None
    raw = b'{broken' if change == 'json' else json.dumps(value).encode()
    with pytest.raises(StateError): decode(raw, 'validation')


def test_json_duplicate_keys_and_nonfinite_stop():
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}'):
        with pytest.raises(StateError): parse(raw)


def test_two_continuations_and_rollback_floor(tmp_path):
    db = Database(tmp_path / 'db.sqlite')
    value = parse(raw_state())
    import_seed(db, value, 'validation')
    for day in ('2026-10-15', '2026-10-16'):
        value['history'].append({'signal_id': 's1', 'date': day, 'session_offset': len(value['history']), 'close': 101.0, 'return_abs': 1.0})
        import_seed(db, decode(compress(json.dumps(value).encode()), 'validation', compressed=True), 'validation')
    assert len(db.validation_rows()[1]) == 3
    assert len(db.validation_rows()[0]) == 1
    with pytest.raises(StateError, match='rollback'):
        import_seed(db, parse(raw_state()), 'validation')
    assert len(db.validation_rows()[1]) == 3


def test_atomic_import_rolls_back_later_table_error(tmp_path):
    db = Database(tmp_path / 'db.sqlite')
    value = parse(raw_state()); value['history'][0]['close'] = {'not': 'sqlite value'}
    with pytest.raises(Exception): import_seed(db, value, 'validation')
    assert db.validation_rows() == ([], [])


def test_no_seed_is_explicit_initial_build(tmp_path):
    from engine.validation import seed_validation
    from engine.experimental.validation import seed_experimental
    from engine.research.pipeline import seed_research
    db = Database(tmp_path / 'db.sqlite')
    assert all(seed(db, None) is False for seed in (seed_validation, seed_experimental, seed_research))


def test_index_mismatch_and_generation_change(monkeypatch):
    remote(monkeypatch, {'state.json': response(raw_state()),
                         'index.json': response(b'{"signal_count":99,"history_count":1,"control_count":0}')})
    with pytest.raises(StateError, match='index/count'):
        load_remote('https://test', 'validation')
    counter = 0
    def get(url, **kwargs):
        nonlocal counter
        if url.endswith('index.json'):
            counter += 1
            return response(json.dumps({'signal_count':1, 'history_count':1, 'control_count':0, 'generation':counter}).encode())
        return response(raw_state()) if url.endswith('state.json') else response(status=404)
    monkeypatch.setattr('engine.state_io.requests.get', get)
    with pytest.raises(StateError, match='generation changed'):
        load_remote('https://test', 'validation')


def test_offline_workflows_have_no_production_deployment():
    from pathlib import Path
    import yaml
    for name in ('state-storage-phase1.yml', 'state-storage-backup-phase1.yml'):
        raw = (Path(__file__).parents[1] / '.github/workflows' / name).read_text()
        workflow = yaml.safe_load(raw)
        assert 'deploy-pages' not in raw
        assert 'upload-pages-artifact' not in raw
        assert 'pages: write' not in raw and 'id-token: write' not in raw
        assert 'environment' not in workflow['jobs'][next(iter(workflow['jobs']))]
        trigger = workflow.get('on', workflow.get(True))
        assert 'workflow_dispatch' not in trigger
    backup = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/state-storage-backup-phase1.yml').read_text())
    trigger = backup.get('on', backup.get(True))
    assert trigger['push']['branches'] == ['codex/state-storage-phase1']
    assert trigger['push']['paths'] == ['docs/state-storage/BACKUP_REQUEST.json']
