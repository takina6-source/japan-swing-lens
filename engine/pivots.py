from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1

import pandas as pd

from .models import Fidelity, SetupState


@dataclass(frozen=True)
class PivotSpec:
    price: float
    pivot_type: str
    basis: str
    fidelity: Fidelity
    setup_start_date: str
    formed_date: str
    setup_id: str
    setup_age: int


@dataclass(frozen=True)
class PivotState:
    state: SetupState
    distance_pct: float
    breakout_date: str | None = None
    breakout_age: int | None = None


def strategy_pivot(strategy: str, df: pd.DataFrame, cfg: dict,
                   previous: dict | None = None) -> PivotSpec | None:
    """T-1以前だけを使ってStrategy固有Pivotを作る。

    保存済みsetupがまだ有効なら同じPivotを返し、現在値に合わせた日々の
    切り上がりを防ぐ。構造の信頼性が不足するときだけLookback Proxyへ戻す。
    """
    if len(df) < 22:
        return None
    hist = df.iloc[:-1]
    as_of = df.index[-1]
    max_age = int(cfg["pivot"]["setup_max_age_days"])
    frozen = _previous(previous, hist, as_of, max_age)
    if frozen:
        return frozen
    if strategy == "Minervini":
        candidate = _vcp(hist, cfg)
        return candidate or _proxy(strategy, hist, 20, "20-day High")
    if strategy == "Qullamaggie":
        candidate = _qullamaggie(hist, cfg)
        return candidate or _proxy(strategy, hist, 20, "20-day High")
    if strategy == "CAN SLIM":
        candidate = _flat_base(hist, cfg)
        return candidate or _proxy(strategy, hist, 50, "50-day High")
    if strategy == "Weinstein":
        candidate = _stage1(hist, cfg)
        return candidate or _proxy(strategy, hist, 126, "126-day High")
    if strategy == "Darvas":
        candidate = _darvas(hist, cfg)
        return candidate or _proxy(strategy, hist, int(cfg["darvas"]["box_days"]), "20-day Box High")
    return None


def pivot_state(df: pd.DataFrame, pivot: PivotSpec | None, cfg: dict,
                watch_pct: float, volume_threshold: float = 1.0,
                qualified: bool = True, forming: bool = False) -> PivotState:
    if pivot is None or not qualified:
        return PivotState(SetupState.SETUP_FORMING if forming else SetupState.NOT_QUALIFIED,
                          float("nan"))
    x = df.iloc[-1]
    price = pivot.price
    distance = (price / float(x.close) - 1) * 100
    recent_days = int(cfg["pivot"]["recent_breakout_days"])
    window = df.tail(recent_days + 2)
    crossings = window.index[(window.close > price) & (window.close.shift(1) <= price)
                             & (window.volume_ratio >= volume_threshold)]
    breakout_date = str(crossings[-1].date()) if len(crossings) else None
    breakout_age = _sessions_since(df, crossings[-1]) if len(crossings) else None
    volume_ok = pd.notna(x.volume_ratio) and float(x.volume_ratio) >= volume_threshold
    crossed_today = (float(df.close.iloc[-2]) <= price < float(x.close)) and volume_ok
    recent = breakout_age is not None and breakout_age <= recent_days and float(x.close) > price
    failure_pct = float(cfg["pivot"]["failed_below_pivot_pct"])
    extended_pct = float(cfg["pivot"]["extended_above_pivot_pct"])
    if breakout_date and float(x.close) < price * (1 - failure_pct / 100):
        return PivotState(SetupState.FAILED, distance, breakout_date, breakout_age)
    if (breakout_date or float(x.close) > price) and float(x.close) > price * (1 + extended_pct / 100):
        return PivotState(SetupState.EXTENDED, distance, breakout_date, breakout_age)
    if crossed_today or recent:
        return PivotState(SetupState.BREAKOUT, distance, breakout_date, breakout_age)
    if 0 <= distance <= watch_pct:
        return PivotState(SetupState.BREAKOUT_WATCH, distance)
    return PivotState(SetupState.SETUP_FORMING if forming else SetupState.NOT_QUALIFIED, distance)


def _previous(previous: dict | None, hist: pd.DataFrame, as_of: pd.Timestamp,
              max_age: int) -> PivotSpec | None:
    if not previous or not previous.get("pivot_price") or not previous.get("setup_start_date"):
        return None
    start = pd.Timestamp(previous["setup_start_date"])
    age = int((hist.index >= start).sum())
    if age > max_age:
        return None
    return PivotSpec(float(previous["pivot_price"]), previous.get("pivot_type") or "Saved Pivot",
                     previous.get("pivot_basis") or "Saved Structure",
                     Fidelity(previous.get("pivot_fidelity") or Fidelity.PRACTICAL.value),
                     str(start.date()), previous.get("pivot_formed_date") or str(hist.index[-1].date()),
                     previous.get("setup_id") or _id("saved", start, previous["pivot_price"]), age)


def _vcp(hist: pd.DataFrame, cfg: dict) -> PivotSpec | None:
    if len(hist) < 60:
        return None
    part = hist.tail(60)
    widths = [_width(part.tail(n)) for n in (60, 30, 15)]
    if not (widths[0] > widths[1] > widths[2]
            and widths[2] <= float(cfg["minervini"]["vcp_max_last_contraction_pct"])):
        return None
    final = part.tail(15)
    return _spec("vcp", final.high.max(), "VCP Final Contraction", "Structure",
                 Fidelity.PRACTICAL, part.index[0], final.index[-1], hist)


def _qullamaggie(hist: pd.DataFrame, cfg: dict) -> PivotSpec | None:
    c = cfg["qullamaggie"]
    min_days, max_days = int(c["consolidation_min_days"]), int(c["consolidation_max_days"])
    if len(hist) < max_days + 40:
        return None
    for days in range(max_days, min_days - 1, -5):
        base = hist.tail(days)
        prior = hist.iloc[-days - 63:-days]
        if len(prior) < 40:
            continue
        prior_move = (float(base.close.iloc[0]) / float(prior.low.min()) - 1) * 100
        tight = _width(base)
        recent = _width(base.tail(max(min_days, days // 2)))
        if prior_move >= float(c["prior_move_min_pct"]) and tight <= float(c["base_max_width_pct"]) and recent <= tight:
            return _spec("qulla", base.high.max(), "Prior Move Consolidation", "Structure",
                         Fidelity.PRACTICAL, base.index[0], base.index[-1], hist)
    return None


def _flat_base(hist: pd.DataFrame, cfg: dict) -> PivotSpec | None:
    c = cfg["oneil"]
    days = int(c["flat_base_days"])
    if len(hist) < days:
        return None
    base = hist.tail(days)
    width = _width(base)
    near_top = float(base.close.iloc[-1]) >= float(base.high.max()) * .9
    if width <= float(c["flat_base_max_width_pct"]) and near_top:
        return _spec("flat", base.high.max(), "Flat Base", "Structure",
                     Fidelity.PRACTICAL, base.index[0], base.index[-1], hist)
    return None


def _stage1(hist: pd.DataFrame, cfg: dict) -> PivotSpec | None:
    days = int(cfg["weinstein"]["stage1_base_days"])
    if len(hist) < max(days, 171):
        return None
    base = hist.tail(days)
    slope = (float(hist.ma150.iloc[-1]) / float(hist.ma150.iloc[-21]) - 1) * 100
    if _width(base) <= float(cfg["weinstein"]["stage1_max_width_pct"]) and abs(slope) <= 3:
        return _spec("stage1", base.high.max(), "Stage 1 Resistance", "Structure",
                     Fidelity.PRACTICAL, base.index[0], base.index[-1], hist)
    return None


def _darvas(hist: pd.DataFrame, cfg: dict) -> PivotSpec | None:
    days = int(cfg["darvas"]["box_days"])
    if len(hist) < days:
        return None
    box = hist.tail(days)
    top = float(box.high.max())
    touches = int((box.high >= top * .98).sum())
    if _width(box) <= float(cfg["darvas"]["box_max_width_pct"]) and touches >= 2:
        return _spec("darvas", top, "Darvas Box Top", "Structure",
                     Fidelity.PRACTICAL, box.index[0], box.index[-1], hist)
    return None


def _proxy(strategy: str, hist: pd.DataFrame, days: int, pivot_type: str) -> PivotSpec:
    part = hist.tail(days)
    return _spec(strategy, part.high.max(), pivot_type, "Lookback Proxy", Fidelity.PROXY,
                 part.index[0], part.index[-1], hist)


def _spec(prefix: str, price, pivot_type: str, basis: str, fidelity: Fidelity,
          start, formed, hist: pd.DataFrame) -> PivotSpec:
    value = float(price)
    return PivotSpec(value, pivot_type, basis, fidelity, str(start.date()), str(formed.date()),
                     _id(prefix, start, value), int((hist.index >= start).sum()))


def _id(prefix: str, start, price) -> str:
    raw = f"{prefix}|{pd.Timestamp(start).date()}|{float(price):.4f}"
    return f"{prefix}-{sha1(raw.encode()).hexdigest()[:12]}"


def _width(frame: pd.DataFrame) -> float:
    midpoint = float(frame.close.mean())
    return (float(frame.high.max()) - float(frame.low.min())) / midpoint * 100 if midpoint else 999.0


def _sessions_since(df: pd.DataFrame, date) -> int:
    return max(0, int((df.index > pd.Timestamp(date)).sum()))
