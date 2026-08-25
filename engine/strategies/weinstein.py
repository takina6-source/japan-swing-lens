from __future__ import annotations

import pandas as pd
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult, Verdict
from ..pivots import pivot_state, strategy_pivot
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    x = df.iloc[-1]
    slope = (x.ma150 / df.ma150.iloc[-21] - 1) * 100 if len(df) >= 171 else float("nan")
    pivot = strategy_pivot("Weinstein", df, cfg,
                           metrics.get("pivot_registry", {}).get("Weinstein"))
    dist = (pivot.price / x.close - 1) * 100 if pivot else float("nan")
    conditions = [
        ok("price_ma", "株価が30週MA相当より上", x.close > x.ma150 if pd.notna(x.ma150) else None, Role.REQUIRED, Layer.MARKET_TREND, x.close, x.ma150, "円", Fidelity.PRACTICAL),
        tri("ma_slope", "30週MA相当が上向き", slope, lambda v: v > 1, lambda v: v > 0, Role.REQUIRED, Layer.MARKET_TREND, 0, "%/20日", Fidelity.PRACTICAL),
        ok("ma_order", "中期トレンド整列", x.ma50 > x.ma150 > x.ma200, Role.SUPPORTING, Layer.MARKET_TREND, f"{x.ma50:.0f}/{x.ma150:.0f}/{x.ma200:.0f}", None, "円", Fidelity.PROXY),
        tri("rs", "市場対比で優位", metrics.get("benchmark_rs_6m"), lambda v: v > 5, lambda v: v > 0, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, 0, "%", Fidelity.PRACTICAL),
        tri("stage2_breakout", "Stage 1抵抗線付近／突破", dist, lambda v: -3 <= v <= 3, lambda v: 0 < v <= 8, Role.TRIGGER, Layer.ENTRY_SETUP, 3, "%", pivot.fidelity if pivot else Fidelity.PROXY),
    ]
    required_ok = all(c_.verdict != Verdict.FAIL for c_ in conditions if c_.role == Role.REQUIRED)
    event = pivot_state(df, pivot, cfg, 3, 1.0, required_ok, required_ok)
    return StrategyResult("Weinstein", event.state, conditions, pivot.price if pivot else None,
                          float(x.ma150), "Stage 1 Base → Stage 2 structure",
                          pivot_type=pivot.pivot_type if pivot else "N/A",
                          pivot_basis=pivot.basis if pivot else "N/A",
                          pivot_fidelity=pivot.fidelity if pivot else Fidelity.PROXY,
                          pivot_formed_date=pivot.formed_date if pivot else None,
                          setup_start_date=pivot.setup_start_date if pivot else None,
                          setup_id=pivot.setup_id if pivot else None,
                          setup_age=pivot.setup_age if pivot else None,
                          distance_to_pivot_pct=event.distance_pct,
                          breakout_date=event.breakout_date, breakout_age=event.breakout_age)
