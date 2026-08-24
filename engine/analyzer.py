from __future__ import annotations

import numpy as np
import pandas as pd
from .indicators import enrich, relative_strength
from .models import SetupState, StockAnalysis
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


def analyze(code: str, name: str, df: pd.DataFrame, fundamentals: dict,
            source: str, benchmark: pd.DataFrame, cfg: dict) -> StockAnalysis:
    x = df.iloc[-1]
    regime, bullish = market_regime(benchmark)
    metrics = {**fundamentals,
        "momentum_percentile": df.attrs.get("momentum_percentile"),
        "benchmark_rs_6m": df.attrs.get("benchmark_rs_6m"),
        "trading_value_20d": float(df.trading_value.tail(20).mean()),
        "liquid": float(df.trading_value.tail(20).mean()) >= cfg["liquidity"]["minimum_trading_value_yen"],
        "market_regime": regime, "market_bullish": bullish,
        "price": float(x.close), "momentum_1m": float(x.momentum_1m),
        "momentum_3m": float(x.momentum_3m), "momentum_6m": float(x.momentum_6m),
        "momentum_12m": float(x.momentum_12m), "rsi2": float(x.rsi2),
    }
    strategies = {name_: fn(df, metrics, cfg) for name_, fn in STRATEGIES.items()}
    states = [r.state for r in strategies.values()]
    state = min(states, key=lambda s: STATE_ORDER[s])
    # Connorsは逆張りPullbackなので、breakout系との不一致を減点せず独立に数える。
    confluence = sum(r.positive for n, r in strategies.items() if n != "Connors")
    if strategies["Connors"].state == SetupState.PULLBACK:
        confluence += 1
    trade_plan = build_trade_plan(state, float(x.close), strategies, df, cfg)
    return StockAnalysis(code, name, str(df.index[-1].date()), source, metrics,
                         strategies, state, confluence, trade_plan)


def rank(analyses: list[StockAnalysis]) -> list[StockAnalysis]:
    for a in analyses:
        a.rank_key = (STATE_ORDER[a.state], -a.confluence,
                      -_num(a.metrics.get("momentum_percentile")),
                      -_num(a.metrics.get("trading_value_20d")))
    return sorted(analyses, key=lambda a: a.rank_key)


def _num(v):
    return -1e30 if v is None or pd.isna(v) else float(v)
