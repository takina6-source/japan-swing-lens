from __future__ import annotations

import pandas as pd
from ..models import Fidelity, Layer, Role, SetupState, StrategyResult
from .common import ok, tri


def evaluate(df: pd.DataFrame, metrics: dict, cfg: dict) -> StrategyResult:
    c = cfg["connors"]
    x = df.iloc[-1]
    conditions = [
        ok("long_trend", "株価 > 200日MA", x.close > x.ma200 if pd.notna(x.ma200) else None, Role.REQUIRED, Layer.MARKET_TREND, x.close, x.ma200, "円", Fidelity.STRICT),
        tri("rsi2", "RSI(2)が短期売られ過ぎ", x.rsi2, lambda v: v < c["rsi2_oversold"], lambda v: v < c["rsi2_borderline"], Role.TRIGGER, Layer.ENTRY_SETUP, c["rsi2_oversold"], "", Fidelity.STRICT),
        ok("pullback", "直近3日がPullback", x.close < df.close.iloc[-4] if len(df) >= 4 else None, Role.SUPPORTING, Layer.ENTRY_SETUP, (x.close/df.close.iloc[-4]-1)*100 if len(df)>=4 else None, 0, "%", Fidelity.PRACTICAL),
        ok("liquid", "最低売買代金", metrics.get("liquid"), Role.SUPPORTING, Layer.QUALITY_MOMENTUM, metrics.get("trading_value_20d"), cfg["liquidity"]["minimum_trading_value_yen"], "円", Fidelity.PRACTICAL),
    ]
    if x.close > x.ma200 and x.rsi2 < c["rsi2_oversold"]:
        state = SetupState.PULLBACK
    elif x.close > x.ma200 and x.rsi2 < c["rsi2_borderline"]:
        state = SetupState.SETUP_FORMING
    elif x.rsi2 > c["exit_rsi2"]:
        state = SetupState.EXTENDED
    else:
        state = SetupState.NOT_QUALIFIED
    return StrategyResult("Connors", state, conditions, None, float(df.low.tail(3).min()), "RSI(2) pullback; trend-following breakoutとは別枠")

