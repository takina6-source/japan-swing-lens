from __future__ import annotations

import math


LIQUIDITY_LEVELS = ("VERY HIGH", "HIGH", "GOOD", "LOW", "VERY LOW")


def liquidity_level(trading_value_20d: float | None, cfg: dict) -> str:
    """Classify normal trading liquidity without changing setup eligibility."""
    value = _number(trading_value_20d)
    if value is None:
        return "N/A"
    levels = cfg["liquidity"]["levels"]
    if value >= float(levels["very_high"]):
        return "VERY HIGH"
    if value >= float(levels["high"]):
        return "HIGH"
    if value >= float(levels["good"]):
        return "GOOD"
    if value >= float(levels["low"]):
        return "LOW"
    return "VERY LOW"


def trading_value_ratio(current: float | None, average_20d: float | None) -> float | None:
    current_value = _number(current)
    average = _number(average_20d)
    if current_value is None or average is None or average <= 0:
        return None
    return current_value / average


def _number(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
