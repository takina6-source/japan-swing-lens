"""Pure guards for the Phase 2A state machine."""

from __future__ import annotations

import math

from .config import DEFAULT_THRESHOLDS, StateMachineThresholds
from .models import CoreObservation, ObservationStatus, PivotFact


TREND_CORE_STATES = frozenset({"SETUP FORMING", "BREAKOUT WATCH", "BREAKOUT"})


def positive_finite(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def candidate_gate(
    observation: CoreObservation,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
) -> bool:
    return (
        observation.status == ObservationStatus.CURRENT
        and observation.core_observed_state in TREND_CORE_STATES
        and (observation.aligned_trend_strategy_count or 0)
        >= thresholds.candidate_min_aligned_strategies
        and positive_finite(observation.close)
        and observation.observed_primary_pivot is not None
        and positive_finite(observation.observed_primary_pivot.price)
    )


def qualified_cross(
    previous_close: float | None,
    close: float | None,
    tracking_pivot: float | None,
    breakout_strategy_count: int | None,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
) -> bool:
    return (
        positive_finite(previous_close)
        and positive_finite(close)
        and positive_finite(tracking_pivot)
        and float(previous_close) <= float(tracking_pivot) < float(close)
        and (breakout_strategy_count or 0) >= thresholds.breakout_min_strategies
    )


def failed_breach(
    close: float | None,
    tracking_pivot: float | None,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
) -> bool:
    return (
        positive_finite(close)
        and positive_finite(tracking_pivot)
        and float(close)
        < float(tracking_pivot) * (1.0 - thresholds.failure_below_tracking_pivot_pct / 100.0)
    )


def retry_zone(
    close: float | None,
    tracking_pivot: float | None,
    aligned_strategy_count: int | None,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
) -> bool:
    if not positive_finite(close) or not positive_finite(tracking_pivot):
        return False
    lower = float(tracking_pivot) * (1.0 - thresholds.retry_watch_distance_pct / 100.0)
    return (
        lower <= float(close) <= float(tracking_pivot)
        and (aligned_strategy_count or 0) >= thresholds.candidate_min_aligned_strategies
    )


def pre_breakout_expired(
    current_session: int,
    minted_session: int,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
) -> bool:
    return current_session - minted_session > thresholds.pre_breakout_max_sessions


def post_breakout_expired(
    current_session: int,
    latest_breakout_session: int | None,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
) -> bool:
    return (
        latest_breakout_session is not None
        and current_session - latest_breakout_session > thresholds.post_breakout_max_sessions
    )


def auto_link_gap_allowed(
    current_session: int,
    previous_session: int,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
) -> bool:
    gap = current_session - previous_session
    return 0 <= gap <= thresholds.auto_link_max_gap_sessions


def pivot_changed(observed: PivotFact | None, tracking: PivotFact) -> bool:
    return observed is not None and observed.signature != tracking.signature
