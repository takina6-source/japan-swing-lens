"""Deterministic T01-T26 transition evaluator."""

from __future__ import annotations

from .config import DEFAULT_THRESHOLDS, STATE_MACHINE_VERSION, StateMachineThresholds
from .guards import (
    candidate_gate,
    failed_breach,
    pivot_changed,
    post_breakout_expired,
    pre_breakout_expired,
    qualified_cross,
    retry_zone,
)
from .models import (
    ContinuityStatus,
    CoreObservation,
    DecisionAction,
    EventType,
    IdentityDecision,
    IdentityResolution,
    ObservationStatus,
    PermanentExitEvidence,
    SetupPhase,
    SetupStateRecord,
    StateStageBlocker,
    TransitionDecision,
)


def _decision(
    action: DecisionAction,
    transition_id: str | None,
    phase: SetupPhase | None,
    events: tuple[EventType, ...] = (),
    reasons: tuple[str, ...] = (),
    *,
    continuity: ContinuityStatus | None = None,
    breakout_ordinal: int | None = None,
    revise_pivot: bool = False,
    distribution_eligible: bool | None = None,
    route: str | None = None,
) -> TransitionDecision:
    return TransitionDecision(
        action=action,
        transition_id=transition_id,
        to_phase=phase,
        events=events,
        reason_codes=reasons,
        continuity_status=continuity,
        breakout_ordinal=breakout_ordinal,
        revise_tracking_pivot=revise_pivot,
        distribution_eligible=distribution_eligible,
        route=route,
    )


def _with_continuity_resumed(
    result: TransitionDecision,
    state: SetupStateRecord,
    observation: CoreObservation,
    thresholds: StateMachineThresholds,
) -> TransitionDecision:
    if state.continuity_status == ContinuityStatus.CONTIGUOUS:
        return result
    gap = observation.market_session_index - state.latest_accepted_market_session_index
    if not 0 <= gap <= thresholds.auto_link_max_gap_sessions:
        return result
    events = result.events
    if EventType.CONTINUITY_RESUMED not in events:
        events = events + (EventType.CONTINUITY_RESUMED,)
    return TransitionDecision(
        action=result.action,
        transition_id=result.transition_id,
        to_phase=result.to_phase,
        events=events,
        reason_codes=result.reason_codes + ("CONTINUITY_RESUMED_WITHIN_GAP_LIMIT",),
        continuity_status=ContinuityStatus.CONTIGUOUS,
        breakout_ordinal=result.breakout_ordinal,
        revise_tracking_pivot=result.revise_tracking_pivot,
        distribution_eligible=result.distribution_eligible,
        route=result.route,
    )


def _with_reason(result: TransitionDecision, reason: str) -> TransitionDecision:
    if reason in result.reason_codes:
        return result
    return TransitionDecision(
        action=result.action,
        transition_id=result.transition_id,
        to_phase=result.to_phase,
        events=result.events,
        reason_codes=result.reason_codes + (reason,),
        continuity_status=result.continuity_status,
        breakout_ordinal=result.breakout_ordinal,
        revise_tracking_pivot=result.revise_tracking_pivot,
        distribution_eligible=result.distribution_eligible,
        route=result.route,
    )


def evaluate_transition(
    observation: CoreObservation,
    identity: IdentityResolution,
    state: SetupStateRecord | None,
    *,
    input_state_machine_version: str = STATE_MACHINE_VERSION,
    cutover_initialization: bool = False,
    permanent_exit_evidence: PermanentExitEvidence | None = None,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
) -> TransitionDecision:
    """Evaluate one observation without I/O or hidden time.

    The returned events are requests. Persistence assigns deterministic event
    UIDs and commits them with the state update.
    """

    # T25: version checks precede market rules.
    if input_state_machine_version != STATE_MACHINE_VERSION or (
        state is not None and state.state_machine_version != input_state_machine_version
    ):
        return _decision(
            DecisionAction.REJECT, "T25", state.phase if state else None,
            reasons=("STATE_MACHINE_VERSION_MISMATCH",), route="shadow_lineage",
        )

    if state is not None and observation.analysis_date is not None:
        if observation.analysis_date < state.latest_accepted_date:
            # T20: never mutate live state; caller may explicitly replay elsewhere.
            return _decision(
                DecisionAction.REJECT, "T20", state.phase,
                reasons=("OUT_OF_ORDER_OBSERVATION",), route="replay_namespace",
            )
        if observation.analysis_date == state.latest_accepted_date:
            if observation.input_sha256 and observation.input_sha256 == state.latest_input_sha256:
                return _decision(
                    DecisionAction.KEEP, "T21", state.phase,
                    reasons=("IDEMPOTENT_REPLAY",),
                    continuity=state.continuity_status,
                    distribution_eligible=state.distribution_eligible,
                )
            return _decision(
                DecisionAction.QUARANTINE, "T22", state.phase,
                reasons=("SAME_DATE_INPUT_CONFLICT",), route="quarantine",
            )

    # T18: terminal setup identities cannot be reopened.
    if state is not None and state.phase.terminal:
        return _decision(
            DecisionAction.REJECT, "T18", state.phase,
            reasons=("TERMINAL_SETUP_UID",),
            continuity=ContinuityStatus.TERMINAL,
            distribution_eligible=False,
        )

    # T26: only structured permanent evidence can close a nonterminal setup.
    if state is not None and permanent_exit_evidence is not None:
        return _decision(
            DecisionAction.TRANSITION, "T26", SetupPhase.CLOSED,
            (EventType.SETUP_CLOSED,),
            ("PERMANENT_SCOPE_EXIT_CONFIRMED", permanent_exit_evidence.evidence_type.value),
            continuity=ContinuityStatus.TERMINAL,
            distribution_eligible=False,
        )

    # T19: market-quality gaps never advance phase.
    if observation.status != ObservationStatus.CURRENT:
        return _decision(
            DecisionAction.KEEP, "T19", state.phase if state else None,
            reasons=observation.reason_codes or (observation.status.value,),
            continuity=ContinuityStatus.PAUSED if state else None,
            distribution_eligible=False if observation.status == ObservationStatus.OUT_OF_SCOPE else None,
        )

    if identity.blocker == StateStageBlocker.LEDGER_UNAVAILABLE:
        return _decision(
            DecisionAction.REJECT, None, state.phase if state else None,
            reasons=identity.reason_codes, route="failed_run",
        )
    if identity.decision == IdentityDecision.AMBIGUOUS:
        continuity = (
            ContinuityStatus.SUSPENDED_GAP
            if "AUTO_LINK_GAP_EXCEEDED" in identity.reason_codes
            else ContinuityStatus.PAUSED
        )
        return _decision(
            DecisionAction.KEEP, "T17", state.phase if state else None,
            reasons=identity.reason_codes,
            continuity=continuity,
            distribution_eligible=False,
        )
    if identity.decision == IdentityDecision.NO_SETUP:
        return _decision(
            DecisionAction.KEEP, "T17", state.phase if state else None,
            reasons=identity.reason_codes,
            continuity=state.continuity_status if state else None,
            distribution_eligible=False if state else None,
        )

    if state is None:
        if identity.decision != IdentityDecision.MINT:
            return _decision(
                DecisionAction.REJECT, None, None,
                reasons=("LINK_TARGET_STATE_NOT_FOUND",),
            )
        if not candidate_gate(observation, thresholds):
            return _decision(
                DecisionAction.REJECT, None, None,
                reasons=("MINT_CANDIDATE_GATE_NOT_MET",),
            )
        if observation.core_observed_state == "BREAKOUT":
            proven_cross = qualified_cross(
                observation.previous_accepted_close,
                observation.close,
                observation.observed_primary_pivot.price,
                observation.breakout_trend_strategy_count,
                thresholds,
            )
            if proven_cross:
                return _decision(
                    DecisionAction.TRANSITION, "T03", SetupPhase.POST_BREAKOUT,
                    (EventType.SETUP_MINTED, EventType.BREAKOUT_CONFIRMED),
                    ("NEW_SETUP_INITIAL_BREAKOUT",),
                    continuity=ContinuityStatus.CONTIGUOUS,
                    breakout_ordinal=1,
                    distribution_eligible=True,
                )
            if cutover_initialization:
                return _decision(
                    DecisionAction.TRANSITION, "T04", SetupPhase.POST_BREAKOUT,
                    (EventType.SETUP_MINTED, EventType.BOOTSTRAP_OBSERVED),
                    ("CUTOVER_POST_BREAKOUT_UNPROVEN",),
                    continuity=ContinuityStatus.CONTIGUOUS,
                    breakout_ordinal=1,
                    distribution_eligible=True,
                )
            return _decision(
                DecisionAction.REJECT, None, None,
                reasons=("UNPROVEN_INITIAL_BREAKOUT_OUTSIDE_CUTOVER",),
            )
        if observation.core_observed_state == "BREAKOUT WATCH":
            return _decision(
                DecisionAction.TRANSITION, "T02", SetupPhase.WATCH,
                (EventType.SETUP_MINTED,), ("NEW_CORE_SETUP_WATCH",),
                continuity=ContinuityStatus.CONTIGUOUS,
                distribution_eligible=True,
            )
        return _decision(
            DecisionAction.TRANSITION, "T01", SetupPhase.FORMING,
            (EventType.SETUP_MINTED,), ("NEW_CORE_SETUP",),
            continuity=ContinuityStatus.CONTIGUOUS,
            distribution_eligible=True,
        )

    if identity.decision != IdentityDecision.LINK:
        return _decision(
            DecisionAction.REJECT, None, state.phase,
            reasons=("EXISTING_STATE_REQUIRES_LINK",),
        )
    if identity.target_core_setup_uid != state.core_setup_uid:
        return _decision(
            DecisionAction.REJECT, None, state.phase,
            reasons=("LINK_TARGET_MISMATCH",),
        )

    pivot_price = state.tracking_pivot.price
    is_cross = qualified_cross(
        observation.previous_accepted_close,
        observation.close,
        pivot_price,
        observation.breakout_trend_strategy_count,
        thresholds,
    )
    if state.phase in (SetupPhase.FORMING, SetupPhase.WATCH, SetupPhase.FAILED, SetupPhase.RETRY_WATCH) and is_cross:
        if state.breakout_count == 0:
            result = _decision(
                DecisionAction.TRANSITION, "T06", SetupPhase.POST_BREAKOUT,
                (EventType.BREAKOUT_CONFIRMED,), ("INITIAL_QUALIFIED_CROSS",),
                continuity=ContinuityStatus.CONTIGUOUS,
                breakout_ordinal=1,
                distribution_eligible=True,
            )
        else:
            transition_id = "T12" if state.phase == SetupPhase.FAILED else "T13"
            result = _decision(
                DecisionAction.TRANSITION, transition_id, SetupPhase.POST_BREAKOUT,
                (EventType.REBREAKOUT_CONFIRMED,), ("QUALIFIED_REBREAKOUT",),
                continuity=ContinuityStatus.CONTIGUOUS,
                breakout_ordinal=state.breakout_count + 1,
                distribution_eligible=True,
            )
        if (
            state.phase in (SetupPhase.FORMING, SetupPhase.WATCH)
            and pre_breakout_expired(
                observation.market_session_index,
                state.minted_market_session_index,
                thresholds,
            )
        ) or (
            state.phase in (SetupPhase.FAILED, SetupPhase.RETRY_WATCH)
            and post_breakout_expired(
                observation.market_session_index,
                state.latest_breakout_market_session_index,
                thresholds,
            )
        ):
            result = _with_reason(result, "QUALIFIED_CROSS_PRECEDES_EXPIRY")
        return _with_continuity_resumed(result, state, observation, thresholds)

    # Cross/rebreakout has priority on the first over-limit CURRENT observation.
    if state.phase in (SetupPhase.FORMING, SetupPhase.WATCH) and pre_breakout_expired(
        observation.market_session_index, state.minted_market_session_index, thresholds
    ):
        return _decision(
            DecisionAction.TRANSITION, "T08", SetupPhase.EXPIRED,
            (EventType.SETUP_EXPIRED,), ("PRE_BREAKOUT_SESSION_LIMIT",),
            continuity=ContinuityStatus.TERMINAL,
            distribution_eligible=False,
        )
    if state.phase in (SetupPhase.POST_BREAKOUT, SetupPhase.FAILED, SetupPhase.RETRY_WATCH) and post_breakout_expired(
        observation.market_session_index, state.latest_breakout_market_session_index, thresholds
    ):
        return _decision(
            DecisionAction.TRANSITION, "T16", SetupPhase.EXPIRED,
            (EventType.SETUP_EXPIRED,), ("POST_BREAKOUT_SESSION_LIMIT",),
            continuity=ContinuityStatus.TERMINAL,
            distribution_eligible=False,
        )

    if state.phase == SetupPhase.POST_BREAKOUT and failed_breach(
        observation.close, pivot_price, thresholds
    ):
        result = _decision(
            DecisionAction.TRANSITION, "T09", SetupPhase.FAILED,
            (EventType.FAILED_CONFIRMED,), ("TRACKING_PIVOT_BREACH_GT_3PCT",),
            continuity=ContinuityStatus.CONTIGUOUS,
            distribution_eligible=True,
        )
        return _with_continuity_resumed(result, state, observation, thresholds)

    if state.phase == SetupPhase.FAILED and retry_zone(
        observation.close, pivot_price, observation.aligned_trend_strategy_count, thresholds
    ):
        result = _decision(
            DecisionAction.TRANSITION, "T11", SetupPhase.RETRY_WATCH,
            (EventType.RETRY_WATCH_ENTERED,), ("RECOVERED_TO_RETRY_ZONE",),
            continuity=ContinuityStatus.CONTIGUOUS,
            distribution_eligible=True,
        )
        return _with_continuity_resumed(result, state, observation, thresholds)

    if state.phase == SetupPhase.RETRY_WATCH:
        if failed_breach(observation.close, pivot_price, thresholds):
            result = _decision(
                DecisionAction.TRANSITION, "T14", SetupPhase.FAILED,
                reasons=("RETRY_LOST_BELOW_FAILURE_LINE",),
                continuity=ContinuityStatus.CONTIGUOUS,
                distribution_eligible=True,
            )
            return _with_continuity_resumed(result, state, observation, thresholds)
        if not retry_zone(
            observation.close, pivot_price, observation.aligned_trend_strategy_count, thresholds
        ):
            result = _decision(
                DecisionAction.TRANSITION, "T15", SetupPhase.FAILED,
                reasons=("RETRY_CONDITION_LOST",),
                continuity=ContinuityStatus.CONTIGUOUS,
                distribution_eligible=True,
            )
            return _with_continuity_resumed(result, state, observation, thresholds)

    if state.phase == SetupPhase.FORMING and observation.core_observed_state == "BREAKOUT WATCH":
        result = _decision(
            DecisionAction.TRANSITION, "T05", SetupPhase.WATCH,
            (EventType.WATCH_ENTERED,), ("CORE_WATCH_OBSERVED",),
            continuity=ContinuityStatus.CONTIGUOUS,
            distribution_eligible=True,
        )
        return _with_continuity_resumed(result, state, observation, thresholds)

    if state.phase == SetupPhase.WATCH and observation.core_observed_state == "SETUP FORMING":
        result = _decision(
            DecisionAction.TRANSITION, "T07", SetupPhase.FORMING,
            reasons=("MOVED_AWAY_FROM_WATCH",),
            continuity=ContinuityStatus.CONTIGUOUS,
            distribution_eligible=True,
        )
        return _with_continuity_resumed(result, state, observation, thresholds)

    changed = pivot_changed(observation.observed_primary_pivot, state.tracking_pivot)
    if state.phase in (SetupPhase.FORMING, SetupPhase.WATCH) and changed:
        result = _decision(
            DecisionAction.KEEP, "T23", state.phase,
            (EventType.PIVOT_REVISED,), ("PRE_BREAKOUT_PIVOT_REVISION",),
            continuity=ContinuityStatus.CONTIGUOUS,
            revise_pivot=True,
            distribution_eligible=True,
        )
        return _with_continuity_resumed(result, state, observation, thresholds)
    if state.phase in (SetupPhase.POST_BREAKOUT, SetupPhase.FAILED, SetupPhase.RETRY_WATCH) and changed:
        result = _decision(
            DecisionAction.KEEP, "T24", state.phase,
            reasons=("TRACKING_PIVOT_FROZEN_AFTER_BREAKOUT",),
            continuity=ContinuityStatus.CONTIGUOUS,
            distribution_eligible=True,
        )
        return _with_continuity_resumed(result, state, observation, thresholds)
    if state.phase == SetupPhase.POST_BREAKOUT:
        result = _decision(
            DecisionAction.KEEP, "T10", SetupPhase.POST_BREAKOUT,
            reasons=("POST_BREAKOUT_CONTINUES",),
            continuity=ContinuityStatus.CONTIGUOUS,
            distribution_eligible=True,
        )
        return _with_continuity_resumed(result, state, observation, thresholds)

    result = _decision(
        DecisionAction.KEEP, "T17", state.phase,
        reasons=("NO_PHASE_CHANGE",),
        continuity=ContinuityStatus.CONTIGUOUS,
        distribution_eligible=True,
    )
    return _with_continuity_resumed(result, state, observation, thresholds)
