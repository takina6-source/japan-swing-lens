from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    PASS = "○"
    BORDERLINE = "△"
    FAIL = "×"
    NA = "N/A"


class Role(str, Enum):
    REQUIRED = "REQUIRED"
    SUPPORTING = "SUPPORTING"
    TRIGGER = "TRIGGER"


class Fidelity(str, Enum):
    STRICT = "STRICT"
    PRACTICAL = "PRACTICAL"
    PROXY = "PROXY"


class Layer(str, Enum):
    MARKET_TREND = "Market / Trend"
    QUALITY_MOMENTUM = "Stock Quality / Momentum"
    ENTRY_SETUP = "Entry Setup"


class SetupState(str, Enum):
    BREAKOUT = "BREAKOUT"
    BREAKOUT_WATCH = "BREAKOUT WATCH"
    SETUP_FORMING = "SETUP FORMING"
    PULLBACK = "PULLBACK"
    EXTENDED = "EXTENDED"
    FAILED = "FAILED"
    NOT_QUALIFIED = "NOT QUALIFIED"


@dataclass
class ConditionResult:
    key: str
    label: str
    verdict: Verdict
    role: Role
    layer: Layer
    value: Any = None
    reference: Any = None
    unit: str = ""
    fidelity: Fidelity = Fidelity.PRACTICAL
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row.update({"verdict": self.verdict.value, "role": self.role.value,
                    "layer": self.layer.value, "fidelity": self.fidelity.value})
        return row


@dataclass
class StrategyResult:
    strategy: str
    state: SetupState
    conditions: list[ConditionResult] = field(default_factory=list)
    pivot: float | None = None
    stop: float | None = None
    summary: str = ""
    pivot_type: str = "N/A"
    pivot_basis: str = "N/A"
    pivot_fidelity: Fidelity = Fidelity.PROXY
    pivot_formed_date: str | None = None
    setup_start_date: str | None = None
    setup_id: str | None = None
    setup_age: int | None = None
    distance_to_pivot_pct: float | None = None
    breakout_date: str | None = None
    breakout_age: int | None = None

    @property
    def counts(self) -> dict[str, int]:
        return {v.value: sum(c.verdict == v for c in self.conditions) for v in Verdict}

    @property
    def positive(self) -> bool:
        required = [c for c in self.conditions if c.role == Role.REQUIRED and c.verdict != Verdict.NA]
        trigger = [c for c in self.conditions if c.role == Role.TRIGGER]
        return bool(required) and all(c.verdict != Verdict.FAIL for c in required) and any(
            c.verdict == Verdict.PASS for c in trigger
        )

    @property
    def compact(self) -> str:
        c = self.counts
        return f"{c['○']}○/{c['△']}△/{c['×']}×/{c['N/A']}N/A"

    @property
    def coverage(self) -> float:
        return (sum(c.verdict != Verdict.NA for c in self.conditions) /
                len(self.conditions) * 100) if self.conditions else 0.0

    @property
    def confidence(self) -> str:
        evaluated = [c for c in self.conditions if c.verdict != Verdict.NA]
        if not evaluated or self.coverage < 45:
            return "LOW"
        weight = {Fidelity.STRICT: 1.0, Fidelity.PRACTICAL: .8, Fidelity.PROXY: .5}
        quality = sum(weight[c.fidelity] for c in evaluated) / len(evaluated) * self.coverage
        return "HIGH" if quality >= 72 else "MEDIUM" if quality >= 52 else "LOW"


@dataclass
class StockAnalysis:
    code: str
    name: str
    as_of: str
    source: str
    metrics: dict[str, Any]
    strategies: dict[str, StrategyResult]
    state: SetupState
    confluence: int
    breakout_strategy_count: int = 0
    aligned_strategy_count: int = 0
    coverage: float = 0.0
    confidence: str = "LOW"
    pivot_fidelity: Fidelity = Fidelity.PROXY
    setup_id: str | None = None
    trade_plan: "TradePlan | None" = None
    rank_key: tuple = field(default_factory=tuple)


@dataclass
class TradePlan:
    status: str
    entry_low: float | None
    entry_high: float | None
    stop: float | None
    target_1r: float | None
    target_2r: float | None
    target_extended: float | None
    risk_pct: float | None
    reward_risk: float | None
    basis: str
    warning: str = ""
