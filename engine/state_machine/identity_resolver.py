"""Pure, conservative setup identity resolution."""

from __future__ import annotations

from collections.abc import Sequence

from .config import DEFAULT_THRESHOLDS, StateMachineThresholds
from .guards import candidate_gate
from .models import (
    CoreObservation,
    IdentityCandidate,
    IdentityDecision,
    IdentityResolution,
    ObservationStatus,
    StateStageBlocker,
)


def resolve_identity(
    observation: CoreObservation,
    candidates: Sequence[IdentityCandidate],
    *,
    ledger_available: bool = True,
    explicit_slot_mapping: str | None = None,
    scope_returned: bool = False,
    lineage_gap: bool = False,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
) -> IdentityResolution:
    if observation.status != ObservationStatus.CURRENT:
        return IdentityResolution(None, None, (observation.status.value,))
    if not ledger_available:
        return IdentityResolution(
            None, None, ("IDENTITY_LEDGER_UNAVAILABLE",),
            StateStageBlocker.LEDGER_UNAVAILABLE,
        )

    same_code = [candidate for candidate in candidates if candidate.code == observation.code]
    if explicit_slot_mapping is not None:
        matching = [c for c in same_code if c.core_setup_uid == explicit_slot_mapping]
        if len(matching) == 1:
            return IdentityResolution(
                IdentityDecision.LINK, matching[0].core_setup_uid,
                ("EXPLICIT_DURABLE_SLOT_MAPPING",),
            )
        return IdentityResolution(
            IdentityDecision.AMBIGUOUS, None,
            ("INVALID_EXPLICIT_SLOT_MAPPING",), StateStageBlocker.IDENTITY_AMBIGUOUS,
        )

    gap_exceeded = any(
        observation.market_session_index - c.last_accepted_market_session_index
        > thresholds.auto_link_max_gap_sessions
        for c in same_code
    )
    # A short, evidenced scope pause may resume (the fixed fixture uses a
    # four-session return).  A return only blocks automatic linkage when the
    # continuity window is exceeded or the lineage itself has a hole.
    if lineage_gap or gap_exceeded or (scope_returned and gap_exceeded):
        return IdentityResolution(
            IdentityDecision.AMBIGUOUS, None,
            ("AUTO_LINK_GAP_EXCEEDED",), StateStageBlocker.IDENTITY_AMBIGUOUS,
        )
    if len(same_code) == 1:
        return IdentityResolution(
            IdentityDecision.LINK, same_code[0].core_setup_uid,
            ("SINGLE_NONTERMINAL_SETUP_WITHIN_GAP_LIMIT",),
        )
    if len(same_code) > 1:
        return IdentityResolution(
            IdentityDecision.AMBIGUOUS, None,
            ("MULTIPLE_ACTIVE_SETUP_CANDIDATES",), StateStageBlocker.IDENTITY_AMBIGUOUS,
        )
    if candidate_gate(observation, thresholds):
        return IdentityResolution(
            IdentityDecision.MINT, None, ("NEW_CORE_SETUP",),
        )
    return IdentityResolution(
        IdentityDecision.NO_SETUP, None, ("CORE_CANDIDATE_GATE_NOT_MET",),
    )
