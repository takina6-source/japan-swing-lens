import json
from pathlib import Path

import pytest

from engine.simple_changes import compact_snapshot, compare_snapshots, validate_compact
from scripts.export_simple_changes import export


def core(as_of="2026-09-11", scope="主要500+Growth", rows=None):
    return {
        "as_of": as_of, "scope": scope,
        "candidates": rows or [
            {"code": "1001", "name": "A", "state": "SETUP FORMING"},
            {"code": "1002", "name": "B", "state": "BREAKOUT WATCH"},
            {"code": "1003", "name": "C", "state": "BREAKOUT"},
        ],
    }


def test_compact_snapshot_keeps_only_code_name_state_and_rank():
    source = core()
    source["candidates"][0]["large_internal_object"] = {"prices": list(range(100))}
    result = compact_snapshot(source)
    assert result["stock_count"] == 3
    assert result["stocks"][0] == {
        "code": "1001", "name": "A", "state": "SETUP FORMING", "rank": 1
    }
    assert "large_internal_object" not in json.dumps(result)
    assert validate_compact(result) is result


def test_compare_reports_state_and_material_rank_changes_by_code():
    prior_rows = [{"code": f"{1000+i}", "name": f"銘柄{i}", "state": "SETUP FORMING"}
                  for i in range(1, 16)]
    current_rows = list(reversed(prior_rows))
    current_rows[-1] = {**current_rows[-1], "state": "BREAKOUT WATCH"}
    report = compare_snapshots(compact_snapshot(core(rows=prior_rows)),
                               compact_snapshot(core("2026-09-12", rows=current_rows)))
    assert report["status"] == "READY"
    assert report["state_changes"] == [{
        "code": "1001", "name": "銘柄1", "from": "SETUP FORMING", "to": "BREAKOUT WATCH"
    }]
    assert {row["code"] for row in report["rank_changes"]} >= {"1001", "1015"}


def test_first_run_and_same_market_date_do_not_make_false_changes():
    current = compact_snapshot(core())
    assert compare_snapshots(None, current)["status"] == "BASELINE_ONLY"
    assert compare_snapshots(current, current)["status"] == "NO_NEW_MARKET_DATE"


def test_guard_stops_incomparable_scope():
    previous = compact_snapshot(core())
    current = compact_snapshot(core("2026-09-12", scope="別範囲"))
    report = compare_snapshots(previous, current)
    assert report["status"] == "UNAVAILABLE"
    assert report["reason"] == "SCOPE_CHANGED"


def test_export_restores_published_history_and_keeps_30_dates(tmp_path: Path):
    published = tmp_path / "published" / "snapshot-history"
    published.mkdir(parents=True)
    entries = []
    for day in range(1, 31):
        item = compact_snapshot(core(f"2026-08-{day:02d}"))
        filename = f'snapshot-{item["as_of"]}.json'
        (published / filename).write_text(json.dumps(item), encoding="utf-8")
        entries.append({"as_of": item["as_of"], "file": filename, "sha256": item["source_hash"]})
    (published / "index.json").write_text(json.dumps({
        "schema_version": "simple-history-index-v1", "entries": entries
    }), encoding="utf-8")
    dashboard = tmp_path / "dashboard"
    (dashboard / "data").mkdir(parents=True)
    (dashboard / "data" / "snapshot.json").write_text(
        json.dumps(core("2026-09-01")), encoding="utf-8")
    report = export(dashboard, published.as_uri(), 30, 2)
    index = json.loads((dashboard / "snapshot-history" / "index.json").read_text())
    assert report["previous_date"] == "2026-08-30"
    assert len(index["entries"]) == 30
    assert index["entries"][0]["as_of"] == "2026-09-01"
    assert not (dashboard / "snapshot-history" / "snapshot-2026-08-01.json").exists()
    assert (dashboard / "briefing" / "what-changed.txt").exists()
