from __future__ import annotations

import math
import pandas as pd
from .models import SetupState, TradePlan


def build_trade_plan(state: SetupState, price: float, strategies: dict,
                     df: pd.DataFrame, cfg: dict) -> TradePlan:
    """再現可能な価格シナリオを作る。個別の資金量や許容損失は扱わない。"""
    c = cfg["trade_plan"]
    breakout_names = ("Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas")
    pivots = [strategies[n].pivot for n in breakout_names if strategies[n].pivot]
    pivot = min(pivots, key=lambda v: abs(v - price)) if pivots else None

    if state in (SetupState.FAILED, SetupState.NOT_QUALIFIED, SetupState.EXTENDED) or pivot is None:
        reason = "条件未成立のため新規エントリー価格を算出しません"
        if state == SetupState.EXTENDED:
            reason = "Pivotから離れた過熱状態のため、追いかけず次のセットアップを待ちます"
        return TradePlan("見送り", None, None, None, None, None, None, None, None, reason)

    if state == SetupState.PULLBACK:
        entry_low, entry_high = price * .995, price * 1.005
        structural = float(df.low.tail(3).min())
        basis = "RSI(2)押し目条件成立時の現在値近辺。終値確認を前提とする参考帯"
    else:
        entry_low = pivot
        entry_high = pivot * (1 + c["entry_zone_above_pivot_pct"] / 100)
        structural_stops = [strategies[n].stop for n in breakout_names
                            if strategies[n].stop and strategies[n].stop < entry_high]
        structural = max(structural_stops) if structural_stops else float(df.low.tail(10).min())
        basis = f"最寄りPivot突破から+{c['entry_zone_above_pivot_pct']}%までの低リスク帯"

    reference_entry = max(entry_low, min(price, entry_high))
    maximum_loss_stop = reference_entry * (1 - c["maximum_stop_loss_pct"] / 100)
    # 構造的支持線と最大許容幅のうち、エントリーに近い方を無効化ラインにする。
    stop = max(structural, maximum_loss_stop)
    if stop >= reference_entry:
        stop = maximum_loss_stop
    risk = reference_entry - stop
    if risk <= 0 or not math.isfinite(risk):
        return TradePlan("算出不能", entry_low, entry_high, None, None, None, None, None, None,
                         basis, "有効な支持線を算出できません")
    target_1r = reference_entry + risk * c["first_target_r_multiple"]
    target_2r = reference_entry + risk * c["main_target_r_multiple"]
    target_extended = reference_entry * (1 + c["extended_profit_pct"] / 100)
    risk_pct = risk / reference_entry * 100
    warning = ""
    if price > pivot * (1 + c["do_not_chase_above_pivot_pct"] / 100):
        warning = f"現在値がPivotを{c['do_not_chase_above_pivot_pct']}%超えており、追随エントリーは見送り候補です"
    status = "押し目候補" if state == SetupState.PULLBACK else ("エントリー帯" if entry_low <= price <= entry_high else "指値待機")
    return TradePlan(status, entry_low, entry_high, stop, target_1r, target_2r,
                     target_extended, risk_pct, 2.0, basis, warning)

