from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ValidationSubject:
    validation_subject_id: str
    subject_type: str
    code: str
    stock_name: str
    anchor_date: str
    anchor_price: float
    price_basis: str
    benchmark_anchor_price: float | None
    momentum_percentile: float | None
    trading_value: float | None
    trading_value_20d: float | None
    liquidity_level: str | None
    market: str | None
    size_class: str | None
    strategy_version: str
    threshold_version: str
    schema_version: str
    research_version: str
    source_event_id: str | None
    data_origin: str
    evaluation_phase: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntrySimulation:
    method: str
    entry_date: str | None
    entry_price: float | None
    price_basis: str
    reference_only: bool
    eligible: bool
    reason: str | None
    path_ambiguous: bool
    gap_pct: float | None = None
    gap_atr: float | None = None
    gap_r: float | None = None
    breakout_range_atr: float | None = None
    false_breakout: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
