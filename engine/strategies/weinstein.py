from __future__ import annotations

import pandas as pd
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    x = df.iloc[-1]
    slope = (x.ma150 / df.ma150.iloc[-21] - 1) * 100 if len(df) >= 171 else float("nan")
    prior_high = float(df.high.tail(126).iloc[:-1].max())
    dist = (prior_high / x.close - 1) * 100
    conditions = [
        ok("price_ma", "株価が30週MA相当より上", x.close > x.ma150 if pd.notna(x.ma150) else None, Role.REQUIRED, Layer.MARKET_TREND, x.close, x.ma150, "円", Fidelity.PRACTICAL),
        tri("ma_slope", "30週MA相当が上向き", slope, lambda v: v > 1, lambda v: v > 0, Role.REQUIRED, Layer.MARKET_TREND, 0, "%/20日", Fidelity.PRACTICAL),
        ok("ma_order", "中期トレンド整列", x.ma50 > x.ma150 > x.ma200, Role.SUPPORTING, Layer.MARKET_TREND, f"{x.ma50:.0f}/{x.ma150:.0f}/{x.ma200:.0f}", None, "円", Fidelity.PROXY),
        tri("rs", "市場対比で優位", metrics.get("benchmark_rs_6m"), lambda v: v > 5, lambda v: v > 0, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, 0, "%", Fidelity.PRACTICAL),
        tri("stage2_breakout", "抵抗線付近／突破", dist, lambda v: -3 <= v <= 3, lambda v: 0 < v <= 8, Role.TRIGGER, Layer.ENTRY_SETUP, 3, "%", Fidelity.PROXY),
    ]
    required_ok = all(c.verdict.value in ("○", "△") for c in conditions[:2])
    if required_ok and x.close >= prior_high:
        state = SetupState.BREAKOUT
    elif required_ok and dist <= 3:
        state = SetupState.BREAKOUT_WATCH
    elif required_ok:
        state = SetupState.SETUP_FORMING
    else:
        state = SetupState.NOT_QUALIFIED
    return StrategyResult("Weinstein", state, conditions, prior_high, float(x.ma150), "Stage 2 quantitative approximation")

