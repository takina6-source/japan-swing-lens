"""Phase 2A orchestration: quality -> identity -> transition -> atomic commit."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any, Mapping

from .adapter import ACTIVE_TREND_STATES, AdaptedRun
from .config import (
    EVENT_SCHEMA_VERSION,
    IDENTITY_DECISION_RULE_VERSION,
    IDENTITY_VERSION,
    OBSERVATION_SCHEMA_VERSION,
    STATE_MACHINE_VERSION,
    THRESHOLD_VERSION,
)
from .identity_resolver import resolve_identity
from .ids import (
    canonical_json,
    decision_uid,
    event_uid,
    hash_payload,
    mint_core_setup_uid,
    mint_strategy_setup_uid,
    observation_uid,
)
from .models import (
    ContinuityStatus,
    CoreObservation,
    CoverageStatus,
    DecisionAction,
    EventType,
    IdentityDecision,
    IdentityResolution,
    ObservationStatus,
    PermanentExitEvidence,
    PublishEligibility,
    RunStatus,
    SetupPhase,
    SetupStateRecord,
)
from .storage import Phase2ASeedError, Phase2AStore, RunBundle
from .transitions import evaluate_transition


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PHASE_EVENT_TYPES = frozenset({
    EventType.SETUP_MINTED,
    EventType.BOOTSTRAP_OBSERVED,
    EventType.WATCH_ENTERED,
    EventType.BREAKOUT_CONFIRMED,
    EventType.FAILED_CONFIRMED,
    EventType.RETRY_WATCH_ENTERED,
    EventType.REBREAKOUT_CONFIRMED,
    EventType.SETUP_EXPIRED,
    EventType.SETUP_CLOSED,
})


def _json(value: Any) -> str:
    return canonical_json(value)


def _scope_status_counts(adapted: AdaptedRun) -> dict[str, int]:
    counts = {status.value: 0 for status in ObservationStatus}
    for row in adapted.scope_members:
        if row["in_scope"]:
            counts[row["observation_status"]] += 1
        elif row["observation_status"] == ObservationStatus.OUT_OF_SCOPE.value:
            counts[ObservationStatus.OUT_OF_SCOPE.value] += 1
    return counts


def _quality(
    adapted: AdaptedRun, identity_counts: Mapping[str, int]
) -> tuple[RunStatus, CoverageStatus, PublishEligibility, tuple[str, ...]]:
    counts = _scope_status_counts(adapted)
    unknown_quality = (
        counts[ObservationStatus.STALE_MARKET_DATE.value]
        + counts[ObservationStatus.FETCH_FAILED.value]
        + counts[ObservationStatus.ANALYSIS_FAILED.value]
        + counts[ObservationStatus.INVALID_INPUT.value]
    )
    reasons: list[str] = []
    if unknown_quality:
        reasons.append("UNRESOLVED_OBSERVATION_QUALITY")
    if identity_counts.get(IdentityDecision.AMBIGUOUS.value, 0):
        reasons.append("IDENTITY_AMBIGUOUS_PRESENT")
    if adapted.upstream_errors:
        reasons.append("UPSTREAM_STRUCTURED_ERRORS_PRESENT")
    partial = bool(unknown_quality or adapted.upstream_errors or reasons)
    if partial:
        return (
            RunStatus.PARTIAL, CoverageStatus.UNKNOWN,
            PublishEligibility.ELIGIBLE_DEGRADED, tuple(sorted(set(reasons))),
        )
    if counts[ObservationStatus.INSUFFICIENT_PRICE_HISTORY.value] or counts[
        ObservationStatus.NO_NEW_MARKET_OBSERVATION.value
    ]:
        return (
            RunStatus.COMPLETE, CoverageStatus.DEGRADED,
            PublishEligibility.ELIGIBLE, (),
        )
    return RunStatus.COMPLETE, CoverageStatus.FULL, PublishEligibility.ELIGIBLE, ()


def _observation_row(
    observation: CoreObservation, run_id: str, uid: str, core_setup_uid: str | None,
    tracking_revision: int | None,
) -> dict[str, Any]:
    pivot = observation.observed_primary_pivot
    assert pivot is not None and observation.analysis_date is not None
    return {
        "observation_uid": uid, "run_id": run_id, "namespace": "CORE",
        "code": observation.code, "analysis_date": observation.analysis_date,
        "expected_market_date": observation.expected_market_date,
        "market_session_index": observation.market_session_index,
        "observed_at": observation.observed_at,
        "close": observation.close,
        "previous_accepted_close": observation.previous_accepted_close,
        "core_observed_state": observation.core_observed_state,
        "trend_strategy_states_json": _json(observation.trend_strategy_states),
        "connors_state": observation.connors_state,
        "aligned_trend_strategy_count": observation.aligned_trend_strategy_count,
        "breakout_trend_strategy_count": observation.breakout_trend_strategy_count,
        "observed_pivot_price": pivot.price,
        "observed_pivot_strategy": pivot.strategy,
        "observed_pivot_type": pivot.pivot_type,
        "observed_pivot_basis": pivot.basis,
        "observed_pivot_fidelity": pivot.fidelity,
        "observed_pivot_reference_date": pivot.reference_date,
        "observation_status": ObservationStatus.CURRENT.value,
        "decision_slot": "core-primary", "core_setup_uid": core_setup_uid,
        "tracking_pivot_revision_no": tracking_revision,
        "source_versions_json": _json(observation.source_versions),
        "legacy_refs_json": _json(observation.legacy_refs),
        "reason_codes_json": _json(observation.reason_codes),
        "input_sha256": observation.input_sha256,
    }


def _event_ordinal(
    event_type: EventType, state: SetupStateRecord | None,
    breakout_ordinal: int | None, pivot_revision: int,
) -> int:
    if event_type in (EventType.BREAKOUT_CONFIRMED, EventType.REBREAKOUT_CONFIRMED):
        return breakout_ordinal or 1
    if event_type == EventType.FAILED_CONFIRMED:
        return (state.failure_cycle_no if state else 0) + 1
    if event_type == EventType.RETRY_WATCH_ENTERED:
        return max(1, state.failure_cycle_no if state else 1)
    if event_type == EventType.PIVOT_REVISED:
        return pivot_revision
    if event_type == EventType.CONTINUITY_RESUMED:
        return (state.state_version if state else 0) + 1
    return 1


def _event_rows(
    *, run_id: str, observed_at: str, observation: CoreObservation,
    observation_id: str | None, core_setup_uid: str, state: SetupStateRecord | None,
    transition: Any, tracking_revision: int,
    permanent_exit_evidence: PermanentExitEvidence | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    prior_uid = state.latest_event_uid if state else None
    from_phase = state.phase.value if state else None
    for kind in transition.events:
        ordinal = _event_ordinal(
            kind, state, transition.breakout_ordinal, tracking_revision
        )
        uid, request_hash = event_uid(
            code=observation.code, core_setup_uid=core_setup_uid,
            event_type=kind.value, observation_uid_value=observation_id,
            state_machine_version=STATE_MACHINE_VERSION, occurrence_ordinal=ordinal,
        )
        effective_date = (
            permanent_exit_evidence.effective_date
            if kind == EventType.SETUP_CLOSED and permanent_exit_evidence
            else observation.analysis_date or observation.expected_market_date
        )
        reason_codes = list(transition.reason_codes)
        evidence = {
            "event_type": kind.value, "core_setup_uid": core_setup_uid,
            "observation_uid": observation_id,
            "permanent_exit_evidence_ref": (
                permanent_exit_evidence.evidence_ref if permanent_exit_evidence else None
            ),
            "tracking_pivot_revision_no": tracking_revision,
            "transition_id": transition.transition_id,
            "reason_codes": reason_codes,
        }
        evidence_hash = hash_payload(evidence)
        is_transition = int(kind in PHASE_EVENT_TYPES)
        row = {
            "event_uid": uid, "event_request_sha256": request_hash,
            "event_type": kind.value, "occurrence_ordinal": ordinal,
            "core_setup_uid": core_setup_uid, "from_phase": from_phase,
            "to_phase": transition.to_phase.value,
            "is_phase_transition": is_transition,
            "effective_date": effective_date,
            "effective_date_status": (
                "MASTER_EVIDENCE_DATE" if kind == EventType.SETUP_CLOSED
                else "EXACT_CURRENT_OBSERVATION"
            ),
            "detected_at": observed_at, "observation_uid": observation_id,
            "permanent_exit_evidence_ref": (
                permanent_exit_evidence.evidence_ref if permanent_exit_evidence else None
            ),
            "tracking_pivot_revision_no": tracking_revision,
            "prior_related_event_uid": prior_uid,
            "state_machine_version": STATE_MACHINE_VERSION,
            "threshold_version": THRESHOLD_VERSION,
            "transition_id": transition.transition_id,
            "reason_codes_json": _json(reason_codes), "evidence_sha256": evidence_hash,
            "idempotency_key": (
                f"{STATE_MACHINE_VERSION}|{core_setup_uid}|{kind.value}|"
                f"{observation_id or permanent_exit_evidence.evidence_ref}|{ordinal}"
            ),
            "producer_run_id": run_id, "corrects_event_uid": None,
        }
        rows.append(row)
        prior_uid = uid
        anchor_type = {
            EventType.SETUP_MINTED: "MINT",
            EventType.BOOTSTRAP_OBSERVED: "BREAKOUT",
            EventType.BREAKOUT_CONFIRMED: "BREAKOUT",
            EventType.REBREAKOUT_CONFIRMED: "BREAKOUT",
            EventType.FAILED_CONFIRMED: "FAILURE",
            EventType.SETUP_EXPIRED: "TERMINAL",
            EventType.SETUP_CLOSED: "TERMINAL",
        }.get(kind)
        if anchor_type:
            anchors.append({
                "core_setup_uid": core_setup_uid, "anchor_type": anchor_type,
                "event_uid": uid, "effective_date": effective_date,
                "market_session_index": observation.market_session_index,
                "occurrence_ordinal": ordinal, "evidence_sha256": evidence_hash,
            })
    return rows, anchors


def _state_row(
    *, lineage: str, run_id: str, observation: CoreObservation,
    observation_id: str | None, core_setup_uid: str,
    state: SetupStateRecord | None, transition: Any,
    tracking_revision: int, event_rows: list[dict[str, Any]],
    permanent_exit_evidence: PermanentExitEvidence | None,
) -> dict[str, Any]:
    phase = transition.to_phase
    assert phase is not None
    phase_changed = state is None or phase != state.phase
    latest_event_uid = event_rows[-1]["event_uid"] if event_rows else (
        state.latest_event_uid if state else None
    )
    latest_breakout_uid = state.latest_breakout_event_uid if state else None
    latest_failure_uid = state.latest_failure_event_uid if state else None
    breakout_count = state.breakout_count if state else 0
    failure_cycle = state.failure_cycle_no if state else 0
    latest_breakout_session = state.latest_breakout_market_session_index if state else None
    for event in event_rows:
        if event["event_type"] in {
            EventType.BOOTSTRAP_OBSERVED.value,
            EventType.BREAKOUT_CONFIRMED.value,
            EventType.REBREAKOUT_CONFIRMED.value,
        }:
            latest_breakout_uid = event["event_uid"]
            latest_breakout_session = observation.market_session_index
            breakout_count = max(breakout_count, event["occurrence_ordinal"])
        if event["event_type"] == EventType.FAILED_CONFIRMED.value:
            latest_failure_uid = event["event_uid"]
            failure_cycle += 1
    accepted = observation.status == ObservationStatus.CURRENT and observation_id is not None
    return {
        "state_lineage": lineage, "core_setup_uid": core_setup_uid,
        "code": observation.code, "current_phase": phase.value,
        "continuity_status": (
            transition.continuity_status or (
                state.continuity_status if state else ContinuityStatus.CONTIGUOUS
            )
        ).value,
        "distribution_eligible": int(
            transition.distribution_eligible
            if transition.distribution_eligible is not None
            else (state.distribution_eligible if state else True)
        ),
        "phase_entered_observation_uid": (
            observation_id if phase_changed and accepted
            else (state.phase_entered_observation_uid if state else None)
        ),
        "phase_entered_effective_date": (
            (
                permanent_exit_evidence.effective_date
                if permanent_exit_evidence else observation.analysis_date
            )
            if phase_changed else (state.phase_entered_effective_date if state else observation.analysis_date)
        ),
        "latest_accepted_observation_uid": (
            observation_id if accepted else state.latest_accepted_observation_uid
        ),
        "latest_accepted_market_session_index": (
            observation.market_session_index if accepted
            else state.latest_accepted_market_session_index
        ),
        "latest_accepted_date": (
            observation.analysis_date if accepted else state.latest_accepted_date
        ),
        "latest_input_sha256": (
            observation.input_sha256 if accepted else state.latest_input_sha256
        ),
        "latest_accepted_close": (
            observation.close if accepted else state.latest_accepted_close
        ),
        "tracking_pivot_revision_no": tracking_revision,
        "latest_event_uid": latest_event_uid,
        "latest_breakout_event_uid": latest_breakout_uid,
        "latest_failure_event_uid": latest_failure_uid,
        "minted_market_session_index": (
            observation.market_session_index if state is None else state.minted_market_session_index
        ),
        "latest_breakout_market_session_index": latest_breakout_session,
        "breakout_count": breakout_count, "failure_cycle_no": failure_cycle,
        "closure_reason_code": (
            permanent_exit_evidence.evidence_type.value
            if permanent_exit_evidence else (state.closure_reason_code if state else None)
        ),
        "closure_evidence_ref": (
            permanent_exit_evidence.evidence_ref
            if permanent_exit_evidence else (state.closure_evidence_ref if state else None)
        ),
        "state_machine_version": STATE_MACHINE_VERSION,
        "threshold_version": THRESHOLD_VERSION,
        "state_version": 1 if state is None else state.state_version + 1,
        "updated_run_id": run_id,
        "expected_state_version": 0 if state is None else state.state_version,
    }


def _membership_changes(
    store: Phase2AStore, observation: CoreObservation, observation_id: str,
    core_setup_uid: str, identity_epoch: str, run_id: str, created_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    active = store.load_active_memberships(core_setup_uid)
    active_by_strategy = {row["strategy_code"]: row for row in active}
    desired = {
        strategy for strategy, state in observation.trend_strategy_states.items()
        if strategy.lower() != "connors" and state in ACTIVE_TREND_STATES
    }
    new_ledger: list[dict[str, Any]] = []
    new_memberships: list[dict[str, Any]] = []
    closures: list[dict[str, Any]] = []
    for strategy, row in active_by_strategy.items():
        if strategy not in desired:
            closures.append({
                "core_setup_uid": core_setup_uid, "strategy_code": strategy,
                "strategy_setup_uid": row["strategy_setup_uid"],
                "member_from_observation_uid": row["member_from_observation_uid"],
                "member_to_observation_uid": observation_id,
                "valid_to_market_session_index": observation.market_session_index,
            })
    for strategy in sorted(desired):
        if strategy in active_by_strategy:
            continue
        slug = strategy.lower().replace(" ", "_")
        strategy_uid, full_hash, canonical = mint_strategy_setup_uid(
            code=observation.code, strategy_slug=slug,
            identity_epoch=identity_epoch, origin_observation_uid=observation_id,
        )
        new_ledger.append({
            "strategy_setup_uid": strategy_uid, "code": observation.code,
            "strategy_code": strategy, "mint_request_sha256": full_hash,
            "canonical_mint_request": canonical, "identity_epoch": identity_epoch,
            "origin_observation_uid": observation_id, "identity_version": "ssu1",
            "created_run_id": run_id, "created_at": created_at,
        })
        evidence = hash_payload({
            "core_setup_uid": core_setup_uid, "strategy_setup_uid": strategy_uid,
            "observation_uid": observation_id, "action": "JOIN",
        })
        new_memberships.append({
            "core_setup_uid": core_setup_uid, "strategy_code": strategy,
            "strategy_setup_uid": strategy_uid,
            "member_from_observation_uid": observation_id,
            "member_to_observation_uid": None,
            "valid_from_market_session_index": observation.market_session_index,
            "valid_to_market_session_index": None, "evidence_sha256": evidence,
        })
    return new_ledger, new_memberships, closures


def build_run_bundle(
    store: Phase2AStore,
    adapted: AdaptedRun,
    *,
    identity_epoch: str,
    started_at: str,
    finished_at: str,
    initialize_cutover: bool = False,
    cutover_manifest_sha256: str | None = None,
    verified_seed_manifest: Mapping[str, Any] | None = None,
    permanent_exit_evidence_by_code: Mapping[str, PermanentExitEvidence] | None = None,
) -> RunBundle:
    if not store.schema_verified():
        raise Phase2ASeedError("Phase 2A schema must be explicitly migrated first")
    counts_before = store.table_counts()
    ledger_empty = counts_before["phase2a_identity_ledger"] == 0
    if ledger_empty and not initialize_cutover:
        raise Phase2ASeedError("verified seed or explicit cutover initialization is required")
    if initialize_cutover and not (
        cutover_manifest_sha256 and SHA256_RE.fullmatch(cutover_manifest_sha256)
    ):
        raise Phase2ASeedError("explicit cutover requires a 64-character manifest SHA-256")
    if initialize_cutover and not ledger_empty and not store.has_committed_run(
        adapted.run_id, adapted.input_sha256
    ):
        raise Phase2ASeedError("cutover initialization requires an empty identity ledger")

    exit_evidence = permanent_exit_evidence_by_code or {}
    observations_by_code = {item.code: item for item in adapted.observations}
    scope_rows: list[dict[str, Any]] = []
    observation_rows: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    strategy_ledger_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    pivot_rows: list[dict[str, Any]] = []
    pivot_closures: list[dict[str, Any]] = []
    pivot_freezes: list[dict[str, Any]] = []
    memberships: list[dict[str, Any]] = []
    membership_closures: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []
    rejection_rows: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    terminal_uids: list[str] = []
    identity_counts = {item.value: 0 for item in IdentityDecision}

    for row in adapted.scope_members:
        scope_rows.append({
            "run_id": adapted.run_id, "code": row["code"],
            "in_scope": int(row["in_scope"]),
            "observation_status": row["observation_status"],
            "analysis_date": row["analysis_date"],
            "latest_price_date": row["latest_price_date"],
            "price_history_count": row["price_history_count"],
            "required_price_history_count": row["required_price_history_count"],
            "reason_codes_json": _json(row["reason_codes"]),
            "structured_error_ref": row["structured_error_ref"],
            "observation_uid": row["observation_uid"],
        })
        code = row["code"]
        candidates = store.load_identity_candidates(code, lineage=adapted.state_lineage)
        current_observation = observations_by_code.get(code)
        if current_observation is None:
            status = ObservationStatus(row["observation_status"])
            placeholder = CoreObservation(
                code=code, analysis_date=row["analysis_date"],
                expected_market_date=adapted.expected_market_date,
                market_session_index=adapted.market_session_index, status=status,
                reason_codes=tuple(row["reason_codes"]), input_sha256=adapted.input_sha256,
                observed_at=adapted.generated_at,
            )
            for candidate in candidates:
                state = store.load_state(candidate.core_setup_uid, lineage=adapted.state_lineage)
                if state is None:
                    continue
                transition = evaluate_transition(
                    placeholder, IdentityResolution(None, None, (status.value,)), state,
                    permanent_exit_evidence=exit_evidence.get(code),
                )
                event_rows, event_anchor_rows = _event_rows(
                    run_id=adapted.run_id, observed_at=finished_at,
                    observation=placeholder, observation_id=None,
                    core_setup_uid=state.core_setup_uid, state=state,
                    transition=transition, tracking_revision=state.tracking_pivot_revision_no,
                    permanent_exit_evidence=exit_evidence.get(code),
                )
                events.extend(event_rows)
                anchors.extend(event_anchor_rows)
                state_rows.append(_state_row(
                    lineage=adapted.state_lineage, run_id=adapted.run_id,
                    observation=placeholder, observation_id=None,
                    core_setup_uid=state.core_setup_uid, state=state,
                    transition=transition, tracking_revision=state.tracking_pivot_revision_no,
                    event_rows=event_rows,
                    permanent_exit_evidence=exit_evidence.get(code),
                ))
                if transition.to_phase == SetupPhase.CLOSED:
                    terminal_uids.append(state.core_setup_uid)
            continue

        obs_uid = observation_uid(code, adapted.run_id, current_observation.input_sha256)
        candidate_states = [
            loaded for candidate in candidates
            if (loaded := store.load_state(
                candidate.core_setup_uid, lineage=adapted.state_lineage
            )) is not None
        ]
        resolution = resolve_identity(
            current_observation, candidates, ledger_available=True,
            scope_returned=any(
                loaded.continuity_status != ContinuityStatus.CONTIGUOUS
                for loaded in candidate_states
            ),
        )
        state: SetupStateRecord | None = None
        target_uid = resolution.target_core_setup_uid
        if resolution.decision == IdentityDecision.LINK and target_uid:
            state = store.load_state(target_uid, lineage=adapted.state_lineage)
            if state and current_observation.previous_accepted_close is None:
                current_observation = replace(
                    current_observation, previous_accepted_close=state.latest_accepted_close
                )
        elif resolution.decision == IdentityDecision.MINT:
            target_uid, mint_hash, canonical_mint = mint_core_setup_uid(
                code=code, identity_epoch=identity_epoch,
                origin_observation_uid=obs_uid, origin_slot=resolution.origin_slot,
            )
            resolution = IdentityResolution(
                IdentityDecision.MINT, target_uid, resolution.reason_codes,
                resolution.blocker, resolution.origin_slot,
            )

        transition = evaluate_transition(
            current_observation, resolution, state,
            cutover_initialization=initialize_cutover,
            permanent_exit_evidence=exit_evidence.get(code),
        )
        if resolution.decision == IdentityDecision.MINT and transition.rejected:
            resolution = IdentityResolution(
                IdentityDecision.AMBIGUOUS, None,
                transition.reason_codes, resolution.blocker, resolution.origin_slot,
            )
            target_uid = None
            transition = evaluate_transition(current_observation, resolution, None)

        assert resolution.decision is not None
        identity_counts[resolution.decision.value] += 1
        if resolution.decision == IdentityDecision.MINT and target_uid:
            ledger_rows.append({
                "core_setup_uid": target_uid, "code": code, "namespace": "CORE_SETUP",
                "mint_request_sha256": mint_hash,
                "canonical_mint_request": canonical_mint,
                "identity_epoch": identity_epoch, "origin_observation_uid": obs_uid,
                "origin_slot": resolution.origin_slot, "identity_version": IDENTITY_VERSION,
                "created_run_id": adapted.run_id, "created_at": started_at, "terminal": 0,
            })

        tracking_revision = (
            state.tracking_pivot_revision_no if state else (1 if target_uid else None)
        )
        if transition.revise_tracking_pivot and state:
            tracking_revision = state.tracking_pivot_revision_no + 1
            pivot_closures.append({
                "core_setup_uid": state.core_setup_uid,
                "revision_no": state.tracking_pivot_revision_no,
                "valid_to_market_session_index": current_observation.market_session_index - 1,
            })
        observation_rows.append(_observation_row(
            current_observation, adapted.run_id, obs_uid, target_uid, tracking_revision
        ))
        decision_rows.append({
            "decision_uid": decision_uid(code, obs_uid, resolution.origin_slot),
            "observation_uid": obs_uid, "code": code, "namespace": "CORE",
            "decision_slot": resolution.origin_slot,
            "decision": resolution.decision.value,
            "target_core_setup_uid": target_uid,
            "identity_epoch": identity_epoch if resolution.decision == IdentityDecision.MINT else None,
            "origin_slot": resolution.origin_slot,
            "decision_rule_version": IDENTITY_DECISION_RULE_VERSION,
            "blocker": resolution.blocker.value,
            "reason_codes_json": _json(resolution.reason_codes),
            "evidence_refs_json": _json([obs_uid]), "supersedes_decision_uid": None,
        })

        if target_uid and (state is None or transition.revise_tracking_pivot):
            pivot = current_observation.observed_primary_pivot
            assert pivot is not None and tracking_revision is not None
            pivot_rows.append({
                "core_setup_uid": target_uid, "revision_no": tracking_revision,
                "tracking_pivot_price": pivot.price, "strategy": pivot.strategy,
                "pivot_type": pivot.pivot_type, "basis": pivot.basis,
                "fidelity": pivot.fidelity, "reference_date": pivot.reference_date,
                "effective_observation_uid": obs_uid,
                "valid_from_market_session_index": current_observation.market_session_index,
                "valid_to_market_session_index": None,
                "frozen_after_breakout": int(
                    transition.to_phase == SetupPhase.POST_BREAKOUT
                ),
                "evidence_sha256": hash_payload({
                    "uid": target_uid, "revision": tracking_revision,
                    "pivot": pivot.signature, "observation": obs_uid,
                }),
            })
        elif target_uid and state and transition.to_phase == SetupPhase.POST_BREAKOUT and state.phase != SetupPhase.POST_BREAKOUT:
            pivot_freezes.append({
                "core_setup_uid": target_uid,
                "revision_no": state.tracking_pivot_revision_no,
            })

        if target_uid and not transition.rejected:
            new_strategy_ids, new_memberships, closed_memberships = _membership_changes(
                store, current_observation, obs_uid, target_uid,
                identity_epoch, adapted.run_id, started_at,
            )
            strategy_ledger_rows.extend(new_strategy_ids)
            memberships.extend(new_memberships)
            membership_closures.extend(closed_memberships)
            assert tracking_revision is not None
            event_rows, event_anchor_rows = _event_rows(
                run_id=adapted.run_id, observed_at=finished_at,
                observation=current_observation, observation_id=obs_uid,
                core_setup_uid=target_uid, state=state, transition=transition,
                tracking_revision=tracking_revision,
                permanent_exit_evidence=exit_evidence.get(code),
            )
            events.extend(event_rows)
            anchors.extend(event_anchor_rows)
            state_rows.append(_state_row(
                lineage=adapted.state_lineage, run_id=adapted.run_id,
                observation=current_observation, observation_id=obs_uid,
                core_setup_uid=target_uid, state=state, transition=transition,
                tracking_revision=tracking_revision, event_rows=event_rows,
                permanent_exit_evidence=exit_evidence.get(code),
            ))
            if transition.to_phase == SetupPhase.CLOSED:
                terminal_uids.append(target_uid)
        if transition.rejected:
            rejection_rows.append({
                "rejection_uid": hashlib.sha256(
                    f"{adapted.run_id}\x1f{code}\x1f{obs_uid}\x1f{transition.reason_codes}".encode()
                ).hexdigest(),
                "run_id": adapted.run_id, "observation_uid": obs_uid,
                "candidate_core_setup_uid": target_uid,
                "action": transition.action.value, "transition_id": transition.transition_id,
                "reason_codes_json": _json(transition.reason_codes),
                "route": transition.route, "state_machine_version": STATE_MACHINE_VERSION,
                "created_at": finished_at,
            })

    processing, coverage, eligibility, quality_reasons = _quality(adapted, identity_counts)
    status_counts = _scope_status_counts(adapted)
    transition_total = sum(row["is_phase_transition"] for row in events)
    run_row = {
        "run_id": adapted.run_id, "run_kind": "SHADOW",
        "state_lineage": adapted.state_lineage,
        "processing_status": processing.value, "coverage_status": coverage.value,
        "publish_eligibility": eligibility.value, "state_commit_status": "COMMITTED",
        "started_at": started_at, "finished_at": finished_at,
        "expected_market_date": adapted.expected_market_date,
        "market_session_index": adapted.market_session_index,
        "scope_name": adapted.scope_name,
        "scope_member_sha256": adapted.scope_member_sha256,
        "scope_total": sum(int(row["in_scope"]) for row in adapted.scope_members),
        "current_total": status_counts[ObservationStatus.CURRENT.value],
        "no_new_market_total": status_counts[ObservationStatus.NO_NEW_MARKET_OBSERVATION.value],
        "stale_total": status_counts[ObservationStatus.STALE_MARKET_DATE.value],
        "insufficient_total": status_counts[ObservationStatus.INSUFFICIENT_PRICE_HISTORY.value],
        "fetch_failed_total": status_counts[ObservationStatus.FETCH_FAILED.value],
        "analysis_failed_total": status_counts[ObservationStatus.ANALYSIS_FAILED.value],
        "out_of_scope_total": status_counts[ObservationStatus.OUT_OF_SCOPE.value],
        "invalid_input_total": status_counts[ObservationStatus.INVALID_INPUT.value],
        "identity_link_total": identity_counts[IdentityDecision.LINK.value],
        "identity_mint_total": identity_counts[IdentityDecision.MINT.value],
        "identity_ambiguous_total": identity_counts[IdentityDecision.AMBIGUOUS.value],
        "no_setup_total": identity_counts[IdentityDecision.NO_SETUP.value],
        "transition_total": transition_total,
        "unchanged_total": sum(
            1 for state in state_rows if state["state_version"] > 1
        ) - sum(
            1 for event in events if event["is_phase_transition"]
        ),
        "rejected_total": len(rejection_rows), "input_sha256": adapted.input_sha256,
        "seed_sha256": (
            verified_seed_manifest.get("sqlite_sha256") if verified_seed_manifest
            else cutover_manifest_sha256
        ),
        "seed_schema_version": (
            verified_seed_manifest.get("seed_schema_version") if verified_seed_manifest
            else ("explicit-cutover-v1" if initialize_cutover else None)
        ),
        "seed_row_count": (
            sum(verified_seed_manifest.get("row_counts", {}).values())
            if verified_seed_manifest else 0
        ),
        "versions_json": _json({
            **adapted.source_versions, "identity_version": IDENTITY_VERSION,
            "identity_decision_rule_version": IDENTITY_DECISION_RULE_VERSION,
            "state_machine_version": STATE_MACHINE_VERSION,
            "state_machine_threshold_version": THRESHOLD_VERSION,
            "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
            "event_schema_version": EVENT_SCHEMA_VERSION,
        }),
        "reason_codes_json": _json(quality_reasons),
        "structured_errors_json": _json(adapted.upstream_errors),
    }
    # The count is descriptive and must never be negative.
    run_row["unchanged_total"] = max(0, run_row["unchanged_total"])
    return RunBundle(
        run=run_row, scope_members=tuple(scope_rows),
        observations=tuple(observation_rows), ledger_rows=tuple(ledger_rows),
        strategy_ledger_rows=tuple(strategy_ledger_rows), decisions=tuple(decision_rows),
        pivot_revisions=tuple(pivot_rows), pivot_closures=tuple(pivot_closures),
        pivot_freezes=tuple(pivot_freezes), memberships=tuple(memberships),
        membership_closures=tuple(membership_closures), events=tuple(events),
        current_states=tuple(state_rows), rejections=tuple(rejection_rows),
        event_anchors=tuple(anchors), terminal_uids=tuple(sorted(set(terminal_uids))),
    )


def execute_shadow_run(
    store: Phase2AStore, adapted: AdaptedRun, **kwargs: Any
) -> tuple[str, RunBundle]:
    bundle = build_run_bundle(store, adapted, **kwargs)
    try:
        result = store.commit_run(bundle)
    except Exception as exc:
        error_digest = hashlib.sha256(
            f"{type(exc).__name__}:{exc}".encode("utf-8")
        ).hexdigest()
        store.record_failed_run(
            bundle.run, stage="ATOMIC_COMMIT",
            reason_codes=("STATE_TRANSACTION_FAILED",), error_digest=error_digest,
        )
        raise
    return result, bundle
