import json

import pandas as pd
import pytest

from engine.config import load_config
from engine.controls import (deterministic_random_codes, select_control_members,
                             track_control_members, update_controls)
from engine.database import Database
from engine.models import (ConditionResult, Fidelity, Layer, Role, SetupState,
                           StockAnalysis, StrategyResult, TradePlan, Verdict)
from engine.validation import export_validation


def _frame(closes, start="2026-01-05"):
    index = pd.bdate_range(start, periods=len(closes))
    values = [float(value) for value in closes]
    return pd.DataFrame({
        "open": values,
        "high": [value * 1.01 for value in values],
        "low": [value * .99 for value in values],
        "close": values,
        "volume": [100_000.0] * len(values),
        "volume_ratio": [1.5] * len(values),
    }, index=index)


def _strategy(name, state):
    conditions = [
        ConditionResult("required", "required", Verdict.PASS, Role.REQUIRED,
                        Layer.ENTRY_SETUP, fidelity=Fidelity.PRACTICAL),
        ConditionResult("trigger", "trigger", Verdict.PASS, Role.TRIGGER,
                        Layer.ENTRY_SETUP, fidelity=Fidelity.PRACTICAL),
    ]
    return StrategyResult(name, state, conditions, pivot=100, stop=93,
                          pivot_type="Test Base", pivot_basis="Structure",
                          pivot_fidelity=Fidelity.PRACTICAL,
                          pivot_formed_date="2025-12-30",
                          setup_start_date="2025-12-01", setup_id=f"{name}-setup")


def _analysis(code, state, date, price=100, momentum=90, liquidity=1e9, setup=None):
    trend_state = SetupState.BREAKOUT if state == SetupState.BREAKOUT else SetupState.NOT_QUALIFIED
    strategies = {name: _strategy(name, trend_state if index < 2 else SetupState.NOT_QUALIFIED)
                  for index, name in enumerate(
                      ("Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas"))}
    strategies["Connors"] = _strategy("Connors", SetupState.NOT_QUALIFIED)
    return StockAnalysis(
        code=code, name=f"Stock {code}", as_of=date, source="TEST",
        metrics={"momentum_percentile": momentum, "market_regime": "BULL",
                 "trading_value_20d": liquidity, "benchmark_price": 100,
                 "price": price},
        strategies=strategies, state=state,
        confluence=2 if state == SetupState.BREAKOUT else 0,
        breakout_strategy_count=2 if state == SetupState.BREAKOUT else 0,
        aligned_strategy_count=2 if state == SetupState.BREAKOUT else 0,
        coverage=100, confidence="HIGH", pivot_fidelity=Fidelity.PRACTICAL,
        setup_id=setup or f"{code}-setup",
        trade_plan=TradePlan("候補", 100, 102, 93, 107, 114, 120, 7, 2, "test"),
    )


def _meta(codes):
    return {code: {"market": "プライム（内国株式）",
                   "size_class": "TOPIX Mid400"} for code in codes}


def test_random_baseline_is_seeded_reproducible_and_excludes_signal():
    candidates = [f"{index:04d}" for index in range(1000, 1050)]
    first = deterministic_random_codes("signal-1", candidates, 20, "control-v1")
    second = deterministic_random_codes("signal-1", list(reversed(candidates)), 20, "control-v1")
    assert first == second
    assert len(first) == 20
    assert "signal-1" not in first


def test_matched_controls_exclude_signal_states_and_membership_is_immutable(tmp_path):
    cfg = load_config()
    cfg["controls"]["random_count"] = 3
    cfg["controls"]["matched_count"] = 2
    db = Database(tmp_path / "controls.db")
    frames = {code: _frame([price]) for code, price in {
        "1000": 100, "1001": 101, "1002": 103, "1003": 98, "1004": 120}.items()}
    date = str(frames["1000"].index[-1].date())
    signal = _analysis("1000", SetupState.BREAKOUT, date, setup="stable")
    excluded_breakout = _analysis("1001", SetupState.BREAKOUT, date, price=101, momentum=90)
    excluded_watch = _analysis("1002", SetupState.BREAKOUT_WATCH, date, price=103, momentum=89)
    controls = [_analysis("1003", SetupState.NOT_QUALIFIED, date, price=98, momentum=88),
                _analysis("1004", SetupState.SETUP_FORMING, date, price=120, momentum=80)]
    analyses = [signal, excluded_breakout, excluded_watch, *controls]
    benchmark = _frame([100])
    db.save_signal_tracking(signal, frames["1000"], benchmark, cfg)
    update_controls(db, analyses, frames, benchmark, _meta(frames), cfg)
    before = db.load_control_members(signal_id=f"stable:{cfg['strategy_version']}")
    matched = {row["control_code"] for row in before if row["control_type"] == "MATCHED"}
    assert matched == {"1003", "1004"}
    assert "1000" not in {row["control_code"] for row in before}

    # Future gains and reordered candidates cannot retroactively change the frozen membership.
    reordered = [signal, controls[1], excluded_watch, controls[0], excluded_breakout]
    for item in reordered:
        item.metrics["momentum_percentile"] = 100 - item.metrics["momentum_percentile"]
    update_controls(db, reordered, frames, benchmark, _meta(frames), cfg)
    after = db.load_control_members(signal_id=f"stable:{cfg['strategy_version']}")
    assert [(row["control_group_id"], row["control_code"]) for row in before] == [
        (row["control_group_id"], row["control_code"]) for row in after]


def test_controls_track_horizons_excess_versions_and_export_links(tmp_path):
    cfg = load_config()
    cfg["summary"]["minimum_combination_sample"] = 1
    db = Database(tmp_path / "performance.db")
    signal_prices = [100 * (1 + .02 * offset) for offset in range(21)]
    control_prices = [100 * (1 + .01 * offset) for offset in range(21)]
    signal_full, control_full = _frame(signal_prices), _frame(control_prices)
    benchmark_full = _frame([100] * 21)
    date0 = str(signal_full.index[0].date())
    signal_id = f"stable:{cfg['strategy_version']}"
    signal0 = _analysis("1000", SetupState.BREAKOUT, date0, setup="stable")
    db.save_signal_tracking(signal0, signal_full.iloc[:1], benchmark_full.iloc[:1], cfg)
    members = []
    for control_type in ("RANDOM", "MATCHED"):
        members.append({
            "control_group_id": f"group-{control_type.lower()}",
            "signal_id": signal_id, "signal_date": date0,
            "control_code": "2000", "control_name": "Control",
            "control_type": control_type, "control_rank": 1,
            "match_score": 0.1 if control_type == "MATCHED" else None,
            "matched_at": date0, "initial_close": 100,
            "selection_version": cfg["controls"]["selection_version"],
            "app_version": cfg["logic_version"],
            "strategy_version": cfg["strategy_version"],
            "threshold_version": cfg["threshold_version"],
            "schema_version": cfg["schema_version"],
        })
    db.save_control_members(members)

    for offset in (0, 1, 5, 10, 20):
        date = str(signal_full.index[offset].date())
        signal = _analysis("1000", SetupState.BREAKOUT, date, setup="stable",
                           price=signal_prices[offset])
        control = _analysis("2000", SetupState.NOT_QUALIFIED, date,
                            price=control_prices[offset])
        db.save_signal_tracking(signal, signal_full.iloc[:offset + 1],
                                benchmark_full.iloc[:offset + 1], cfg)
        track_control_members(db, {"2000": control},
                              {"2000": control_full.iloc[:offset + 1]},
                              benchmark_full.iloc[:offset + 1], cfg)

    index = export_validation(db, tmp_path / "validation", cfg)
    performance = json.loads((tmp_path / "validation" / "performance.json").read_text())
    control_performance = json.loads(
        (tmp_path / "validation" / "control_performance.json").read_text())
    summary = json.loads((tmp_path / "validation" / "summary.json").read_text())
    row = performance[0]
    assert row["return_1d_pct"] == pytest.approx(2.0)
    assert row["random_return_mean_1d_pct"] == pytest.approx(1.0)
    assert row["excess_vs_random_1d_pct"] == pytest.approx(1.0)
    assert row["excess_vs_matched_5d_pct"] == pytest.approx(5.0)
    assert row["excess_vs_market_20d_pct"] == pytest.approx(40.0)
    assert {item["signal_id"] for item in control_performance} == {signal_id}
    assert {item["control_group_id"] for item in control_performance} == {
        "group-random", "group-matched"}
    assert all(item["strategy_version"] == cfg["strategy_version"]
               for item in control_performance)
    assert index["control_count"] == 2
    assert "summary.json" in index["available_files"]
    all_1d = [item for item in summary["rows"]
              if item["dimension"] == "ALL" and item["horizon_days"] == 1]
    assert all_1d[0]["average_excess_vs_matched_pct"] == pytest.approx(1.0)


def test_seed_import_keeps_existing_signal_and_control_membership_immutable(tmp_path):
    cfg = load_config()
    source = Database(tmp_path / "source.db")
    target = Database(tmp_path / "target.db")
    frame, benchmark = _frame([100]), _frame([100])
    date = str(frame.index[-1].date())
    signal = _analysis("1000", SetupState.BREAKOUT, date, setup="stable")
    source.save_signal_tracking(signal, frame, benchmark, cfg)
    signal_id = f"stable:{cfg['strategy_version']}"
    source.save_control_members([{
        "control_group_id": "fixed", "signal_id": signal_id, "signal_date": date,
        "control_code": "2000", "control_name": "Original", "control_type": "RANDOM",
        "control_rank": 1, "matched_at": date, "initial_close": 100,
        "selection_version": "control-v1", "app_version": cfg["logic_version"],
        "strategy_version": cfg["strategy_version"],
        "threshold_version": cfg["threshold_version"], "schema_version": cfg["schema_version"],
    }])
    signals, history = source.validation_rows()
    controls, control_history = source.control_validation_rows()
    target.import_validation_rows(signals, history, controls, control_history)
    changed = dict(controls[0], control_name="Changed")
    target.import_validation_rows(signals, history, [changed], control_history)
    imported_signals, _ = target.validation_rows()
    imported_controls, _ = target.control_validation_rows()
    assert imported_signals[0]["signal_id"] == signal_id
    assert imported_controls[0]["control_name"] == "Original"
