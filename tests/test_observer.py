import pandas as pd

from engine.analyzer import consensus_state
from engine.config import load_config
from engine.data.demo import make_demo_history
from engine.database import Database
from engine.fundamentals import annual_earnings_condition
from engine.indicators import enrich
from engine.models import (ConditionResult, Fidelity, Layer, Role, SetupState,
                           StockAnalysis, StrategyResult, TradePlan, Verdict)
from engine.pivots import PivotSpec, pivot_state, strategy_pivot
from engine.validation import export_validation


def test_canslim_annual_eps_growth_missing_loss_and_anomaly():
    cfg = load_config()
    passing = annual_earnings_condition({"annual_eps_source": "金融庁 EDINET API v2", "annual_eps": [
        {"fiscal_year": "2022", "eps": 100}, {"fiscal_year": "2023", "eps": 130},
        {"fiscal_year": "2024", "eps": 170}, {"fiscal_year": "2025", "eps": 225}]}, cfg)
    assert passing.verdict == Verdict.PASS
    assert passing.fidelity == Fidelity.STRICT
    assert annual_earnings_condition({"annual_eps": []}, cfg).verdict == Verdict.NA
    loss = annual_earnings_condition({"annual_eps": [
        {"fiscal_year": "2023", "eps": -10}, {"fiscal_year": "2024", "eps": -2},
        {"fiscal_year": "2025", "eps": -1}]}, cfg)
    assert loss.verdict == Verdict.FAIL
    turnaround = annual_earnings_condition({"annual_eps": [
        {"fiscal_year": "2023", "eps": -10}, {"fiscal_year": "2024", "eps": 2},
        {"fiscal_year": "2025", "eps": 20}]}, cfg)
    assert turnaround.verdict == Verdict.BORDERLINE
    anomaly = annual_earnings_condition({"annual_eps": [
        {"fiscal_year": "2022", "eps": 1}, {"fiscal_year": "2023", "eps": 10},
        {"fiscal_year": "2024", "eps": 14}, {"fiscal_year": "2025", "eps": 20}]}, cfg)
    assert anomaly.verdict == Verdict.BORDERLINE


def test_pivot_uses_t_minus_one_and_can_be_frozen():
    cfg = load_config()
    frame = enrich(make_demo_history("9251", 300))
    first = strategy_pivot("Minervini", frame, cfg)
    changed = frame.copy()
    changed.loc[changed.index[-1], "high"] = first.price * 10
    second = strategy_pivot("Minervini", changed, cfg)
    assert second.price == first.price
    previous = {"pivot_price": first.price, "pivot_type": first.pivot_type,
                "pivot_basis": first.basis, "pivot_fidelity": first.fidelity.value,
                "pivot_formed_date": first.formed_date,
                "setup_start_date": first.setup_start_date, "setup_id": first.setup_id}
    frozen = strategy_pivot("Minervini", changed, cfg, previous)
    assert frozen.setup_id == first.setup_id
    assert frozen.price == first.price


def test_breakout_is_crossing_event_then_extended_or_failed():
    cfg = load_config()
    frame = enrich(make_demo_history("7203", 260))
    pivot = float(frame.close.iloc[-2]) * 1.01
    frame.loc[frame.index[-2], "close"] = pivot * .99
    frame.loc[frame.index[-1], "close"] = pivot * 1.01
    frame.loc[frame.index[-1], "volume_ratio"] = 2.0
    spec = PivotSpec(pivot, "Test Base", "Structure", Fidelity.PRACTICAL,
                     str(frame.index[-20].date()), str(frame.index[-2].date()), "setup-test", 20)
    assert pivot_state(frame, spec, cfg, 3, 1.2).state == SetupState.BREAKOUT
    extended = frame.copy()
    extended.loc[extended.index[-1], "close"] = pivot * 1.2
    assert pivot_state(extended, spec, cfg, 3, 1.2).state == SetupState.EXTENDED
    failed = frame.copy()
    extra = failed.iloc[-1:].copy()
    extra.index = [failed.index[-1] + pd.offsets.BDay(1)]
    extra.loc[:, "close"] = pivot * .95
    failed = pd.concat([failed, extra])
    assert pivot_state(failed, spec, cfg, 3, 1.2).state == SetupState.FAILED


def _strategy(name, state):
    conditions = [
        ConditionResult("required", "required", Verdict.PASS, Role.REQUIRED, Layer.ENTRY_SETUP),
        ConditionResult("trigger", "trigger", Verdict.PASS, Role.TRIGGER, Layer.ENTRY_SETUP),
    ]
    return StrategyResult(name, state, conditions, 100.0, 93.0,
                          pivot_type="Test Base", pivot_basis="Structure",
                          pivot_fidelity=Fidelity.PRACTICAL, pivot_formed_date="2026-01-01",
                          setup_start_date="2025-12-01", setup_id=f"{name}-setup")


def test_consensus_requires_two_breakout_strategies_and_excludes_connors():
    cfg = load_config()
    names = ("Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas")
    strategies = {name: _strategy(name, SetupState.NOT_QUALIFIED) for name in names}
    strategies["Connors"] = _strategy("Connors", SetupState.BREAKOUT)
    assert consensus_state(strategies, 0, 0, 0, cfg) == SetupState.NOT_QUALIFIED
    strategies["Minervini"] = _strategy("Minervini", SetupState.BREAKOUT)
    assert consensus_state(strategies, 1, 0, 1, cfg) != SetupState.BREAKOUT
    strategies["Darvas"] = _strategy("Darvas", SetupState.BREAKOUT)
    assert consensus_state(strategies, 2, 0, 2, cfg) == SetupState.BREAKOUT


def test_snapshot_is_immutable_and_export_links_history(tmp_path):
    cfg = load_config()
    db = Database(tmp_path / "observer.db")
    frame = enrich(make_demo_history("9251", 260))
    benchmark = enrich(make_demo_history("TOPIX", 260))
    strategies = {name: _strategy(name, SetupState.BREAKOUT)
                  for name in ("Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas")}
    strategies["Connors"] = _strategy("Connors", SetupState.NOT_QUALIFIED)
    plan = TradePlan("候補", 100, 102, 93, 107, 114, 120, 7, 2, "test")
    analysis = StockAnalysis("9251", "ＡＢ＆Ｃｏｍｐａｎｙ", str(frame.index[-1].date()),
                             "TEST", {"momentum_percentile": 90, "market_regime": "BULL",
                                      "trading_value_20d": 1e9,
                                      "benchmark_price": float(benchmark.close.iloc[-1])},
                             strategies, SetupState.BREAKOUT, 5, 5, 5, 100, "HIGH",
                             Fidelity.PRACTICAL, "9251-consensus", plan)
    db.save_signal_tracking(analysis, frame, benchmark, cfg)
    analysis.confluence = 1
    db.save_signal_tracking(analysis, frame, benchmark, cfg)
    signals, history = db.validation_rows()
    assert len(signals) == 1
    assert signals[0]["confluence"] == 5
    assert history[0]["signal_id"] == signals[0]["signal_id"]
    index = export_validation(db, tmp_path / "validation", cfg)
    assert index["signal_count"] == 1
    assert (tmp_path / "validation" / "signals.csv").exists()
    assert (tmp_path / "validation" / "performance.json").exists()


def test_signal_transition_is_saved_at_0_5_10_20_sessions(tmp_path):
    cfg = load_config()
    db = Database(tmp_path / "transitions.db")
    full = enrich(make_demo_history("9251", 300))
    bench = enrich(make_demo_history("TOPIX", 300))
    start_pos = len(full) - 21
    sequence = [(0, SetupState.BREAKOUT_WATCH, 0, 2),
                (5, SetupState.BREAKOUT, 3, 3),
                (10, SetupState.BREAKOUT, 4, 4),
                (20, SetupState.EXTENDED, 1, 3)]
    for offset, state, breakout_count, aligned in sequence:
        frame = full.iloc[:start_pos + offset + 1]
        benchmark = bench.loc[:frame.index[-1]]
        method_state = SetupState.BREAKOUT if breakout_count else SetupState.BREAKOUT_WATCH
        strategies = {name: _strategy(name, method_state if i < max(breakout_count, 1)
                                       else SetupState.SETUP_FORMING)
                      for i, name in enumerate(("Minervini", "Qullamaggie", "CAN SLIM",
                                                "Weinstein", "Darvas"))}
        strategies["Connors"] = _strategy("Connors", SetupState.NOT_QUALIFIED)
        analysis = StockAnalysis(
            "9251", "ＡＢ＆Ｃｏｍｐａｎｙ", str(frame.index[-1].date()), "TEST",
            {"momentum_percentile": 90, "market_regime": "BULL",
             "trading_value_20d": 1e9, "benchmark_price": float(benchmark.close.iloc[-1])},
            strategies, state, aligned, breakout_count, aligned, 95, "HIGH",
            Fidelity.PRACTICAL, "9251-stable-consensus",
            TradePlan("候補", 100, 102, 93, 107, 114, 120, 7, 2, "test"))
        db.save_signal_tracking(analysis, frame, benchmark, cfg)
    signals, history = db.validation_rows()
    assert len(signals) == 1
    by_offset = {row["session_offset"]: row for row in history}
    assert set(by_offset) == {0, 5, 10, 20}
    assert by_offset[0]["consensus_state"] == SetupState.BREAKOUT_WATCH.value
    assert by_offset[5]["breakout_count"] == 3
    assert by_offset[10]["breakout_count"] == 4
    assert by_offset[20]["consensus_state"] == SetupState.EXTENDED.value
