from __future__ import annotations

import pandas as pd
from ..fundamentals import annual_earnings_condition
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult, Verdict
from ..pivots import pivot_state, strategy_pivot
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    c = cfg["oneil"]
    x = df.iloc[-1]
    eps = metrics.get("eps_growth")
    sales = metrics.get("sales_growth")
    pivot = strategy_pivot("CAN SLIM", df, cfg,
                           metrics.get("pivot_registry", {}).get("CAN SLIM"))
    dist = (pivot.price / x.close - 1) * 100 if pivot else float("nan")
    conditions = [
        tri("current_earnings", "C: 直近EPS成長", eps, lambda v: v >= c["eps_growth_pct"], lambda v: v >= 15, Role.REQUIRED, Layer.QUALITY_MOMENTUM, c["eps_growth_pct"], "%", Fidelity.PRACTICAL),
        tri("sales", "売上高成長", sales, lambda v: v >= c["sales_growth_pct"], lambda v: v >= 10, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, c["sales_growth_pct"], "%", Fidelity.PRACTICAL),
        annual_earnings_condition(metrics, cfg),
        ok("new", "N: 新製品・新経営・新高値", x.close >= x.high52 * .95 if pd.notna(x.high52) else None, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, (x.high52-x.close)/x.high52*100 if pd.notna(x.high52) else None, 5, "%", Fidelity.PROXY, "新高値接近度のみを代理使用"),
        tri("rs", "L: 市場リーダー候補", metrics.get("momentum_percentile"), lambda v: v >= c["rs_percentile"], lambda v: v >= 70, Role.REQUIRED, Layer.QUALITY_MOMENTUM, c["rs_percentile"], "percentile", Fidelity.PROXY),
        ok("institutional", "I: Institutional Sponsorship", None, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, fidelity=Fidelity.STRICT, note="日本株で一貫した保有増減データを取得できないためN/A"),
        ok("market", "M: 市場トレンドが良好", metrics.get("market_bullish"),
           Role.REQUIRED, Layer.MARKET_TREND, metrics.get("market_regime"),
           "BULL以上", fidelity=Fidelity.PRACTICAL),
        tri("base_breakout", "Base上限付近／突破イベント", dist, lambda v: -2 <= v <= 3, lambda v: 0 < v <= 7, Role.TRIGGER, Layer.ENTRY_SETUP, 3, "%", pivot.fidelity if pivot else Fidelity.PROXY),
    ]
    required = [c_ for c_ in conditions if c_.role == Role.REQUIRED and c_.verdict != Verdict.NA]
    required_ok = bool(required) and all(c_.verdict != Verdict.FAIL for c_ in required)
    event = pivot_state(df, pivot, cfg, 3, 1.3, required_ok, 3 < dist <= 7)
    return StrategyResult("CAN SLIM", event.state, conditions, pivot.price if pivot else None,
                          float(x.ma50), "CAN SLIM fundamentals + Base structure",
                          pivot_type=pivot.pivot_type if pivot else "N/A",
                          pivot_basis=pivot.basis if pivot else "N/A",
                          pivot_fidelity=pivot.fidelity if pivot else Fidelity.PROXY,
                          pivot_formed_date=pivot.formed_date if pivot else None,
                          setup_start_date=pivot.setup_start_date if pivot else None,
                          setup_id=pivot.setup_id if pivot else None,
                          setup_age=pivot.setup_age if pivot else None,
                          distance_to_pivot_pct=event.distance_pct,
                          breakout_date=event.breakout_date, breakout_age=event.breakout_age)
