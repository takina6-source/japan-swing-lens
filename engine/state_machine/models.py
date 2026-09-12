"""Immutable contracts used by the Phase 2A domain layer."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


CODE_RE = re.compile(r"^[0-9]{4}$")


class SetupPhase(str, Enum):
    FORMING = "FORMING"
    WATCH = "WATCH"
    POST_BREAKOUT = "POST_BREAKOUT"
    FAILED = "FAILED"
    RETRY_WATCH = "RETRY_WATCH"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"

    @property
    def terminal(self) -> bool:
        return self in (SetupPhase.EXPIRED, SetupPhase.CLOSED)


class EventType(str, Enum):
    SETUP_MINTED = "SETUP_MINTED"
    BOOTSTRAP_OBSERVED = "BOOTSTRAP_OBSERVED"
    WATCH_ENTERED = "WATCH_ENTERED"
    BREAKOUT_CONFIRMED = "BREAKOUT_CONFIRMED"
    FAILED_CONFIRMED = "FAILED_CONFIRMED"
    RETRY_WATCH_ENTERED = "RETRY_WATCH_ENTERED"
    REBREAKOUT_CONFIRMED = "REBREAKOUT_CONFIRMED"
    PIVOT_REVISED = "PIVOT_REVISED"
    SETUP_EXPIRED = "SETUP_EXPIRED"
    SETUP_CLOSED = "SETUP_CLOSED"
    CONTINUITY_RESUMED = "CONTINUITY_RESUMED"
    CORRECTION_RECORDED = "CORRECTION_RECORDED"


class IdentityDecision(str, Enum):
    LINK = "LINK"
    MINT = "MINT"
    AMBIGUOUS = "AMBIGUOUS"
    NO_SETUP = "NO_SETUP"


class ObservationStatus(str, Enum):
    CURRENT = "CURRENT"
    NO_NEW_MARKET_OBSERVATION = "NO_NEW_MARKET_OBSERVATION"
    STALE_MARKET_DATE = "STALE_MARKET_DATE"
    INSUFFICIENT_PRICE_HISTORY = "INSUFFICIENT_PRICE_HISTORY"
    FETCH_FAILED = "FETCH_FAILED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    INVALID_INPUT = "INVALID_INPUT"


class StateStageBlocker(str, Enum):
    NONE = "NONE"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    LEDGER_UNAVAILABLE = "LEDGER_UNAVAILABLE"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    INPUT_CONFLICT = "INPUT_CONFLICT"


class RunStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class CoverageStatus(str, Enum):
    FULL = "FULL"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class PublishEligibility(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    ELIGIBLE_DEGRADED = "ELIGIBLE_DEGRADED"
    BLOCKED = "BLOCKED"


class ContinuityStatus(str, Enum):
    CONTIGUOUS = "CONTIGUOUS"
    PAUSED = "PAUSED"
    SUSPENDED_GAP = "SUSPENDED_GAP"
    TERMINAL = "TERMINAL"


class DecisionAction(str, Enum):
    TRANSITION = "TRANSITION"
    KEEP = "KEEP"
    REJECT = "REJECT"
    QUARANTINE = "QUARANTINE"
    REPLAY = "REPLAY"


class PermanentExitType(str, Enum):
    DELISTED_CONFIRMED = "DELISTED_CONFIRMED"
    SECURITY_CODE_RETIRED = "SECURITY_CODE_RETIRED"
    CORPORATE_SUCCESSOR_CONFIRMED = "CORPORATE_SUCCESSOR_CONFIRMED"
    SCOPE_POLICY_TERMINAL_REMOVAL = "SCOPE_POLICY_TERMINAL_REMOVAL"


def validate_code(code: str) -> None:
    if not isinstance(code, str) or CODE_RE.fullmatch(code) is None:
        raise ValueError(f"invalid security code: {code!r}")


def validate_positive_finite(value: float | None, name: str) -> None:
    if value is None or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{name} must be finite and positive")


def validate_iso_date(value: str | None, name: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be a canonical ISO date")


def validate_iso_datetime(value: str | None, name: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc


@dataclass(frozen=True)
class RunContext:
    run_id: str
    state_lineage: str
    expected_market_date: str
    market_session_index: int
    generated_at: str
    state_machine_version: str
    threshold_version: str

    def __post_init__(self) -> None:
        if not all((self.run_id, self.state_lineage, self.state_machine_version, self.threshold_version)):
            raise ValueError("run and version identifiers are required")
        validate_iso_date(self.expected_market_date, "expected market date")
        validate_iso_datetime(self.generated_at, "generated_at")
        if self.market_session_index < 0:
            raise ValueError("market_session_index must be non-negative")


@dataclass(frozen=True)
class ScopeMemberFact:
    code: str
    in_scope: bool
    status: ObservationStatus
    analysis_date: str | None = None
    latest_price_date: str | None = None
    history_count: int | None = None
    required_history_count: int | None = None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_code(self.code)
        validate_iso_date(self.analysis_date, "analysis date", optional=True)
        validate_iso_date(self.latest_price_date, "latest price date", optional=True)
        for value in (self.history_count, self.required_history_count):
            if value is not None and value < 0:
                raise ValueError("history counts must be non-negative")


@dataclass(frozen=True)
class PivotFact:
    price: float
    strategy: str
    pivot_type: str
    basis: str
    fidelity: str
    reference_date: str | None = None

    def __post_init__(self) -> None:
        validate_positive_finite(self.price, "pivot price")
        for name in ("strategy", "pivot_type", "basis", "fidelity"):
            if not getattr(self, name):
                raise ValueError(f"pivot {name} is required")
        validate_iso_date(self.reference_date, "pivot reference date", optional=True)

    @property
    def signature(self) -> tuple[Any, ...]:
        return (
            float(self.price), self.strategy, self.pivot_type,
            self.basis, self.fidelity, self.reference_date,
        )


@dataclass(frozen=True)
class CoreObservation:
    code: str
    analysis_date: str | None
    expected_market_date: str
    market_session_index: int
    status: ObservationStatus
    close: float | None = None
    previous_accepted_close: float | None = None
    core_observed_state: str | None = None
    aligned_trend_strategy_count: int | None = None
    breakout_trend_strategy_count: int | None = None
    observed_primary_pivot: PivotFact | None = None
    trend_strategy_states: dict[str, str] = field(default_factory=dict)
    connors_state: str | None = None
    reason_codes: tuple[str, ...] = ()
    input_sha256: str = ""
    observed_at: str | None = None
    source_versions: dict[str, str] = field(default_factory=dict)
    legacy_refs: dict[str, Any] = field(default_factory=dict)
    history_count: int | None = None

    def __post_init__(self) -> None:
        validate_code(self.code)
        validate_iso_date(self.expected_market_date, "expected market date")
        validate_iso_date(self.analysis_date, "analysis date", optional=True)
        validate_iso_datetime(self.observed_at, "observed_at", optional=True)
        if self.market_session_index < 0:
            raise ValueError("market_session_index must be non-negative")
        if self.status == ObservationStatus.CURRENT:
            validate_positive_finite(self.close, "close")
            if self.observed_primary_pivot is None:
                raise ValueError("CURRENT observation requires a primary pivot")
            if self.analysis_date != self.expected_market_date:
                raise ValueError("CURRENT observation date must match expected market date")
            if self.aligned_trend_strategy_count is None or self.aligned_trend_strategy_count < 0:
                raise ValueError("CURRENT observation requires aligned strategy count")
            if self.breakout_trend_strategy_count is None or self.breakout_trend_strategy_count < 0:
                raise ValueError("CURRENT observation requires breakout strategy count")
        if self.previous_accepted_close is not None:
            validate_positive_finite(self.previous_accepted_close, "previous accepted close")


@dataclass(frozen=True)
class PermanentExitEvidence:
    evidence_type: PermanentExitType
    evidence_ref: str
    effective_date: str

    def __post_init__(self) -> None:
        if not self.evidence_ref:
            raise ValueError("permanent exit evidence_ref is required")
        validate_iso_date(self.effective_date, "permanent exit effective date")


@dataclass(frozen=True)
class IdentityCandidate:
    core_setup_uid: str
    code: str
    phase: SetupPhase
    last_accepted_market_session_index: int

    def __post_init__(self) -> None:
        validate_code(self.code)


@dataclass(frozen=True)
class IdentityResolution:
    decision: IdentityDecision | None
    target_core_setup_uid: str | None
    reason_codes: tuple[str, ...]
    blocker: StateStageBlocker = StateStageBlocker.NONE
    origin_slot: str = "core-primary"


@dataclass(frozen=True)
class IdentityContext:
    observation: CoreObservation
    candidates: tuple[IdentityCandidate, ...]
    ledger_verified: bool
    explicit_slot_mapping: str | None = None
    scope_returned: bool = False
    lineage_gap: bool = False


@dataclass(frozen=True)
class SetupStateRecord:
    core_setup_uid: str
    code: str
    phase: SetupPhase
    tracking_pivot: PivotFact
    tracking_pivot_revision_no: int
    minted_market_session_index: int
    latest_accepted_market_session_index: int
    latest_accepted_date: str
    latest_input_sha256: str
    latest_accepted_close: float
    latest_accepted_observation_uid: str | None = None
    latest_breakout_market_session_index: int | None = None
    breakout_count: int = 0
    failure_cycle_no: int = 0
    continuity_status: ContinuityStatus = ContinuityStatus.CONTIGUOUS
    distribution_eligible: bool = True
    state_machine_version: str = "sm1"
    threshold_version: str = "smt1"
    state_version: int = 1
    latest_event_uid: str | None = None
    latest_breakout_event_uid: str | None = None
    latest_failure_event_uid: str | None = None
    phase_entered_observation_uid: str | None = None
    phase_entered_effective_date: str | None = None
    closure_reason_code: str | None = None
    closure_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        validate_code(self.code)
        validate_positive_finite(self.latest_accepted_close, "latest accepted close")
        validate_iso_date(self.latest_accepted_date, "latest accepted date")
        validate_iso_date(
            self.phase_entered_effective_date,
            "phase entered effective date",
            optional=True,
        )
        if self.tracking_pivot_revision_no < 1 or self.state_version < 1:
            raise ValueError("revision and state version must be positive")


@dataclass(frozen=True)
class TransitionDecision:
    action: DecisionAction
    transition_id: str | None
    to_phase: SetupPhase | None
    events: tuple[EventType, ...] = ()
    reason_codes: tuple[str, ...] = ()
    continuity_status: ContinuityStatus | None = None
    breakout_ordinal: int | None = None
    revise_tracking_pivot: bool = False
    distribution_eligible: bool | None = None
    route: str | None = None

    @property
    def rejected(self) -> bool:
        return self.action in (DecisionAction.REJECT, DecisionAction.QUARANTINE, DecisionAction.REPLAY)


@dataclass(frozen=True)
class PivotRevision:
    core_setup_uid: str
    revision_no: int
    pivot: PivotFact
    effective_observation_uid: str
    valid_from_market_session_index: int
    frozen_after_breakout: bool

    def __post_init__(self) -> None:
        if not self.core_setup_uid or not self.effective_observation_uid:
            raise ValueError("pivot revision identifiers are required")
        if self.revision_no < 1 or self.valid_from_market_session_index < 0:
            raise ValueError("pivot revision indexes are invalid")


@dataclass(frozen=True)
class SetupEvent:
    event_uid: str
    event_type: EventType
    core_setup_uid: str
    effective_date: str
    detected_at: str
    observation_uid: str | None
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not all((self.event_uid, self.core_setup_uid, self.evidence_sha256)):
            raise ValueError("event identifiers and evidence hash are required")
        validate_iso_date(self.effective_date, "event effective date")
        validate_iso_datetime(self.detected_at, "event detected_at")


SetupState = SetupStateRecord
