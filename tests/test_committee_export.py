import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from engine.committee_export import export_committee, normalize_ticker


ROOT = Path(__file__).parents[1]
SCHEMA = ROOT / "schemas" / "investment_committee_engine.schema.json"


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _dashboard(tmp_path: Path) -> Path:
    dashboard = tmp_path / "dashboard"
    candidates = [
        {"code": "9244.T", "name": "デジタリフト", "state": "SETUP FORMING"},
        {"code": "7371", "name": "全研本社", "state": "WATCH"},
    ]
    _dump(dashboard / "data" / "snapshot.json", {
        "as_of": "2026-09-03",
        "logic_version": "logic-test-v1",
        "candidates": candidates,
    })
    detail = {
        "code": "9244.T",
        "name": "デジタリフト",
        "state": "SETUP FORMING",
        "confidence": 0.75,
        "source": "Yahoo Finance",
        "price": 920.0,
        "pivot": 950.0,
        "pivot_type": "20d high",
        "methods": {"Minervini": "○", "CAN SLIM": "N/A"},
        "strategies": {
            "Minervini": {"coverage": 1.0, "confidence": 0.8, "pivot": 950.0,
                           "summary": "trend template"},
            "CAN SLIM": {"coverage": 0.5, "confidence": None, "pivot": None,
                         "summary": "annual EPS missing"},
        },
        "confluence": 1,
        "coverage": 0.8,
        "consensus_pivot_fidelity": "PRACTICAL",
        "liquidity_level": "HIGH",
        "liquid": True,
        "trading_value_20d": 300000000,
        "metrics": {
            "momentum_6m": 0.22,
            "quarterly_earnings": {
                "coverage": 50.0,
                "source": "Yahoo",
                "fidelity": "PROXY",
                "missing": ["sales_growth"],
                "eps_growth": None,
            },
        },
        "annual_earnings": {
            "status": "INSUFFICIENT",
            "source_summary": "EDINET",
            "fidelity": "STRICT",
            "reason_code": "INSUFFICIENT_YEARS",
            "years_available": 2,
        },
        "experimental": {
            "alignment": "1/3",
            "combination": "EARNINGS_ONLY",
            "results": {
                "Earnings Momentum": {
                    "state": "N/A", "positive": None, "coverage": 0.5,
                    "fidelity": "PROXY", "metrics": {"eps_growth": None},
                }
            },
        },
    }
    _dump(dashboard / "data" / "details" / "9244.json", detail)
    _dump(dashboard / "data" / "details" / "7371.json", {
        **detail, "code": "7371", "name": "全研本社", "state": "WATCH",
        "confidence": None,
    })
    return dashboard


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_normalize_ticker_uses_provider_independent_jpx_code():
    assert normalize_ticker("9244.T") == "9244"
    assert normalize_ticker(" abc1 ") == "ABC1"


def test_export_creates_schema_valid_documents_manifest_and_ranking(tmp_path):
    dashboard = _dashboard(tmp_path)
    output = dashboard / "committee"
    source_files = [dashboard / "data" / "snapshot.json", *
                    sorted((dashboard / "data" / "details").glob("*.json"))]
    before = {path: _sha(path) for path in source_files}

    manifest = export_committee(
        dashboard, output, SCHEMA,
        config={"threshold_version": "cfg-v1", "strategy_version": "strategy-v1"},
        generated_at="2026-09-05T12:34:56+09:00", git_commit="abc123",
    )

    document = json.loads((output / "latest" / "9244.json").read_text())
    schema = json.loads(SCHEMA.read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)
    assert document["ticker"] == "9244"
    assert document["provider_ticker"] == "9244.T"
    assert document["evaluation_date"] == "2026-09-03"
    assert document["generated_at"] == "2026-09-05T12:34:56+09:00"
    assert document["evaluation_date"] != document["generated_at"]
    assert document["core"]["verdict"] == "SETUP FORMING"
    assert document["experimental"]["affects_core_ranking"] is False
    assert document["experimental"]["signals"]["earnings_momentum"]["status"] == "N/A"
    assert document["metrics"]["quarterly_earnings"]["eps_growth"] is None
    assert document["source_status"]["fundamentals"]["status"] == "missing"
    assert document["source_status"]["fundamentals"]["reason_code"] == "INSUFFICIENT_YEARS"
    assert document["source_status"]["earnings"]["status"] == "partial"
    assert document["risk_level"] is None
    assert manifest["count"] == 2
    assert manifest["files"][0] == {"ticker": "9244", "path": "latest/9244.json"}
    ranking = json.loads((output / "ranking.json").read_text())
    assert [row["ticker"] for row in ranking["ranking"]] == ["9244", "7371"]
    assert (output / "schema.json").exists()
    assert (output / "history" / "2026-09-03" / "ranking.json").exists()
    assert before == {path: _sha(path) for path in source_files}


def test_history_seed_is_joined_and_retained_by_evaluation_date(tmp_path):
    dashboard = _dashboard(tmp_path)
    seed = tmp_path / "published" / "committee"
    _dump(seed / "history" / "index.json", {
        "engine": "japan_swing_lens",
        "snapshots": [{
            "evaluation_date": "2026-09-02", "generated_at": "2026-09-02T19:00:00+09:00",
            "engine_version": "logic-test-v1", "git_commit": "old", "count": 1,
            "ranking": [{"rank": 1, "ticker": "1111", "verdict": "WATCH",
                         "core_verdict": "WATCH", "experimental_alignment": None}],
        }],
    })
    output = dashboard / "committee"
    export_committee(dashboard, output, SCHEMA, generated_at="2026-09-05T12:00:00+09:00",
                     git_commit="new", history_seed_url=seed.as_uri(), history_limit=260)
    history = json.loads((output / "history" / "index.json").read_text())
    assert [row["evaluation_date"] for row in history["snapshots"]] == [
        "2026-09-02", "2026-09-03"]
    assert (output / "history" / "2026-09-02" / "ranking.json").exists()


def test_failed_export_does_not_replace_previous_committee_directory(tmp_path):
    dashboard = _dashboard(tmp_path)
    output = dashboard / "committee"
    _dump(output / "marker.json", {"kept": True})
    snapshot_path = dashboard / "data" / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    snapshot["candidates"].append({"code": "bad/ticker", "name": "invalid"})
    _dump(snapshot_path, snapshot)

    try:
        export_committee(dashboard, output, SCHEMA)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid ticker must fail export")

    assert json.loads((output / "marker.json").read_text()) == {"kept": True}
    assert not (output / "manifest.json").exists()
