"""Shared path evaluation primitives used by Core and Research subjects."""
from __future__ import annotations

import pandas as pd


VALIDATION_ENGINE_VERSION = "validation-subject-v1"


def price_on_or_before(frame: pd.DataFrame | None, date: pd.Timestamp,
                       column: str = "close") -> float | None:
    if frame is None or frame.empty or column not in frame.columns:
        return None
    rows = frame.loc[frame.index <= date]
    return float(rows[column].iloc[-1]) if not rows.empty else None


def path_metrics(path: pd.DataFrame, anchor_price: float,
                 benchmark: pd.DataFrame | None = None,
                 anchor_date: pd.Timestamp | None = None,
                 end_date: pd.Timestamp | None = None,
                 benchmark_price_basis: str = "close") -> dict:
    if path.empty:
        raise ValueError("path must not be empty")
    close = float(path.close.iloc[-1])
    absolute = (close / float(anchor_price) - 1) * 100
    relative = None
    if benchmark is not None and anchor_date is not None and end_date is not None:
        benchmark_start = price_on_or_before(benchmark, anchor_date, benchmark_price_basis)
        benchmark_now = price_on_or_before(benchmark, end_date)
        if benchmark_start and benchmark_now:
            relative = absolute - ((benchmark_now / benchmark_start - 1) * 100)
    return {
        "close": close,
        "return_abs": absolute,
        "benchmark_relative_return": relative,
        "mfe": (float(path.high.max()) / float(anchor_price) - 1) * 100,
        "mae": (float(path.low.min()) / float(anchor_price) - 1) * 100,
    }
