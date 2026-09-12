"""Phase 2A event-centered setup state machine.

The package is intentionally not imported by the product export path.  Callers
must opt in and provide an explicit SQLite path.
"""

from .config import DEFAULT_THRESHOLDS, STATE_MACHINE_VERSION, THRESHOLD_VERSION
from .models import (
    ContinuityStatus,
    CoreObservation,
    CoverageStatus,
    DecisionAction,
    EventType,
    IdentityDecision,
    IdentityContext,
    ObservationStatus,
    PivotRevision,
    PublishEligibility,
    RunContext,
    RunStatus,
    ScopeMemberFact,
    SetupEvent,
    SetupPhase,
    SetupState,
    SetupStateRecord,
    TransitionDecision,
)

__all__ = [
    "ContinuityStatus",
    "CoreObservation",
    "CoverageStatus",
    "DEFAULT_THRESHOLDS",
    "DecisionAction",
    "EventType",
    "IdentityDecision",
    "IdentityContext",
    "ObservationStatus",
    "PivotRevision",
    "PublishEligibility",
    "RunContext",
    "RunStatus",
    "ScopeMemberFact",
    "STATE_MACHINE_VERSION",
    "SetupPhase",
    "SetupEvent",
    "SetupState",
    "SetupStateRecord",
    "THRESHOLD_VERSION",
    "TransitionDecision",
]
