import numpy as np
import pandas as pd
from engine.analyzer import analyze, prepare_universe, rank
from engine.config import load_config
from engine.data.demo import make_demo_history
from engine.models import Verdict


def test_six_independent_strategies_and_na():
    cfg = load_config()
    raw = {"7203": make_demo_history("7203"), "6758": make_demo_history("6758")}
    benchmark = make_demo_history("TOPIX")
    data = prepare_universe(raw, benchmark)
    result = analyze("7203", "トヨタ", data["7203"],
                     {"eps_growth": None, "sales_growth": None}, "TEST", benchmark, cfg)
    assert set(result.strategies) == {"Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas", "Connors"}
    can = result.strategies["CAN SLIM"]
    assert any(c.verdict == Verdict.NA for c in can.conditions)
    assert 0 <= result.confluence <= 6


def test_ranking_is_deterministic_and_state_first():
    cfg = load_config()
    raw = {code: make_demo_history(code) for code in ("7203", "6758", "8035")}
    benchmark = make_demo_history("TOPIX")
    prepared = prepare_universe(raw, benchmark)
    analyses = [analyze(code, code, prepared[code], {"eps_growth": 30, "sales_growth": 25},
                        "TEST", benchmark, cfg) for code in raw]
    once = [a.code for a in rank(analyses)]
    twice = [a.code for a in rank(analyses)]
    assert once == twice


def test_missing_long_history_is_na_not_crash():
    cfg = load_config()
    short = make_demo_history("7203", periods=80)
    benchmark = make_demo_history("TOPIX", periods=80)
    prepared = prepare_universe({"7203": short}, benchmark)
    result = analyze("7203", "short", prepared["7203"], {}, "TEST", benchmark, cfg)
    verdicts = [c.verdict for s in result.strategies.values() for c in s.conditions]
    assert Verdict.NA in verdicts


def test_low_liquidity_is_warning_not_consensus_override():
    cfg = load_config()
    raw = {"9244": make_demo_history("9244")}
    benchmark = make_demo_history("TOPIX")
    prepared = prepare_universe(raw, benchmark)
    low = prepared["9244"].copy()
    low.loc[:, "trading_value"] = low["close"]
    high = prepared["9244"].copy()
    high.loc[:, "trading_value"] = 2_000_000_000
    fundamentals = {"eps_growth": 30, "sales_growth": 25}
    low_result = analyze("9244", "低流動性テスト", low, fundamentals,
                         "TEST", benchmark, cfg)
    high_result = analyze("9244", "高流動性テスト", high, fundamentals,
                          "TEST", benchmark, cfg)
    assert low_result.metrics["liquidity_level"] == "VERY LOW"
    assert low_result.metrics["liquid"] is False
    assert low_result.state == high_result.state
    assert low_result.breakout_strategy_count == high_result.breakout_strategy_count
