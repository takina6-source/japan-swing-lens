from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .models import EntrySimulation


def simulate_entry_methods(frame: pd.DataFrame, breakout_date: str,
                           pivot: float | None, pivot_known_date: str | None,
                           stop: float | None) -> dict[str, EntrySimulation]:
    """Create fixed daily-bar entry models without inferring intraday ordering."""
    date = pd.Timestamp(breakout_date)
    ordered = frame.sort_index()
    if date not in ordered.index:
        return {method: _ineligible(method, "BREAKOUT_OHLC_MISSING")
                for method in ("T_PLUS_1_OPEN", "T_CLOSE_REFERENCE", "PIVOT_STOP")}
    row = ordered.loc[date]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    position = ordered.index.get_loc(date)
    if not isinstance(position, int):
        position = int(position[-1] if hasattr(position, "__len__") else position)
    atr = _number(row.get("atr14"))
    close = float(row.close)
    next_row = ordered.iloc[position + 1] if position + 1 < len(ordered) else None
    next_date = ordered.index[position + 1] if next_row is not None else None
    risk = close - float(stop) if stop is not None and close > float(stop) else None
    gap_pct = ((float(next_row.open) / close - 1) * 100) if next_row is not None else None
    gap_atr = ((float(next_row.open) - close) / atr) if next_row is not None and atr else None
    gap_r = ((float(next_row.open) - close) / risk) if next_row is not None and risk else None
    range_atr = ((float(row.high) - float(row.low)) / atr) if atr else None
    false_breakout = bool(pivot is not None and float(row.high) > float(pivot)
                          and close <= float(pivot))

    next_open = EntrySimulation(
        "T_PLUS_1_OPEN", str(next_date.date()) if next_date is not None else None,
        float(next_row.open) if next_row is not None else None, "OPEN", False,
        next_row is not None, None if next_row is not None else "NEXT_SESSION_NOT_AVAILABLE",
        False,
        gap_pct, gap_atr, gap_r, range_atr, false_breakout)
    reference = EntrySimulation(
        "T_CLOSE_REFERENCE", breakout_date, close, "REFERENCE", True, True,
        "REFERENCE_ONLY_NOT_EXECUTABLE", False, gap_pct, gap_atr, gap_r,
        range_atr, false_breakout)
    known = bool(pivot is not None and pivot_known_date
                 and str(pivot_known_date) < breakout_date)
    triggered = bool(known and float(row.high) >= float(pivot))
    ambiguous = bool(triggered and stop is not None and float(row.low) <= float(stop))
    pivot_entry = EntrySimulation(
        "PIVOT_STOP", breakout_date if triggered else None,
        float(pivot) if triggered else None, "PIVOT_TRIGGER", False, triggered,
        None if triggered else ("PIVOT_NOT_KNOWN_BEFORE_SESSION" if not known
                                else "PIVOT_NOT_TRIGGERED"),
        ambiguous, gap_pct, gap_atr, gap_r, range_atr, false_breakout)
    return {item.method: item for item in (next_open, reference, pivot_entry)}


def _ineligible(method: str, reason: str) -> EntrySimulation:
    basis = {"T_PLUS_1_OPEN": "OPEN", "T_CLOSE_REFERENCE": "REFERENCE",
             "PIVOT_STOP": "PIVOT_TRIGGER"}[method]
    return EntrySimulation(method, None, None, basis, method == "T_CLOSE_REFERENCE",
                           False, reason, False)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None
