import hashlib

import pytest

from engine.database import Database
from scripts.state_dry_run import fast_temporary_connections


def test_fast_replay_keeps_transactions_and_cannot_open_source_cache(tmp_path):
    source=tmp_path/'source.db';original=Database(source)
    with original.connect() as con:
        con.execute('CREATE TABLE replay_probe(value INTEGER)')
        con.execute('INSERT INTO replay_probe VALUES(7)')
    source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
    target=tmp_path/'disposable.db';target.write_bytes(source.read_bytes())
    with fast_temporary_connections(Database,target):
        db=Database(target)
        with db.connect() as con:
            assert con.execute('PRAGMA synchronous').fetchone()[0]==0
            con.execute('INSERT INTO replay_probe VALUES(11)')
        with pytest.raises(RuntimeError,match='injected'):
            with db.connect() as con:
                con.execute('INSERT INTO replay_probe VALUES(99)')
                raise RuntimeError('injected')
        with db.connect() as con:
            assert con.execute('SELECT value FROM replay_probe').fetchall()==[(7,),(11,)]
        with pytest.raises(RuntimeError,match='source or production'):
            with original.connect(): pass
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_sha
    with original.connect() as con:
        assert con.execute('PRAGMA synchronous').fetchone()[0]!=0


def test_two_fresh_processes_keep_the_previous_database_and_gzip_generation(tmp_path):
    import gzip,json,subprocess,sys
    import pandas as pd
    from pathlib import Path
    from scripts.export_web import export
    from scripts.fixed_input_regression import fixed_history,fundamentals
    from test_state_research import snapshot
    from engine.state_generation import json_bytes
    pages=snapshot(tmp_path)
    db_path=tmp_path/'source.sqlite';db=Database(db_path)
    meta={'1000':{'code':'1000','name':'Fixture','market':'Prime','sector33':'Fixture','size_class':'Mid'}}
    db.save_securities(list(meta.values()))
    frame=fixed_history(1000);frame.index=pd.bdate_range(end='2026-10-15',periods=len(frame))
    benchmark=fixed_history(777,benchmark=True);benchmark.index=frame.index
    fixed={'clock':'2026-10-16T06:00:00+09:00','names':{'1000':'Fixture'},'frames':{'1000':frame},
           'fundamentals':{'1000':fundamentals('1000')},'sources':{'1000':'FIXED'},
           'meta':meta,'actions':{},'benchmark':benchmark,'benchmark_source':'FIXED'}
    export(False,'主要500+Growth',db_path=db_path,dashboard=pages,captured_input=fixed)
    def frame_value(df):
        return {'index':[str(day) for day in df.index],'columns':list(df.columns),'data':df.to_numpy().tolist()}
    captured={**fixed,'frames':{'1000':frame_value(frame)},'benchmark':frame_value(benchmark)}
    inputs=tmp_path/'inputs.json.gz';inputs.write_bytes(gzip.compress(json_bytes(captured),mtime=0))
    digest=hashlib.sha256(db_path.read_bytes()).hexdigest()
    root=Path(__file__).parents[1]
    result=subprocess.run([sys.executable,str(root/'scripts/state_dry_run.py'),'run','--fast-temp-db',
                           '--db',str(db_path),'--inputs',str(inputs),'--pages',str(pages),
                           '--baseline',str(root),'--work',str(tmp_path/'runs'),'--report',str(tmp_path/'result.json')],
                          capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads((tmp_path/'result.json').read_bytes())
    assert report['passed'] is True
    assert report['cycles']==2
    for mode in ('legacy','gzip'):
        rows=report['results'][mode]['cycles']
        assert [r['iteration'] for r in rows]==[1,2]
        assert rows[0]['worker_pid']!=rows[1]['worker_pid']
    assert hashlib.sha256(db_path.read_bytes()).hexdigest()==digest
