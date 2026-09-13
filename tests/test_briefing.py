import copy
import hashlib
import json
import socket
import sqlite3
from pathlib import Path

import pytest
import yaml
from jsonschema import ValidationError

from engine.briefing import METHODS, ROOT, build_brief, export_brief, validate_brief

NOW = "2026-09-10T20:00:00+09:00"
CONFIG = {"logic_version": "test-v1", "strategy_version": "pivot-consensus-v1",
          "threshold_version": "test-thresholds", "trade_plan": {
              "first_target_r_multiple": 1, "main_target_r_multiple": 2}}


def inputs():
    candidate = {"code": "1000", "name": "固定入力・テスト銘柄", "price": 101,
                 "state": "BREAKOUT WATCH", "aligned_strategy_count": 5,
                 "breakout_strategy_count": 1, "confluence": 3, "pivot": 100,
                 "pivot_type": "VCP", "pivot_basis": "Structure", "pivot_fidelity": "PRACTICAL",
                 "consensus_pivot_fidelity": "STRICT", "momentum_percentile": 95,
                 "trading_value_20d": 100000000, "trading_value": 200000000,
                 "trading_value_ratio": 2, "liquidity_level": "GOOD", "liquid": True,
                 "coverage": 100, "confidence": "HIGH",
                 "methods": {name: "BREAKOUT WATCH" for name in METHODS},
                 "trade_plan": {"status": "エントリー帯", "entry_low": 100, "entry_high": 102,
                                "stop": 94, "target_1r": 108, "target_2r": 115,
                                "basis": "既存の参考帯", "warning": ""}}
    condition = {"key": "dryup", "label": "出来高Dry-up", "verdict": "△",
                 "role": "SUPPORTING", "layer": "Entry Setup", "value": .8,
                 "reference": .65, "unit": "倍", "fidelity": "PRACTICAL", "note": ""}
    detail = {**copy.deepcopy(candidate), "as_of": "2026-09-10", "source": "Yahoo Finance",
              "strategies": {name: {"state": candidate["methods"][name],
                                    "conditions": [copy.deepcopy(condition)]} for name in METHODS}}
    snapshot = {"generated_at": NOW, "as_of": "2026-09-10", "scope": "固定入力",
                "universe_count": 1, "logic_version": "test-v1",
                "benchmark_source": "1306 TOPIX ETF Yahoo", "candidates": [candidate]}
    return snapshot, {"1000": detail}


def build(snapshot, details, **kwargs):
    payload = build_brief(snapshot, details, CONFIG, generated_at=NOW, **kwargs)
    validate_brief(payload)
    return payload


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_core_fields_risk_and_distinct_counts():
    s, d = inputs()
    before = copy.deepcopy((s, d, CONFIG))
    p = build(s, d, same_run_config=True)
    row = p["stocks"][0]
    assert p["status"] == "OK"
    assert (row["aligned_count"], row["breakout_count"], row["confluence"]) == (5, 1, 3)
    assert row["pivot_fidelity"] == "PRACTICAL" and row["consensus_pivot_fidelity"] == "STRICT"
    assert row["pivot_distance_pct"] == pytest.approx((100 / 101 - 1) * 100)
    assert row["trade_plan"]["risk_per_share"] == 7
    assert row["volume_ratio"] == .8
    assert (s, d, CONFIG) == before
    assert build(s, d, same_run_config=True) == p


@pytest.mark.parametrize("price,entry", [(90, 100), (101, 101), (110, 102)])
def test_risk_uses_clamped_reference_entry(price, entry):
    s, d = inputs()
    c = s["candidates"][0]
    c["price"] = price
    c["trade_plan"].update(target_1r=entry + (entry - 94), target_2r=entry + 2 * (entry - 94))
    d["1000"].update(copy.deepcopy(c))
    assert build(s, d, same_run_config=True)["stocks"][0]["trade_plan"]["risk_per_share"] == entry - 94


def test_unverified_config_and_target_mismatch_do_not_invent_risk():
    s, d = inputs()
    p = build(s, d)
    assert p["versions"]["strategy_version"] is None
    assert p["stocks"][0]["trade_plan"]["risk_per_share"] is None
    s["candidates"][0]["trade_plan"]["target_2r"] = 999
    d["1000"]["trade_plan"]["target_2r"] = 999
    p = build(s, d, same_run_config=True)
    assert p["stocks"][0]["trade_plan"]["target_2r"] == 999
    assert p["stocks"][0]["trade_plan"]["risk_per_share"] is None


def test_detail_missing_mismatch_and_old_dates_keep_rank():
    s, d = inputs()
    s["candidates"].append({**copy.deepcopy(s["candidates"][0]), "code": "1001"})
    s["universe_count"] = 2
    d["1000"]["as_of"] = "2026-09-09"
    p = build(s, d, limit=0)
    assert [r["rank"] for r in p["stocks"]] == [1, 2]
    assert p["stocks"][0]["date_status"] == "OLDER_THAN_SNAPSHOT"
    assert p["stocks"][1]["analysis_date"] is None
    assert build(s, d, limit=1)["output_count"] == 1
    d["1000"]["price"] = 999
    p = build(s, d)
    assert p["stocks"][0]["close"] == 101
    assert p["stocks"][0]["analysis_date"] is None
    assert any(i["reason"] == "DETAIL_MISMATCH" for i in p["stocks"][0]["issues"])


@pytest.mark.parametrize("bad", [None, 0, -1, float("nan"), float("inf")])
def test_bad_price_never_produces_nonfinite_or_fabricated_risk(bad):
    s, d = inputs()
    s["candidates"][0]["price"] = bad
    d["1000"]["price"] = bad
    p = build(s, d, same_run_config=True)
    assert p["stocks"][0]["pivot_distance_pct"] is None
    assert p["stocks"][0]["trade_plan"]["risk_per_share"] is None
    json.dumps(p, allow_nan=False)


def test_skipped_plan_na_and_allowlist():
    s, d = inputs()
    s["candidates"][0]["trade_plan"] = {"status": "見送り", "basis": "条件未成立", "warning": ""}
    d["1000"].update(copy.deepcopy(s["candidates"][0]))
    c = d["1000"]["strategies"]["CAN SLIM"]["conditions"][0]
    c.update(verdict="N/A", value=None, note="必要な年次EPSが不足")
    s["secret"] = d["1000"]["secret"] = "DO_NOT_EXPORT"
    s["candidates"][0]["experimental"] = {"secret": "DO_NOT_EXPORT"}
    c["internal"] = {"password": "DO_NOT_EXPORT"}
    c["reference"] = {"secret": "DO_NOT_EXPORT"}
    config = {**CONFIG, "api_key": "DO_NOT_EXPORT", "path": "/Users/private"}
    p = build_brief(s, d, config, generated_at=NOW, same_run_config=True)
    validate_brief(p)
    assert "DO_NOT_EXPORT" not in json.dumps(p)
    assert "experimental" not in p["stocks"][0]
    assert p["stocks"][0]["strategies"]["CAN SLIM"]["conditions"][0]["verdict"] == "N/A"
    assert any(i["reason"] == "PLAN_SKIPPED" for i in p["stocks"][0]["issues"])


@pytest.mark.parametrize("mode", ["empty", "duplicate", "path", "date", "methods"])
def test_invalid_snapshot_explicitly_unavailable(mode):
    s, d = inputs()
    if mode == "empty": s["candidates"] = []
    if mode == "duplicate": s["candidates"] *= 2
    if mode == "path": s["candidates"][0]["code"] = "../secret"
    if mode == "date": s["as_of"] = "bad-date"
    if mode == "methods": s["candidates"][0]["methods"] = []
    p = build(s, d)
    assert p["status"] == "UNAVAILABLE" and p["stocks"] == []


def test_schema_rejects_extra_output_fields():
    p = build(*inputs())
    p["stocks"][0]["setup_id"] = "not-in-phase1"
    with pytest.raises(ValidationError):
        validate_brief(p)


def test_default_limit_and_full_selection():
    s, d = inputs()
    s["candidates"] = [{**copy.deepcopy(s["candidates"][0]), "code": str(1000 + i)} for i in range(25)]
    s["universe_count"] = 25
    assert build(s, d)["output_count"] == 20
    assert [r["rank"] for r in build(s, d, limit=0)["stocks"]] == list(range(1, 26))


def test_quality_count_and_config_mismatch_are_explicit():
    s, d = inputs()
    s.update(universe_count=-1, benchmark_source="代替benchmark")
    s["candidates"][0].update(coverage=40, confidence="LOW", aligned_strategy_count=6)
    d["1000"].update(copy.deepcopy(s["candidates"][0]))
    p = build_brief(s, d, {**CONFIG, "logic_version": "other"}, generated_at=NOW, same_run_config=True)
    validate_brief(p)
    assert p["versions"]["strategy_version"] is None
    assert p["stocks"][0]["aligned_count"] is None
    assert {i["reason"] for i in p["issues"]} >= {"COUNT_MISMATCH", "BENCHMARK_FALLBACK", "VERSION_UNVERIFIED"}
    assert {i["reason"] for i in p["stocks"][0]["issues"]} >= {"COVERAGE_INCOMPLETE", "LOW_CONFIDENCE"}


def test_source_paths_are_not_exported_and_schema_requires_fields():
    s, d = inputs()
    d["1000"]["source"] = "/Users/private/source.json"
    p = build(s, d)
    assert "/Users/" not in json.dumps(p)
    del p["stocks"][0]["volume_ratio"]
    with pytest.raises(ValidationError):
        validate_brief(p)


def test_malformed_detail_remains_partial_not_global_failure():
    s, d = inputs()
    d["1000"]["strategies"]["Connors"] = ["invalid"]
    p = build(s, d)
    assert p["status"] == "PARTIAL" and p["output_count"] == 1


def test_concurrent_snapshot_change_is_unavailable(tmp_path, monkeypatch):
    s, d = inputs()
    root = tmp_path / "dashboard"
    dump(root / "data/snapshot.json", s)
    dump(root / "data/details/1000.json", d["1000"])
    original = Path.read_text
    reads = 0
    def changed(path, *args, **kwargs):
        nonlocal reads
        if path.name == "snapshot.json":
            reads += 1
            if reads == 2:
                return json.dumps({**s, "generated_at": "2026-09-10T21:00:00+09:00"})
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", changed)
    p = export_brief(root, root / "briefing", CONFIG, generated_at=NOW)
    assert p["status"] == "UNAVAILABLE"


def test_cli_uses_offline_inputs(tmp_path):
    import subprocess
    import sys
    s, d = inputs()
    dump(tmp_path / "data/snapshot.json", s)
    dump(tmp_path / "data/details/1000.json", d["1000"])
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(CONFIG))
    run = subprocess.run([sys.executable, str(ROOT / "scripts/export_briefing.py"),
                          "--dashboard-root", str(tmp_path), "--output", str(tmp_path / "briefing"),
                          "--config", str(tmp_path / "config.yaml"), "--same-run-config"],
                         capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)["status"] == "OK"


def test_export_is_offline_readonly_and_overwrites_failure(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network/database forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    s, d = inputs()
    root = tmp_path / "dashboard"
    dump(root / "data/snapshot.json", s)
    dump(root / "data/details/1000.json", d["1000"])
    dump(root / "data/details/9999.json", {"secret": "never read"})
    files = list((root / "data").rglob("*.json"))
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    output = root / "briefing"
    p = export_brief(root, output, CONFIG, generated_at=NOW, same_run_config=True)
    assert p["status"] == "OK"
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert set(p["input_hashes"]["details"]) == {"1000"}
    (root / "data/snapshot.json").write_text("broken")
    p = export_brief(root, output, CONFIG, generated_at=NOW)
    assert p["status"] == "UNAVAILABLE"
    assert json.loads((output / "latest.json").read_text())["stocks"] == []


def test_symlinks_and_overlapping_output_rejected(tmp_path):
    s, d = inputs()
    root = tmp_path / "dashboard"
    dump(root / "data/snapshot.json", s)
    (root / "data/details").mkdir()
    secret = tmp_path / "secret.json"
    dump(secret, d["1000"])
    (root / "data/details/1000.json").symlink_to(secret)
    p = export_brief(root, root / "briefing", CONFIG, generated_at=NOW)
    assert any(i["reason"] == "DETAIL_UNSAFE_PATH" for i in p["stocks"][0]["issues"])
    with pytest.raises(ValueError):
        export_brief(root, root / "data", CONFIG, generated_at=NOW)


def test_atomic_failure_removes_old_latest(tmp_path, monkeypatch):
    import engine.briefing as module
    s, d = inputs()
    dump(tmp_path / "data/snapshot.json", s)
    dump(tmp_path / "data/details/1000.json", d["1000"])
    dump(tmp_path / "briefing/latest.json", {"status": "OK", "old": True})
    def fail(*args): raise OSError("write failure")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        export_brief(tmp_path, tmp_path / "briefing", CONFIG, generated_at=NOW)
    assert not (tmp_path / "briefing/latest.json").exists()
    assert not list((tmp_path / "briefing").glob(".brief-*"))


def test_export_embeds_optional_lightweight_what_changed_summary(tmp_path):
    s, d = inputs()
    root = tmp_path / "dashboard"
    dump(root / "data/snapshot.json", s)
    dump(root / "data/details/1000.json", d["1000"])
    output = root / "briefing"
    dump(output / "what-changed.json", {
        "schema_version": "simple-what-changed-v1", "status": "READY", "reason": None,
        "current_date": "2026-09-10", "previous_date": "2026-09-09",
        "scope": "固定入力", "current_count": 1, "previous_count": 1,
        "state_changes": [{"code": "1000"}], "rank_changes": [],
        "added_codes": [], "removed_codes": [], "summary_lines": ["1000：形成中 → 直前"],
        "disclaimer": "銘柄コード単位の参考比較です。",
    })
    payload = export_brief(root, output, CONFIG, generated_at=NOW, same_run_config=True)
    assert payload["what_changed"]["state_change_count"] == 1
    assert payload["what_changed"]["previous_date"] == "2026-09-09"
    validate_brief(payload)


def test_workflow_runs_after_core_and_excludes_failure():
    workflow = yaml.safe_load((ROOT / ".github/workflows/update-dashboard.yml").read_text())
    steps = workflow["jobs"]["analyze-and-publish"]["steps"]
    core = next(i for i, s in enumerate(steps) if s.get("name") == "Analyze latest market data")
    brief = next(i for i, s in enumerate(steps) if s.get("id") == "briefing")
    assert core < brief
    assert steps[brief]["continue-on-error"]
    assert "--same-run-config" in steps[brief]["run"]
    assert steps[brief + 1]["if"] == "steps.briefing.outcome == 'failure'"
