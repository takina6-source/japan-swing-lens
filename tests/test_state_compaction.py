import copy
import json
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from engine.database import Database
from engine.research.storage import ResearchStore
from engine.research.pipeline import RESEARCH_LOGIC_VERSION
from engine.state_generation import finalize, restore_generation
from engine.state_io import RESEARCH_KEYS, StateError, decode, import_seed
from test_state_research import snapshot


def research_value(store):
    return {key: store.rows({'hypotheses': 'research_hypotheses',
                             'families': 'research_families'}.get(key, key))
            for key in RESEARCH_KEYS}


@pytest.fixture
def compacted(tmp_path):
    pages = snapshot(tmp_path)
    db = Database(tmp_path / 'cache.db')
    import_seed(db, json.loads((pages / 'research/state.json').read_bytes()), 'research')
    store = ResearchStore(db)
    subject_id = store.rows('validation_subjects')[0]['validation_subject_id']
    store.save_control_members([{
        'control_group_id': 'g1', 'validation_subject_id': subject_id, 'control_code': '2000',
        'anchor_date': '2026-10-01', 'anchor_price': 100, 'feature_hash': 'fixed',
    }])
    rows = [{'control_group_id': 'g1', 'validation_subject_id': subject_id,
             'control_code': '2000', 'date': str(date(2026, 10, 1) + timedelta(days=i)),
             'session_offset': i, 'close': 100 + i, 'return_abs': float(i)}
            for i in range(21)]
    store.save_control_history(rows[:20])
    before = research_value(store)
    updated = ResearchStore(Database(tmp_path / 'updated.db'))
    updated.import_state(before)
    updated.save_control_history(rows[20:])
    assert updated.compact_matured_history() == 17
    value = decode(json.dumps({'research_logic_version': RESEARCH_LOGIC_VERSION,
                               **research_value(updated)}).encode(), 'research')
    return pages, db, before, value


def test_verified_restore_accepts_compaction_without_resurrecting_rows(compacted, tmp_path, monkeypatch):
    pages, db, before, value = compacted
    (pages / 'research/state.json').write_text(json.dumps(value))
    target = tmp_path / 'staged'
    ledger, _ = finalize(pages, target, run_id=100, commit='a' * 40, market_date='2026-10-21')

    def get(url, **kwargs):
        path = target / url.removeprefix('https://test/')
        result = Mock(status_code=200 if path.exists() else 404)
        result.content = path.read_bytes() if path.exists() else b''
        return result

    monkeypatch.setattr('engine.state_io.requests.get', get)
    for _ in range(2):
        restore_generation(db, 'https://test', {'kind': 'compressed', 'generation': ledger},
                           tmp_path / 'history.json')
        actual = research_value(ResearchStore(db))
        assert [r['session_offset'] for r in actual['research_control_history']
                if r['control_group_id'] == 'g1'] == [1, 5, 10, 20]
        for table in RESEARCH_KEYS.keys() - {'research_control_history'}:
            # INSERT OR REPLACE refreshes the operational SQLite timestamp.
            normalize = lambda rows: [{k: v for k, v in row.items() if k != 'created_at'}
                                      for row in rows]
            assert normalize(actual[table]) == normalize(before[table])


def test_legacy_restore_still_rejects_missing_cached_history(compacted):
    _, db, before, value = compacted
    with pytest.raises(StateError, match='research_control_history'):
        import_seed(db, value, 'research')
    assert research_value(ResearchStore(db)) == before


@pytest.mark.parametrize('damage', ['horizon', 'immature', 'membership', 'subject',
                                    'unrelated_group', 'newer_cache', 'subject_history'])
def test_compaction_exception_cannot_hide_data_loss(compacted, damage):
    _, db, before, value = compacted
    if damage == 'horizon':
        value['research_control_history'] = [r for r in value['research_control_history']
                                             if r['session_offset'] != 5]
    elif damage == 'immature':
        for row in value['research_control_history']:
            row['matured'] = 0
    elif damage == 'membership':
        value['research_control_members'] = []
    elif damage == 'subject':
        value['validation_subjects'] = []
    elif damage in {'unrelated_group', 'newer_cache'}:
        extra = copy.deepcopy(before['research_control_history'][0])
        if damage == 'unrelated_group':
            extra['control_group_id'] = 'different-group'
        else:
            extra.update(date='2026-12-01', session_offset=21)
        ResearchStore(db).save_control_history([extra])
        before = research_value(ResearchStore(db))
    else:
        value['research_subject_history'] = []
        assert before['research_subject_history']
    with pytest.raises(StateError, match='missing cached IDs or rollback'):
        import_seed(db, value, 'research', allow_research_compaction=True)
    assert research_value(ResearchStore(db)) == before


def test_compaction_deletions_roll_back_on_later_import_failure(compacted, monkeypatch):
    _, db, before, value = compacted
    original = ResearchStore.import_state

    def fail(self, payload):
        original(self, payload)
        raise RuntimeError('failed after import')

    monkeypatch.setattr(ResearchStore, 'import_state', fail)
    with pytest.raises(RuntimeError, match='failed after import'):
        import_seed(db, value, 'research', allow_research_compaction=True)
    assert research_value(ResearchStore(db)) == before
