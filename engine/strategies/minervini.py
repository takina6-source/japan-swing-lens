from __future__ import annotations

import pandas as pd
from ..indicators import contraction_widths
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult, Verdict
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    c = cfg["minervini"]
    x = df.iloc[-1]
    ma200_slope = (x.ma200 / df.ma200.iloc[-21] - 1) * 100 if len(df) >= 221 else float("nan")
    above_low = (x.close / x.low52 - 1) * 100
    below_high = (x.high52 - x.close) / x.high52 * 100
    widths = contraction_widths(df)
    contracting = len(widths) == 3 and widths[0] > widths[1] > widths[2]
    pivot = float(df.high.tail(20).iloc[:-1].max())
    distance = (pivot / x.close - 1) * 100
    conditions = [
        ok("price_ma50", "株価 > 50日移動平均", x.close > x.ma50, Role.REQUIRED, Layer.MARKET_TREND, x.close, x.ma50, "円", Fidelity.STRICT),
        ok("price_ma150", "株価 > 150日移動平均", x.close > x.ma150, Role.REQUIRED, Layer.MARKET_TREND, x.close, x.ma150, "円", Fidelity.STRICT),
        ok("price_ma200", "株価 > 200日移動平均", x.close > x.ma200, Role.REQUIRED, Layer.MARKET_TREND, x.close, x.ma200, "円", Fidelity.STRICT),
        ok("ma50_ma150", "50日MA > 150日MA", x.ma50 > x.ma150, Role.REQUIRED, Layer.MARKET_TREND, x.ma50, x.ma150, "円", Fidelity.STRICT),
        ok("ma150_ma200", "150日MA > 200日MA", x.ma150 > x.ma200, Role.REQUIRED, Layer.MARKET_TREND, x.ma150, x.ma200, "円", Fidelity.STRICT),
        ok("ma200_rising", "200日MAが1か月前より上向き", ma200_slope > 0 if pd.notna(ma200_slope) else None, Role.REQUIRED, Layer.MARKET_TREND, ma200_slope, 0, "%", Fidelity.PRACTICAL),
        ok("above_52w_low", "52週安値から30%以上上昇", above_low >= c["low_52w_above_pct"] if pd.notna(above_low) else None, Role.REQUIRED, Layer.QUALITY_MOMENTUM, above_low, c["low_52w_above_pct"], "%", Fidelity.STRICT),
        tri("near_52w_high", "52週高値から25%以内", below_high, lambda v: v <= c["high_52w_within_pct"], lambda v: v <= 30, Role.REQUIRED, Layer.QUALITY_MOMENTUM, c["high_52w_within_pct"], "%", Fidelity.STRICT),
        tri("rs", "市場内Relative Strength上位", metrics.get("momentum_percentile"), lambda v: v >= c["rs_percentile"], lambda v: v >= 60, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, c["rs_percentile"], "percentile", Fidelity.PROXY, "市場内複合モメンタム順位をRS Ratingの代理に使用"),
        ok("vcp", "値幅が段階的に収縮", contracting if widths else None, Role.SUPPORTING, Layer.ENTRY_SETUP, " → ".join(map(str, widths)) if widths else None, "収縮", "%", Fidelity.PROXY, "60/30/15日のレンジ幅による機械判定"),
        tri("pivot", "Pivotまで3%以内", distance, lambda v: -2 <= v <= c["pivot_watch_pct"], lambda v: 0 < v <= 7, Role.TRIGGER, Layer.ENTRY_SETUP, c["pivot_watch_pct"], "%", Fidelity.PROXY),
    ]
    if -2 <= distance <= 0 and x.volume_ratio >= 1.2:
        state = SetupState.BREAKOUT
    elif 0 < distance <= c["pivot_watch_pct"]:
        state = SetupState.BREAKOUT_WATCH
    elif contracting:
        state = SetupState.SETUP_FORMING
    elif x.close > x.ma50 * 1.15:
        state = SetupState.EXTENDED
    else:
        state = SetupState.NOT_QUALIFIED
    return StrategyResult("Minervini", state, conditions, pivot, float(x.ma50), "Trend Template + VCP proxy")

