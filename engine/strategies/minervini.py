from __future__ import annotations

import pandas as pd
from ..indicators import contraction_widths
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult, Verdict
from ..pivots import pivot_state, strategy_pivot
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    c = cfg["minervini"]
    x = df.iloc[-1]
    ma200_slope = (x.ma200 / df.ma200.iloc[-21] - 1) * 100 if len(df) >= 221 else float("nan")
    above_low = (x.close / x.low52 - 1) * 100
    below_high = (x.high52 - x.close) / x.high52 * 100
    widths = contraction_widths(df)
    contracting = len(widths) == 3 and widths[0] > widths[1] > widths[2]
    pivot = strategy_pivot("Minervini", df, cfg,
                           metrics.get("pivot_registry", {}).get("Minervini"))
    distance = (pivot.price / x.close - 1) * 100 if pivot else float("nan")
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
        tri("pivot", "VCP Pivotまで3%以内", distance, lambda v: -2 <= v <= c["pivot_watch_pct"], lambda v: 0 < v <= 7, Role.TRIGGER, Layer.ENTRY_SETUP, c["pivot_watch_pct"], "%", pivot.fidelity if pivot else Fidelity.PROXY),
    ]
    required_ok = all(c_.verdict != Verdict.FAIL for c_ in conditions if c_.role == Role.REQUIRED)
    event = pivot_state(df, pivot, cfg, c["pivot_watch_pct"], 1.2, required_ok, contracting)
    return StrategyResult("Minervini", event.state, conditions, pivot.price if pivot else None,
                          float(x.ma50), "Trend Template + VCP structure",
                          pivot_type=pivot.pivot_type if pivot else "N/A",
                          pivot_basis=pivot.basis if pivot else "N/A",
                          pivot_fidelity=pivot.fidelity if pivot else Fidelity.PROXY,
                          pivot_formed_date=pivot.formed_date if pivot else None,
                          setup_start_date=pivot.setup_start_date if pivot else None,
                          setup_id=pivot.setup_id if pivot else None,
                          setup_age=pivot.setup_age if pivot else None,
                          distance_to_pivot_pct=event.distance_pct,
                          breakout_date=event.breakout_date, breakout_age=event.breakout_age)
