import json

import pytest

from engine.config import load_config
from engine.database import Database
from engine.research.pipeline import export_research, run_research
from engine.research.storage import ResearchStore
from engine.state_backup import create_backup, verify_backup
from engine.state_io import StateError, compress, decode, describe, import_seed
from engine.validation import export_validation
from engine.experimental.validation import export_experimental
from scripts.state_replay import normalize
from test_research_pipeline import _frame, _insert_watch_snapshot


def snapshot(tmp_path):
    db = Database(tmp_path / 'source.sqlite')
    _insert_watch_snapshot(db)
    cfg = load_config()
    run_research(db, [], {'1000': _frame()}, _frame(price=2000), {}, cfg)
    pages = tmp_path / 'pages'
    export_validation(db, pages / 'validation', cfg)
    export_experimental(db, pages / 'experimental', cfg)
    export_research(db, pages / 'research', cfg)
    return pages


def test_three_stream_backup_restore_and_tamper(tmp_path):
    pages = snapshot(tmp_path)
    archive = tmp_path / 'backup.zip'
    manifest = create_backup(pages, archive, generation='fixed-fixture', market_date='2026-10-14')
    target = tmp_path / 'restored'
    assert verify_backup(archive, target) == manifest
    for kind in ('validation', 'experimental', 'research'):
        assert (target / kind / 'state.json').read_bytes() == (pages / kind / 'state.json').read_bytes()
    with pytest.raises(StateError, match='must not exist'): verify_backup(archive, target)
    with pytest.raises(StateError, match='overwrite'):
        create_backup(pages, archive, generation='other', market_date='2026-10-14')
    import zipfile
    bad = tmp_path / 'tampered.zip'
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(bad, 'w') as dest:
        for name in source.namelist():
            raw = source.read(name)
            dest.writestr(name, raw[:-1] if name == 'research/state.json.gz' else raw)
    with pytest.raises(StateError): verify_backup(bad, tmp_path / 'must-not-appear')
    assert not (tmp_path / 'must-not-appear').exists()


def test_research_results_after_two_actual_continuations(tmp_path):
    pages = snapshot(tmp_path)
    targets = [Database(tmp_path / f'{mode}.sqlite') for mode in ('legacy', 'gzip')]
    for kind in ('validation', 'experimental', 'research'):
        raw = (pages / kind / 'state.json').read_bytes()
        import_seed(targets[0], decode(raw, kind), kind)
        import_seed(targets[1], decode(compress(raw), kind, compressed=True, expected=describe(raw, kind)), kind)
    for periods in (26, 27):
        for db in targets:
            run_research(db, [], {'1000': _frame(periods=periods)},
                         _frame(periods=periods, price=2000), {}, load_config())
    outputs = []
    for number, db in enumerate(targets):
        out = tmp_path / str(number)
        export_research(db, out, load_config())
        outputs.append({name: normalize(json.loads((out / name).read_bytes())) for name in (
            'hypotheses.json', 'families.json', 'events.json', 'performance.json', 'checkpoints.json')})
    assert outputs[0] == outputs[1]
    assert len(outputs[0]['hypotheses.json']) == 4
    events = ResearchStore(targets[1]).rows('research_events')
    assert any(r['data_origin'] == 'LIVE_FORWARD' and r['evaluation_phase'] == 'HOLDOUT' for r in events)
