import gzip
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from engine.database import Database
from engine.state_generation import (finalize, json_bytes, read_plain,
                                     restore_generation)
from engine.state_io import StateError, describe
from engine.storage_metrics import tree_bytes
from test_state_research import snapshot


def test_finalize_preserves_source_bytes_and_exact_physical_accounting(tmp_path):
    source = snapshot(tmp_path)
    (source/'data').mkdir()
    (source/'data/snapshot.json').write_text('{"as_of":"2026-10-14"}')
    before = {k: (source/k/'state.json').read_bytes() for k in ('validation','experimental','research')}
    target = tmp_path/'staged'
    ledger, metrics = finalize(source,target,run_id=100,commit='a'*40,market_date='2026-10-14')
    assert tree_bytes(target) == metrics['pages_bytes']
    for kind, raw in before.items():
        assert (source/kind/'state.json').read_bytes() == raw
        assert not (target/kind/'state.json').exists()
        assert gzip.decompress((target/kind/'state.json.gz').read_bytes()) == raw
        assert read_plain(target,kind) == raw
    assert metrics['budget_status'] == 'YELLOW'
    assert metrics['projected_180_sessions_bytes'] is None
    second = tmp_path/'second'
    second_ledger, _ = finalize(target,second,run_id=101,commit='a'*40,market_date='2026-10-14',
                                previous=ledger,history=json.loads((target/'storage/history.json').read_bytes()))
    assert len(json.loads((second/'storage/history.json').read_bytes())) == 1
    with pytest.raises(StateError,match='rollback'):
        finalize(target,tmp_path/'old',run_id=99,commit='a'*40,market_date='2026-10-14',previous=ledger)


def test_independent_authority_rejects_cold_cache_rollback_and_missing_stream(tmp_path, monkeypatch):
    source = snapshot(tmp_path)
    (source/'data').mkdir()
    (source/'data/snapshot.json').write_text('{"as_of":"2026-10-14"}')
    target = tmp_path/'staged'
    ledger,_ = finalize(source,target,run_id=100,commit='a'*40,market_date='2026-10-14')
    def get(url,**kwargs):
        path = target/url.removeprefix('https://test/')
        response = Mock(status_code=200 if path.exists() else 404)
        response.content = path.read_bytes() if path.exists() else None
        response.raise_for_status.side_effect = None if path.exists() else __import__('requests').HTTPError('404')
        return response
    monkeypatch.setattr('engine.state_io.requests.get',get)
    db = Database(tmp_path/'target.db')
    restore_generation(db,'https://test',{'kind':'compressed','generation':ledger},tmp_path/'history.json')
    assert len(db.validation_rows()[0]) == 1
    newer = {**ledger,'run_id':101,'generation_id':'gh-101'}
    with pytest.raises(StateError,match='latest independent'):
        restore_generation(Database(tmp_path/'cold.db'),'https://test',{'kind':'compressed','generation':newer},tmp_path/'never.json')
    (target/'research/state.json.gz').unlink()
    cold = Database(tmp_path/'cold-missing.db')
    with pytest.raises(StateError):
        restore_generation(cold,'https://test',{'kind':'compressed','generation':ledger},tmp_path/'never.json')
    assert cold.validation_rows() == ([],[])


def test_cutover_requires_both_explicit_approval_and_matching_local_archive(tmp_path,monkeypatch):
    from scripts.state_release import select
    monkeypatch.setattr('scripts.state_release.gh',lambda *args:'[[]]')
    config = Path(__file__).parents[1]/'config/state-cutover.json'
    with pytest.raises(StateError,match='cutover is locked'):
        select(tmp_path/'authority.json',config)
    assert not (tmp_path/'authority.json').exists()


def test_full_export_can_use_captured_input_without_network(tmp_path,monkeypatch):
    from scripts.export_web import export
    from scripts.fixed_input_regression import fixed_history,fundamentals
    from engine.models import SetupState
    dbpath=tmp_path/'fixed.db'
    db=Database(dbpath)
    meta={'1000':{'code':'1000','name':'Fixture','market':'Prime','sector33':'Fixture','size_class':'Mid'}}
    db.save_securities(list(meta.values()))
    fixed={'names':{'1000':'Fixture'},'frames':{'1000':fixed_history(1000)},
           'fundamentals':{'1000':fundamentals('1000')},'sources':{'1000':'FIXED'},
           'meta':meta,'actions':{},'benchmark':fixed_history(777,benchmark=True),'benchmark_source':'FIXED'}
    monkeypatch.setattr('requests.sessions.Session.request',lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')))
    export(False,'主要500+Growth',db_path=dbpath,dashboard=tmp_path/'output',captured_input=fixed)
    assert json.loads((tmp_path/'output/data/snapshot.json').read_text())['universe_count']==1
    assert (tmp_path/'output/data/market-sessions.json').exists()


def test_nested_publication_target_is_rejected_without_copying(tmp_path):
    source=tmp_path/'source';source.mkdir()
    with pytest.raises(StateError,match='separate'):
        finalize(source,source/'nested',run_id=100,commit='a'*40,market_date='2026-10-15')
    assert list(source.iterdir())==[]


def test_floor_rejects_lost_ids_and_changed_holdout_without_exposing_target(tmp_path):
    from engine.state_generation import continuity_floor,check_continuity_floor
    source=snapshot(tmp_path)
    values={k:json.loads((source/k/'state.json').read_bytes()) for k in ('validation','experimental','research')}
    floor=continuity_floor(values)
    values['validation']['signals']=[]
    with pytest.raises(StateError,match='immutable IDs'):
        check_continuity_floor(values['validation'],'validation',floor)
    values['research']['research_events'][0]['evaluation_phase']='DISCOVERY-CHANGED'
    with pytest.raises(StateError,match='HOLDOUT'):
        check_continuity_floor(values['research'],'research',floor)


def test_completed_trend_accounting_converges_and_contains_exact_bytes(tmp_path):
    import pandas as pd
    source=snapshot(tmp_path)
    (source/'data').mkdir()
    dates=[str(d.date()) for d in pd.bdate_range(end='2026-10-14',periods=8)]
    history=[{'market_date':d,'generation_id':f'gh-{i+1}','format':'gzip-v1',
              'pages_bytes':10000+i*17,'logical_state_bytes':20000,'compressed_state_bytes':2000}
             for i,d in enumerate(dates[:-1])]
    target=tmp_path/'with-trends'
    _,physical=finalize(source,target,run_id=100,commit='a'*40,market_date='2026-10-14',history=history,sessions=dates)
    assert tree_bytes(target)==physical['pages_bytes']
    assert physical['trend_7_sessions']['complete'] is True
    assert (target/'research/storage_metrics.json').stat().st_size==16384
    assert json.loads((target/'research/storage_metrics.json').read_bytes())['pages']['pages_bytes']==tree_bytes(target)


def test_initial_approval_rejects_a_stale_source_before_downloading(tmp_path,monkeypatch):
    from scripts.state_release import select
    approval={'format':'jsl-cutover-approval-v1','approved':True,'approved_for_merge_and_deploy':True,
              'archive_sha256':'a'*64,'local_sha256_confirmed':'a'*64,'source_run_id':10}
    path=tmp_path/'approval.json';path.write_text(json.dumps(approval))
    calls=[]
    def gh(*args):
        calls.append(args)
        return '[[]]' if '--slurp' in args else '{"workflow_runs":[{"id":11}]}'
    monkeypatch.setattr('scripts.state_release.gh',gh)
    with pytest.raises(StateError,match='stale'):
        select(tmp_path/'authority.json',path)
    assert not any('download' in call for call in calls)
