from __future__ import annotations

import pandas as pd
from ..indicators import contraction_widths
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult, Verdict
from ..pivots import pivot_state, strategy_pivot
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    c = cfg["qullamaggie"]
    x = df.iloc[-1]
    pivot = strategy_pivot("Qullamaggie", df, cfg,
                           metrics.get("pivot_registry", {}).get("Qullamaggie"))
    dist = (pivot.price / x.close - 1) * 100 if pivot else float("nan")
    widths = contraction_widths(df)
    contraction = bool(widths and widths[0] > widths[1] > widths[2])
    conditions = [
        tri("momentum3", "3か月で大きく上昇", x.momentum_3m, lambda v: v >= c["momentum_3m_min_pct"], lambda v: v >= 20, Role.REQUIRED, Layer.QUALITY_MOMENTUM, c["momentum_3m_min_pct"], "%", Fidelity.PRACTICAL),
        tri("momentum6", "6か月Momentum", x.momentum_6m, lambda v: v >= c["momentum_6m_min_pct"], lambda v: v >= 30, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, c["momentum_6m_min_pct"], "%", Fidelity.PRACTICAL),
        ok("trend", "株価 > 50日MA > 150日MA", x.close > x.ma50 > x.ma150, Role.REQUIRED, Layer.MARKET_TREND, f"{x.close:.0f} > {x.ma50:.0f} > {x.ma150:.0f}", None, "円", Fidelity.PRACTICAL),
        ok("contraction", "上昇後のレンジ収縮", contraction if widths else None, Role.REQUIRED, Layer.ENTRY_SETUP, " → ".join(map(str, widths)) if widths else None, "縮小", "%", Fidelity.PROXY),
        tri("dryup", "出来高Dry-up", x.volume_ratio, lambda v: v <= .65, lambda v: v <= .9, Role.SUPPORTING, Layer.ENTRY_SETUP, .65, "倍", Fidelity.PROXY),
        tri("pivot_distance", "Consolidation Pivot付近", dist, lambda v: -2 <= v <= c["pivot_watch_pct"], lambda v: 0 < v <= 7, Role.TRIGGER, Layer.ENTRY_SETUP, c["pivot_watch_pct"], "%", pivot.fidelity if pivot else Fidelity.PROXY),
        ok("volume_expansion", "突破イベント時の出来高増加", (df.close.iloc[-2] <= pivot.price < x.close and x.volume_ratio >= c["breakout_volume_ratio"]) if pivot else None, Role.TRIGGER, Layer.ENTRY_SETUP, x.volume_ratio, c["breakout_volume_ratio"], "倍", Fidelity.PRACTICAL),
    ]
    required_ok = all(c_.verdict != Verdict.FAIL for c_ in conditions if c_.role == Role.REQUIRED)
    event = pivot_state(df, pivot, cfg, c["pivot_watch_pct"], c["breakout_volume_ratio"],
                        required_ok, contraction)
    return StrategyResult("Qullamaggie", event.state, conditions, pivot.price if pivot else None,
                          float(df.low.tail(10).min()), "Prior move + consolidation structure",
                          pivot_type=pivot.pivot_type if pivot else "N/A",
                          pivot_basis=pivot.basis if pivot else "N/A",
                          pivot_fidelity=pivot.fidelity if pivot else Fidelity.PROXY,
                          pivot_formed_date=pivot.formed_date if pivot else None,
                          setup_start_date=pivot.setup_start_date if pivot else None,
                          setup_id=pivot.setup_id if pivot else None,
                          setup_age=pivot.setup_age if pivot else None,
                          distance_to_pivot_pct=event.distance_pct,
                          breakout_date=event.breakout_date, breakout_age=event.breakout_age)
