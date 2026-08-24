from __future__ import annotations

import pandas as pd
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    c = cfg["oneil"]
    x = df.iloc[-1]
    eps = metrics.get("eps_growth")
    sales = metrics.get("sales_growth")
    pivot = float(df.high.tail(50).iloc[:-1].max())
    dist = (pivot / x.close - 1) * 100
    conditions = [
        tri("current_earnings", "C: 直近EPS成長", eps, lambda v: v >= c["eps_growth_pct"], lambda v: v >= 15, Role.REQUIRED, Layer.QUALITY_MOMENTUM, c["eps_growth_pct"], "%", Fidelity.PRACTICAL),
        tri("sales", "売上高成長", sales, lambda v: v >= c["sales_growth_pct"], lambda v: v >= 10, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, c["sales_growth_pct"], "%", Fidelity.PRACTICAL),
        ok("annual_earnings", "A: 年次利益成長", None, Role.REQUIRED, Layer.QUALITY_MOMENTUM, None, "3年", fidelity=Fidelity.STRICT, note="3年以上の正規化年次EPS履歴が未取得"),
        ok("new", "N: 新製品・新経営・新高値", x.close >= x.high52 * .95 if pd.notna(x.high52) else None, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, (x.high52-x.close)/x.high52*100 if pd.notna(x.high52) else None, 5, "%", Fidelity.PROXY, "新高値接近度のみを代理使用"),
        tri("rs", "L: 市場リーダー候補", metrics.get("momentum_percentile"), lambda v: v >= c["rs_percentile"], lambda v: v >= 70, Role.REQUIRED, Layer.QUALITY_MOMENTUM, c["rs_percentile"], "percentile", Fidelity.PROXY),
        ok("institutional", "I: Institutional Sponsorship", None, Role.SUPPORTING, Layer.QUALITY_MOMENTUM, fidelity=Fidelity.STRICT, note="日本株で一貫した保有増減データを取得できないためN/A"),
        ok("market", "M: 市場トレンドが良好", metrics.get("market_bullish"), Role.REQUIRED, Layer.MARKET_TREND, metrics.get("market_regime"), "BULL以上", Fidelity.PRACTICAL),
        tri("base_breakout", "Base上限付近／ブレイク", dist, lambda v: -2 <= v <= 3, lambda v: 0 < v <= 7, Role.TRIGGER, Layer.ENTRY_SETUP, 3, "%", Fidelity.PROXY),
    ]
    state = SetupState.BREAKOUT if x.close >= pivot and x.volume_ratio >= 1.3 else (SetupState.BREAKOUT_WATCH if 0 <= dist <= 3 else SetupState.SETUP_FORMING if 3 < dist <= 7 else SetupState.NOT_QUALIFIED)
    return StrategyResult("CAN SLIM", state, conditions, pivot, float(x.ma50), "取得不能項目はN/A")

