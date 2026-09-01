import json

import pandas as pd

from engine.analyzer import analyze, prepare_universe, rank
from engine.config import load_config
from engine.data.demo import demo_fundamentals, make_demo_history
from engine.database import Database
from engine.experimental.analyzer import (ExperimentalAnalysis, ExperimentalResult,
                                          analyze_experimental_universe,
                                          earnings_momentum, sector_result, turtle)
from engine.experimental.validation import export_experimental, update_experimental_tracking
from engine.indicators import enrich
from engine.models import SetupState


def _turtle_frame(last=(100, 102), periods=220):
    index = pd.bdate_range("2026-01-05", periods=periods)
    close = [100.0] * periods
    close[-2], close[-1] = last
    frame = pd.DataFrame({"open": close, "high": [101.0] * periods,
                          "low": [99.0] * periods, "close": close,
                          "volume": [100_000.0] * periods}, index=index)
    frame.loc[index[-1], "high"] = max(close[-1], 101) + 1
    return enrich(frame)


def test_turtle_breakout_uses_t_minus_one_and_records_atr_stop():
    cfg = load_config()
    frame = _turtle_frame()
    result = turtle(frame, cfg)
    assert result.state == "BREAKOUT"
    assert result.metrics["breakout_price"] == 101.0
    assert result.metrics["atr"] > 0
    assert result.metrics["stop"] < float(frame.close.iloc[-1])
    changed = frame.copy()
    changed.loc[changed.index[-1], "high"] = 10_000
    assert turtle(changed, cfg).metrics["breakout_price"] == 101.0


def test_turtle_existing_breakout_is_not_new_and_pivot_failure_is_failed():
    cfg = load_config()
    trending = _turtle_frame(last=(102, 103))
    assert turtle(trending, cfg).state == "TRENDING"
    failed = _turtle_frame(last=(102, 100))
    assert turtle(failed, cfg).state == "FAILED"


def test_earnings_acceleration_deceleration_turnaround_missing_and_anomaly():
    cfg = load_config()
    strong = earnings_momentum({"eps_growth": 40, "sales_growth": 25,
        "annual_eps": [{"fiscal_year": "2023", "eps": 100},
                       {"fiscal_year": "2024", "eps": 120},
                       {"fiscal_year": "2025", "eps": 156}]}, cfg, "2026-09-01", "1000")
    assert strong.state == "STRONG"
    slowing = earnings_momentum({"eps_growth": 40, "sales_growth": 25,
        "annual_eps": [{"fiscal_year": "2023", "eps": 100},
                       {"fiscal_year": "2024", "eps": 150},
                       {"fiscal_year": "2025", "eps": 165}]}, cfg)
    assert slowing.state != "STRONG"
    turnaround = earnings_momentum({"annual_eps": [
        {"fiscal_year": "2024", "eps": -10}, {"fiscal_year": "2025", "eps": 20}]}, cfg)
    assert turnaround.state == "IMPROVING" and turnaround.metrics["turnaround_flag"]
    missing = earnings_momentum({}, cfg)
    assert missing.state == "NOT QUALIFIED" and missing.coverage == 0
    anomaly = earnings_momentum({"annual_eps": [
        {"fiscal_year": "2023", "eps": 1}, {"fiscal_year": "2024", "eps": 10},
        {"fiscal_year": "2025", "eps": 11}]}, cfg)
    assert anomaly.state == "IMPROVING" and anomaly.metrics["anomaly_flag"]


def test_sector_leader_requires_both_sector_and_stock_strength():
    cfg = load_config()
    leading = {"1000": {"sector_name": "情報・通信業", "sector_percentile": 95}}
    assert sector_result("1000", 90, leading, cfg).state == "LEADING SECTOR"
    weak_sector = {"1000": {"sector_name": "情報・通信業", "sector_percentile": 50}}
    assert not sector_result("1000", 90, weak_sector, cfg).positive
    assert not sector_result("1000", 50, leading, cfg).positive


def test_experimental_analysis_does_not_mutate_core_state_or_ranking():
    cfg = load_config()
    raw = {code: make_demo_history(code) for code in ("7203", "6758", "8306")}
    benchmark = make_demo_history("TOPIX")
    prepared = prepare_universe(raw, benchmark)
    core = rank([analyze(code, code, frame, demo_fundamentals(code), "DEMO",
                         benchmark, cfg) for code, frame in prepared.items()])
    before = [(item.code, item.state, item.breakout_strategy_count,
               item.aligned_strategy_count, item.confluence, item.rank_key) for item in core]
    meta = {code: {"sector33": "輸送用機器" if code == "7203" else "電気機器"}
            for code in raw}
    analyze_experimental_universe(core, prepared,
                                  {code: demo_fundamentals(code) for code in raw},
                                  meta, benchmark, cfg)
    after = [(item.code, item.state, item.breakout_strategy_count,
              item.aligned_strategy_count, item.confluence, item.rank_key) for item in core]
    assert after == before


def test_experimental_does_not_backfill_before_start_date(tmp_path):
    cfg = load_config()
    cfg["experiment_start_date"] = "2026-09-01"
    db = Database(tmp_path / "no-backfill.db")
    frame = enrich(make_demo_history("7203"))
    core = analyze("7203", "Toyota", frame, demo_fundamentals("7203"),
                   "TEST", make_demo_history("TOPIX"), cfg)
    core.as_of = "2026-08-31"
    positive = ExperimentalResult("TURTLE", "BREAKOUT", True, True, {}, 100,
                                  "STRICT", "TURTLE:2026-08-31:20")
    experimental = {"7203": ExperimentalAnalysis(
        "7203", "Toyota", "2026-08-31", {"TURTLE": positive}, 1, "TURTLE")}
    update_experimental_tracking(db, experimental, [core], {"7203": frame},
                                 make_demo_history("TOPIX"), {"7203": {}}, cfg)
    snapshots, history, controls, control_history = db.experimental_rows()
    assert snapshots == history == controls == control_history == []


def test_experimental_snapshot_is_immutable_tracks_and_exports_separately(tmp_path):
    cfg = load_config()
    cfg["experiment_start_date"] = "2026-09-01"
    cfg["experimental"]["controls"]["random_count"] = 1
    cfg["experimental"]["controls"]["matched_count"] = 1
    db = Database(tmp_path / "experimental.db")
    dates = pd.bdate_range("2026-09-01", periods=2)
    frames = {}
    analyses = []
    for code, prices, state in (("1000", [100, 102], SetupState.BREAKOUT),
                                ("2000", [100, 101], SetupState.NOT_QUALIFIED)):
        raw = pd.DataFrame({"open": prices, "high": [p * 1.01 for p in prices],
                            "low": [p * .99 for p in prices], "close": prices,
                            "volume": [1_000_000] * 2}, index=dates)
        frames[code] = enrich(raw)
        item = analyze(code, code, enrich(make_demo_history(code)), demo_fundamentals(code),
                       "TEST", make_demo_history("TOPIX"), cfg)
        item.as_of = "2026-09-01"; item.state = state
        item.setup_id = "core-1000" if code == "1000" else None
        item.metrics.update({"price": prices[0], "trading_value_20d": 1e9,
                             "momentum_percentile": 90 if code == "1000" else 50})
        analyses.append(item)
    benchmark = pd.DataFrame({"close": [100, 100]}, index=dates)
    db.save_signal_tracking(analyses[0], frames["1000"].iloc[:1], benchmark.iloc[:1], cfg)
    positive = ExperimentalResult("TURTLE", "BREAKOUT", True, True,
                                  {"breakout_price": 101}, 100, "STRICT", "TURTLE:2026-09-01:20")
    neutral = ExperimentalResult("TURTLE", "NOT QUALIFIED", False, False, {}, 100, "STRICT", None)
    exp = {"1000": ExperimentalAnalysis("1000", "1000", "2026-09-01",
                                        {"TURTLE": positive}, 1, "TURTLE"),
           "2000": ExperimentalAnalysis("2000", "2000", "2026-09-01",
                                        {"TURTLE": neutral}, 0, "NONE")}
    meta = {code: {"market": "プライム", "size_class": "Mid400"} for code in frames}
    update_experimental_tracking(db, exp, analyses,
                                 {code: frame.iloc[:1] for code, frame in frames.items()},
                                 benchmark.iloc[:1], meta, cfg)
    analyses[0].as_of = analyses[1].as_of = "2026-09-02"
    exp["1000"] = ExperimentalAnalysis("1000", "1000", "2026-09-02",
        {"TURTLE": ExperimentalResult("TURTLE", "TRENDING", False, False,
         {"breakout_price": 999}, 100, "STRICT", "TURTLE:2026-09-01:20")}, 0, "NONE")
    exp["2000"] = ExperimentalAnalysis("2000", "2000", "2026-09-02",
                                        {"TURTLE": neutral}, 0, "NONE")
    update_experimental_tracking(db, exp, analyses, frames, benchmark, meta, cfg)
    snapshots, history, controls, _ = db.experimental_rows()
    assert len(snapshots) == 1 and json.loads(snapshots[0]["metrics_json"])["breakout_price"] == 101
    assert {row["session_offset"] for row in history} == {0, 1}
    assert {row["control_type"] for row in controls} == {"RANDOM", "MATCHED"}
    core_signals, _ = db.validation_rows()
    assert core_signals[0]["experimental_alignment"] == 1
    index = export_experimental(db, tmp_path / "experimental", cfg)
    assert index["signal_count"] == 1
    assert (tmp_path / "experimental" / "performance.csv").exists()
    assert (tmp_path / "experimental" / "summary.json").exists()
