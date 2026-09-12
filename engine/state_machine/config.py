"""Versioned Phase 2A thresholds."""

from __future__ import annotations

from dataclasses import dataclass


STATE_MACHINE_VERSION = "sm1"
THRESHOLD_VERSION = "smt1"
IDENTITY_VERSION = "csu1"
IDENTITY_DECISION_RULE_VERSION = "idr1"
OBSERVATION_SCHEMA_VERSION = "smo1"
EVENT_SCHEMA_VERSION = "sev1"
SCHEMA_VERSION = "phase2a-schema-v1"


@dataclass(frozen=True)
class StateMachineThresholds:
    failure_below_tracking_pivot_pct: float = 3.0
    retry_watch_distance_pct: float = 3.0
    candidate_min_aligned_strategies: int = 2
    breakout_min_strategies: int = 2
    pre_breakout_max_sessions: int = 90
    post_breakout_max_sessions: int = 20
    auto_link_max_gap_sessions: int = 5


DEFAULT_THRESHOLDS = StateMachineThresholds()
