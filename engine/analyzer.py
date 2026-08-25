from __future__ import annotations

import numpy as np
import pandas as pd
from hashlib import sha1
from .indicators import enrich, relative_strength
from .models import Fidelity, SetupState, StockAnalysis, Verdict
from .strategies import STRATEGIES
from .trade_plan import build_trade_plan

STATE_ORDER = {SetupState.BREAKOUT: 0, SetupState.BREAKOUT_WATCH: 1,
               SetupState.PULLBACK: 2, SetupState.SETUP_FORMING: 3,
               SetupState.EXTENDED: 4, SetupState.FAILED: 5,
               SetupState.NOT_QUALIFIED: 6}


def prepare_universe(raw: dict[str, pd.DataFrame], benchmark: pd.DataFrame) -> dict[str, pd.DataFrame]:
    enriched = {code: enrich(df) for code, df in raw.items()}
    mom = pd.Series({code: df.iloc[-1].momentum_6m for code, df in enriched.items()})
    percentiles = mom.rank(pct=True) * 100
    b = benchmark["close"]
    for code, df in enriched.items():
        df.attrs["momentum_percentile"] = float(percentiles.get(code, np.nan))
        rs = relative_strength(df.close, b, 126)
        df.attrs["benchmark_rs_6m"] = float(rs.iloc[-1]) if len(rs) else np.nan
    return enriched


def market_regime(benchmark: pd.DataFrame) -> tuple[str, bool]:
    b = enrich(benchmark).iloc[-1]
    if pd.isna(b.ma200): return "NEUTRAL", False
    if b.close > b.ma50 > b.ma200: return "STRONG BULL", True
    if b.close > b.ma200: return "BULL", True
    if b.close > b.ma50: return "NEUTRAL", False
    if b.close < b.ma200: return "BEAR", False
    return "WEAK", False


TREND_STRATEGIES = ("Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas")


def analyze(code: str, name: str, df: pd.DataFrame, fundamentals: dict,
            source: str, benchmark: pd.DataFrame, cfg: dict,
            setup_registry: dict[str, dict] | None = None) -> StockAnalysis:
    x = df.iloc[-1]
    regime, bullish = market_regime(benchmark)
    metrics = {**fundamentals, "code": code,
        "momentum_percentile": df.attrs.get("momentum_percentile"),
        "benchmark_rs_6m": df.attrs.get("benchmark_rs_6m"),
        "trading_value_20d": float(df.trading_value.tail(20).mean()),
        "liquid": float(df.trading_value.tail(20).mean()) >= cfg["liquidity"]["minimum_trading_value_yen"],
        "market_regime": regime, "market_bullish": bullish,
        "price": float(x.close), "momentum_1m": float(x.momentum_1m),
        "momentum_3m": float(x.momentum_3m), "momentum_6m": float(x.momentum_6m),
        "momentum_12m": float(x.momentum_12m), "rsi2": float(x.rsi2),
        "benchmark_price": float(benchmark.close.iloc[-1]),
        "pivot_registry": setup_registry or {},
    }
    strategies = {name_: fn(df, metrics, cfg) for name_, fn in STRATEGIES.items()}
    trend = [strategies[n] for n in TREND_STRATEGIES]
    breakout_count = sum(r.state == SetupState.BREAKOUT for r in trend)
    watch_count = sum(r.state == SetupState.BREAKOUT_WATCH for r in trend)
    aligned_count = sum(r.state in (SetupState.BREAKOUT, SetupState.BREAKOUT_WATCH,
                                    SetupState.SETUP_FORMING) for r in trend)
    confluence = sum(r.positive for r in trend)
    state = consensus_state(strategies, breakout_count, watch_count, confluence, cfg)
    evaluated = [c for r in strategies.values() for c in r.conditions if c.verdict != Verdict.NA]
    total = sum(len(r.conditions) for r in strategies.values())
    coverage = len(evaluated) / total * 100 if total else 0.0
    confidence = confidence_label(evaluated, coverage)
    pivots = [r for r in trend if r.pivot is not None]
    pivot_fidelity = aggregate_pivot_fidelity(pivots)
    setup_id = consensus_setup_id(code, trend)
    trade_plan = build_trade_plan(state, float(x.close), strategies, df, cfg)
    return StockAnalysis(code=code, name=name, as_of=str(df.index[-1].date()), source=source,
                         metrics=metrics, strategies=strategies, state=state,
                         confluence=confluence, breakout_strategy_count=breakout_count,
                         aligned_strategy_count=aligned_count, coverage=coverage,
                         confidence=confidence, pivot_fidelity=pivot_fidelity,
                         setup_id=setup_id, trade_plan=trade_plan)


def rank(analyses: list[StockAnalysis]) -> list[StockAnalysis]:
    for a in analyses:
        a.rank_key = (STATE_ORDER[a.state], -a.breakout_strategy_count, -a.confluence,
                      -a.coverage, -confidence_order(a.confidence),
                      -{Fidelity.STRICT: 2, Fidelity.PRACTICAL: 1, Fidelity.PROXY: 0}[a.pivot_fidelity],
                      -_num(a.metrics.get("momentum_percentile")),
                      -_num(a.metrics.get("trading_value_20d")))
    return sorted(analyses, key=lambda a: a.rank_key)


def _num(v):
    return -1e30 if v is None or pd.isna(v) else float(v)


def consensus_state(strategies: dict, breakout_count: int, watch_count: int,
                    confluence: int, cfg: dict) -> SetupState:
    trend = [strategies[n] for n in TREND_STRATEGIES]
    minimum = int(cfg["consensus"]["breakout_min_strategies"])
    if breakout_count >= minimum:
        return SetupState.BREAKOUT
    if (breakout_count == 1 and watch_count >= 1) or (
            watch_count >= int(cfg["consensus"]["watch_min_strategies"])
            and confluence >= int(cfg["consensus"]["watch_min_confluence"])):
        return SetupState.BREAKOUT_WATCH
    if sum(r.state == SetupState.FAILED for r in trend) >= minimum:
        return SetupState.FAILED
    if sum(r.state == SetupState.EXTENDED for r in trend) >= minimum:
        return SetupState.EXTENDED
    if strategies["Connors"].state == SetupState.PULLBACK:
        return SetupState.PULLBACK
    if any(r.state in (SetupState.SETUP_FORMING, SetupState.BREAKOUT_WATCH,
                       SetupState.BREAKOUT) for r in trend):
        return SetupState.SETUP_FORMING
    return SetupState.NOT_QUALIFIED


def confidence_label(conditions: list, coverage: float) -> str:
    if not conditions or coverage < 45:
        return "LOW"
    weights = {Fidelity.STRICT: 1.0, Fidelity.PRACTICAL: .8, Fidelity.PROXY: .5}
    quality = sum(weights[c.fidelity] for c in conditions) / len(conditions) * 100
    combined = quality * coverage / 100
    if combined >= 72:
        return "HIGH"
    if combined >= 52:
        return "MEDIUM"
    return "LOW"


def confidence_order(value: str) -> int:
    return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}.get(value, 0)


def aggregate_pivot_fidelity(results: list) -> Fidelity:
    values = [r.pivot_fidelity for r in results]
    if any(v == Fidelity.STRICT for v in values):
        return Fidelity.STRICT
    if sum(v == Fidelity.PRACTICAL for v in values) >= 2:
        return Fidelity.PRACTICAL
    return Fidelity.PROXY


def consensus_setup_id(code: str, results: list) -> str | None:
    active = sorted({r.setup_id for r in results if r.setup_id})
    if not active:
        return None
    raw = f"{code}|" + "|".join(active)
    return f"{code}-{sha1(raw.encode()).hexdigest()[:16]}"
