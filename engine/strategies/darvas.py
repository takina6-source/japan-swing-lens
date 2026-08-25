from __future__ import annotations

import pandas as pd
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult, Verdict
from ..pivots import pivot_state, strategy_pivot
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    c = cfg["darvas"]
    x = df.iloc[-1]
    box = df.tail(c["box_days"] + 1).iloc[:-1]
    pivot = strategy_pivot("Darvas", df, cfg,
                           metrics.get("pivot_registry", {}).get("Darvas"))
    top, bottom = (pivot.price if pivot else float(box.high.max())), float(box.low.min())
    width = (top - bottom) / bottom * 100
    dist = (top / x.close - 1) * 100
    high_zone = x.close >= x.high52 * .8 if pd.notna(x.high52) else None
    conditions = [
        ok("high_zone", "52週高値圏", high_zone, Role.REQUIRED, Layer.QUALITY_MOMENTUM, x.close, x.high52, "円", Fidelity.PRACTICAL),
        tri("box_width", "Boxの値幅が限定", width, lambda v: v <= c["box_max_width_pct"], lambda v: v <= 20, Role.REQUIRED, Layer.ENTRY_SETUP, c["box_max_width_pct"], "%", pivot.fidelity if pivot else Fidelity.PROXY),
        ok("floor", "Box下限が維持", x.close > bottom, Role.REQUIRED, Layer.ENTRY_SETUP, x.close, bottom, "円", pivot.fidelity if pivot else Fidelity.PROXY),
        tri("top", "Box Top付近／突破", dist, lambda v: -2 <= v <= c["pivot_watch_pct"], lambda v: 0 < v <= 7, Role.TRIGGER, Layer.ENTRY_SETUP, c["pivot_watch_pct"], "%", pivot.fidelity if pivot else Fidelity.PROXY),
        ok("volume", "突破イベント時の出来高増加", (df.close.iloc[-2] <= top < x.close and x.volume_ratio >= 1.3) if pivot else None, Role.SUPPORTING, Layer.ENTRY_SETUP, x.volume_ratio, 1.3, "倍", Fidelity.PRACTICAL),
    ]
    required_ok = all(c_.verdict != Verdict.FAIL for c_ in conditions if c_.role == Role.REQUIRED)
    event = pivot_state(df, pivot, cfg, c["pivot_watch_pct"], 1.3, required_ok, width <= 20)
    return StrategyResult("Darvas", event.state, conditions, top, bottom, "Darvas Box structure",
                          pivot_type=pivot.pivot_type if pivot else "N/A",
                          pivot_basis=pivot.basis if pivot else "N/A",
                          pivot_fidelity=pivot.fidelity if pivot else Fidelity.PROXY,
                          pivot_formed_date=pivot.formed_date if pivot else None,
                          setup_start_date=pivot.setup_start_date if pivot else None,
                          setup_id=pivot.setup_id if pivot else None,
                          setup_age=pivot.setup_age if pivot else None,
                          distance_to_pivot_pct=event.distance_pct,
                          breakout_date=event.breakout_date, breakout_age=event.breakout_age)
