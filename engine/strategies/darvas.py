from __future__ import annotations

import pandas as pd
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    c = cfg["darvas"]
    x = df.iloc[-1]
    box = df.tail(c["box_days"] + 1).iloc[:-1]
    top, bottom = float(box.high.max()), float(box.low.min())
    width = (top - bottom) / bottom * 100
    dist = (top / x.close - 1) * 100
    high_zone = x.close >= x.high52 * .8 if pd.notna(x.high52) else None
    conditions = [
        ok("high_zone", "52週高値圏", high_zone, Role.REQUIRED, Layer.QUALITY_MOMENTUM, x.close, x.high52, "円", Fidelity.PRACTICAL),
        tri("box_width", "Boxの値幅が限定", width, lambda v: v <= c["box_max_width_pct"], lambda v: v <= 20, Role.REQUIRED, Layer.ENTRY_SETUP, c["box_max_width_pct"], "%", Fidelity.PROXY),
        ok("floor", "Box下限が維持", x.close > bottom, Role.REQUIRED, Layer.ENTRY_SETUP, x.close, bottom, "円", Fidelity.PROXY),
        tri("top", "Box上限付近／突破", dist, lambda v: -2 <= v <= c["pivot_watch_pct"], lambda v: 0 < v <= 7, Role.TRIGGER, Layer.ENTRY_SETUP, c["pivot_watch_pct"], "%", Fidelity.PROXY),
        ok("volume", "突破時の出来高増加", x.close >= top and x.volume_ratio >= 1.3, Role.SUPPORTING, Layer.ENTRY_SETUP, x.volume_ratio, 1.3, "倍", Fidelity.PRACTICAL),
    ]
    state = SetupState.BREAKOUT if x.close >= top and x.volume_ratio >= 1.3 else (SetupState.BREAKOUT_WATCH if 0 <= dist <= c["pivot_watch_pct"] else SetupState.SETUP_FORMING if width <= 20 else SetupState.NOT_QUALIFIED)
    return StrategyResult("Darvas", state, conditions, top, bottom, "20-day Darvas box proxy")

