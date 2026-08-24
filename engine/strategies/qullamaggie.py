from __future__ import annotations

import pandas as pd
from ..indicators import contraction_widths
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    c = cfg["qullamaggie"]
    x = df.iloc[-1]
    pivot = float(df.high.tail(21).iloc[:-1].max())
    dist = (pivot / x.close - 1) * 100
    widths = contraction_widths(df)
    contraction = bool(widths and widths[0] > widths[1] > widths[2])
    conditions = [
        tri("momentum3", "3か月で大きく上昇", x.momentum_3m, lambda v: v >= c["momentum_3m_min_pct"], lambda v: v >= 20, Role.REQUIRED, Layer.QUALITY_MOMENTUM, c["momentum_3m_min_pct"], "%", Fidelity.PRACTICAL),
        tri("momentum6", "6か月Momentum", x.momentum_6m, lambda v: v >= c["momentum_6m_min_pct"], lambda v: v >= 30, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, c["momentum_6m_min_pct"], "%", Fidelity.PRACTICAL),
        ok("trend", "株価 > 50日MA > 150日MA", x.close > x.ma50 > x.ma150, Role.REQUIRED, Layer.MARKET_TREND, f"{x.close:.0f} > {x.ma50:.0f} > {x.ma150:.0f}", None, "円", Fidelity.PRACTICAL),
        ok("contraction", "上昇後のレンジ収縮", contraction if widths else None, Role.REQUIRED, Layer.ENTRY_SETUP, " → ".join(map(str, widths)) if widths else None, "縮小", "%", Fidelity.PROXY),
        tri("dryup", "出来高Dry-up", x.volume_ratio, lambda v: v <= .65, lambda v: v <= .9, Role.SUPPORTING, Layer.ENTRY_SETUP, .65, "倍", Fidelity.PROXY),
        tri("pivot_distance", "Pivot付近", dist, lambda v: -2 <= v <= c["pivot_watch_pct"], lambda v: 0 < v <= 7, Role.TRIGGER, Layer.ENTRY_SETUP, c["pivot_watch_pct"], "%", Fidelity.PROXY),
        ok("volume_expansion", "ブレイク時の出来高増加", x.close >= pivot and x.volume_ratio >= c["breakout_volume_ratio"], Role.TRIGGER, Layer.ENTRY_SETUP, x.volume_ratio, c["breakout_volume_ratio"], "倍", Fidelity.PRACTICAL),
    ]
    if x.close >= pivot and x.volume_ratio >= c["breakout_volume_ratio"]:
        state = SetupState.BREAKOUT
    elif 0 <= dist <= c["pivot_watch_pct"]:
        state = SetupState.BREAKOUT_WATCH
    elif contraction:
        state = SetupState.SETUP_FORMING
    else:
        state = SetupState.NOT_QUALIFIED
    return StrategyResult("Qullamaggie", state, conditions, pivot, float(df.low.tail(10).min()), "Breakout setup proxy")

