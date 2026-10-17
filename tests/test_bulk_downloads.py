import gzip
import json

import pytest

from engine.bulk_downloads import stage_downloads, restore_downloads
from engine.state_io import StateError


def test_bulk_export_byte_identity_contracts_and_recovery(tmp_path):
    (tmp_path/'validation').mkdir()
    raw=b'code,value\r\n1000,1.2345678901234567\r\n'
    (tmp_path/'validation/controls.csv').write_bytes(raw)
    untouched=b'{"summary":1.2345678901234567}'
    (tmp_path/'validation/summary.json').write_bytes(untouched)
    (tmp_path/'validation/index.json').write_text('{"available_files":["controls.csv","summary.json"]}')
    (tmp_path/'index.html').write_text('<a href="./validation/controls.csv">CSV</a></body>')
    checksum=stage_downloads(tmp_path)
    packed=(tmp_path/'validation/controls.csv.gz').read_bytes()
    assert gzip.decompress(packed)==raw
    assert not (tmp_path/'validation/controls.csv').exists()
    assert (tmp_path/'validation/summary.json').read_bytes()==untouched
    assert 'data-original-sha256' in (tmp_path/'index.html').read_text()
    assert stage_downloads(tmp_path)==checksum
    assert (tmp_path/'validation/controls.csv.gz').read_bytes()==packed
    restore_downloads(tmp_path)
    assert (tmp_path/'validation/controls.csv').read_bytes()==raw
    assert not (tmp_path/'validation/controls.csv.gz').exists()
    assert 'href="./validation/controls.csv"' in (tmp_path/'index.html').read_text()
    assert json.loads((tmp_path/'validation/index.json').read_bytes())['available_files']==['controls.csv','summary.json']


def test_bulk_recovery_stops_on_checksum_mismatch(tmp_path):
    (tmp_path/'experimental').mkdir()
    (tmp_path/'experimental/controls.json').write_text('[{"value":1.5}]')
    stage_downloads(tmp_path)
    (tmp_path/'experimental/controls.json.gz').write_bytes(gzip.compress(b'[]'))
    with pytest.raises(StateError,match='checksum'):
        restore_downloads(tmp_path)
    assert not (tmp_path/'experimental/controls.json').exists()


def test_committee_history_reference_paths_keep_original_json(tmp_path):
    from engine.bulk_downloads import RAW_CONTRACTS
    values={}
    for name in RAW_CONTRACTS:
        path=tmp_path/name;path.parent.mkdir(exist_ok=True)
        values[name]=b'[{"close":1.2345678901234567}]'
        path.write_bytes(values[name])
        path.with_suffix('.csv').write_bytes(b'close\r\n1.2345678901234567\r\n')
    stage_downloads(tmp_path)
    manifest=json.loads((tmp_path/'bulk-downloads.json').read_bytes())
    for name,raw in values.items():
        assert (tmp_path/name).read_bytes()==raw
        assert not (tmp_path/(name+'.gz')).exists()
        assert name not in manifest['files']
        assert (tmp_path/(name[:-5]+'.csv.gz')).exists()
