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
