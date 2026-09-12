from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.state_machine.guards import (
    auto_link_gap_allowed,
    candidate_gate,
    failed_breach,
    post_breakout_expired,
    pre_breakout_expired,
    qualified_cross,
    retry_zone,
)
from engine.state_machine.identity_resolver import resolve_identity
from engine.state_machine.models import (
    ContinuityStatus,
    CoreObservation,
    DecisionAction,
    EventType,
    IdentityCandidate,
    IdentityDecision,
    IdentityResolution,
    ObservationStatus,
    PermanentExitEvidence,
    PermanentExitType,
    PivotFact,
    SetupPhase,
    SetupStateRecord,
)
from engine.state_machine.transitions import evaluate_transition


FIXTURE = Path(__file__).parents[1] / "docs/examples/phase2a-state-machine/transition-fixtures.json"
PAYLOAD = json.loads(FIXTURE.read_text(encoding="utf-8"))
SCENARIOS = {row["id"]: row for row in PAYLOAD["scenarios"]}
UID = "csu1:5901:" + "1" * 40


def _pivot(price=1000.0):
    return PivotFact(float(price), "minervini", "practical", "T_MINUS_1", "PRACTICAL", "2026-01-01")


def _observation(step, *, default_session=101, default_core_state="SETUP FORMING"):
    status = ObservationStatus(step.get("status", "CURRENT"))
    expected = step.get("expected_market_date") or step.get("date") or "2026-05-01"
    analysis = step.get("analysis_date", step.get("date", expected))
    if status == ObservationStatus.INSUFFICIENT_PRICE_HISTORY:
        analysis = None
    current = status == ObservationStatus.CURRENT
    pivot_value = step.get("pivot", 1000)
    pivot = _pivot(pivot_value) if current and pivot_value is not None else None
    return CoreObservation(
        code=step.get("code", "5901"),
        analysis_date=analysis,
        expected_market_date=expected,
        market_session_index=step.get("session", default_session),
        status=status,
        close=step.get("close", 980) if current else None,
        previous_accepted_close=step.get("previous_close", 975) if current else None,
        core_observed_state=step.get("core_state", default_core_state) if current else None,
        aligned_trend_strategy_count=step.get("aligned", 2) if current else None,
        breakout_trend_strategy_count=step.get("breakouts", 0) if current else None,
        observed_primary_pivot=pivot,
        connors_state=step.get("connors_state"),
        reason_codes=(step["reason_code"],) if step.get("reason_code") else (status.value,),
        input_sha256=step.get("input_hash", f"input-{expected}"),
        history_count=step.get("history_count"),
    )


def _state(initial, *, code="5901"):
    phase = initial.get("phase")
    if phase is None:
        return None
    latest_session = initial.get("last_accepted_session", initial.get("last_session", 100))
    return SetupStateRecord(
        core_setup_uid=initial.get("core_setup_uid", UID),
        code=code,
        phase=SetupPhase(phase),
        tracking_pivot=_pivot(initial.get("tracking_pivot", 1000)),
        tracking_pivot_revision_no=initial.get("tracking_revision", 1),
        minted_market_session_index=initial.get("minted_session", latest_session),
        latest_accepted_market_session_index=latest_session,
        latest_accepted_date=initial.get("latest_accepted_date", "2026-01-01"),
        latest_input_sha256=initial.get("prior_input_hash", "prior"),
        latest_accepted_close=initial.get("latest_close", 975),
        latest_breakout_market_session_index=initial.get("latest_breakout_session"),
        breakout_count=initial.get("breakout_count", 0),
        continuity_status=ContinuityStatus(initial.get("continuity", "CONTIGUOUS")),
        state_machine_version=initial.get("state_machine_version", "sm1"),
    )


def _resolution(step, state):
    decision = step.get("decision")
    if decision is None:
        return IdentityResolution(None, None, (step.get("reason_code", "QUALITY_GAP"),))
    kind = IdentityDecision(decision)
    return IdentityResolution(kind, state.core_setup_uid if kind == IdentityDecision.LINK and state else None, ("FIXTURE",))


def _advance(state, observation, result, target_uid=UID):
    if result.to_phase is None:
        return state
    pivot = observation.observed_primary_pivot if state is None or result.revise_tracking_pivot else state.tracking_pivot
    revision = 1 if state is None else state.tracking_pivot_revision_no + int(result.revise_tracking_pivot)
    accepted = observation.status == ObservationStatus.CURRENT
    breakout_count = state.breakout_count if state else 0
    latest_breakout = state.latest_breakout_market_session_index if state else None
    if EventType.BOOTSTRAP_OBSERVED in result.events or EventType.BREAKOUT_CONFIRMED in result.events or EventType.REBREAKOUT_CONFIRMED in result.events:
        breakout_count = max(breakout_count, result.breakout_ordinal or 1)
        latest_breakout = observation.market_session_index
    return SetupStateRecord(
        core_setup_uid=state.core_setup_uid if state else target_uid,
        code=observation.code,
        phase=result.to_phase,
        tracking_pivot=pivot,
        tracking_pivot_revision_no=revision,
        minted_market_session_index=state.minted_market_session_index if state else observation.market_session_index,
        latest_accepted_market_session_index=observation.market_session_index if accepted else state.latest_accepted_market_session_index,
        latest_accepted_date=observation.analysis_date if accepted else state.latest_accepted_date,
        latest_input_sha256=observation.input_sha256 if accepted else state.latest_input_sha256,
        latest_accepted_close=observation.close if accepted else state.latest_accepted_close,
        latest_breakout_market_session_index=latest_breakout,
        breakout_count=breakout_count,
        failure_cycle_no=(state.failure_cycle_no if state else 0) + int(EventType.FAILED_CONFIRMED in result.events),
        continuity_status=result.continuity_status or (state.continuity_status if state else ContinuityStatus.CONTIGUOUS),
        distribution_eligible=result.distribution_eligible if result.distribution_eligible is not None else True,
    )


STEP_SCENARIOS = {
    "normal-forming-watch-breakout",
    "watch-continuation-no-duplicate-event",
    "post-breakout-continuation",
    "breakout-failed-retry-rebreakout-with-missing-day",
    "second-failure-and-second-rebreakout",
    "retry-watch-refails",
    "direct-rebreakout-from-failed",
    "pre-breakout-pivot-revision-same-uid",
    "post-breakout-pivot-change-does-not-move-tracking-pivot",
    "insufficient-history-8303-like",
    "stale-four-stocks-like",
    "scope-leave-return-short-gap",
    "scope-return-long-gap",
    "permanent-scope-exit-closes",
    "dynamic-scope-removal-does-not-close",
    "qualified-cross-wins-on-first-over-limit-session",
    "out-of-order-live-input",
    "state-machine-version-change",
    "cutover-breakout-does-not-invent-event",
    "connors-only-does-not-mint-core-trend-setup",
}


@pytest.mark.parametrize("scenario_id", sorted(STEP_SCENARIOS))
def test_step_fixture_scenarios(scenario_id):
    scenario = SCENARIOS[scenario_id]
    state = _state(scenario.get("initial", {}), code=scenario.get("steps", [{}])[0].get("code", "5901"))
    for step in scenario["steps"]:
        default_core = {
            SetupPhase.WATCH: "BREAKOUT WATCH",
            SetupPhase.POST_BREAKOUT: "BREAKOUT",
        }.get(state.phase if state else None, "SETUP FORMING")
        observation = _observation(
            step,
            default_session=(state.latest_accepted_market_session_index + 1 if state else 100),
            default_core_state=default_core,
        )
        if scenario_id == "scope-return-long-gap":
            candidates = [IdentityCandidate(UID, "5901", SetupPhase.WATCH, 50)]
            resolution = resolve_identity(observation, candidates, scope_returned=True)
        elif scenario_id == "connors-only-does-not-mint-core-trend-setup":
            resolution = resolve_identity(observation, [])
        else:
            resolution = _resolution(step, state)
        evidence = None
        if step.get("master_reason") == "DELISTED_CONFIRMED":
            evidence = PermanentExitEvidence(
                PermanentExitType.DELISTED_CONFIRMED,
                step["permanent_exit_evidence_ref"],
                step.get("date", "2026-05-01"),
            )
        result = evaluate_transition(
            observation,
            resolution,
            state,
            input_state_machine_version=step.get("input_state_machine_version", "sm1"),
            cutover_initialization=scenario_id == "cutover-breakout-does-not-invent-event",
            permanent_exit_evidence=evidence,
        )
        if "expect_phase" in step:
            assert (result.to_phase.value if result.to_phase else None) == step["expect_phase"]
        if "expect_events" in step:
            assert [event.value for event in result.events] == step["expect_events"]
        for forbidden in step.get("forbid_events", []):
            assert forbidden not in [event.value for event in result.events]
        if step.get("expect"):
            expected_action = "KEEP" if step["expect"] == "NO_OP" else step["expect"]
            assert result.action.value == expected_action
        if step.get("reason_code") and scenario_id not in {"insufficient-history-8303-like"}:
            assert step["reason_code"] in result.reason_codes
        if "expect_continuity" in step:
            assert result.continuity_status.value == step["expect_continuity"]
        if "expect_breakout_ordinal" in step:
            assert result.breakout_ordinal == step["expect_breakout_ordinal"]
        if "expect_tracking_pivot" in step:
            next_state = _advance(state, observation, result)
            assert next_state.tracking_pivot.price == step["expect_tracking_pivot"]
        else:
            next_state = _advance(state, observation, result) if result.to_phase else state
        if "expect_tracking_revision" in step:
            assert next_state.tracking_pivot_revision_no == step["expect_tracking_revision"]
        state = next_state


def test_identity_fixture_scenarios():
    observation = _observation(SCENARIOS["multiple-active-setups-ambiguous"]["steps"][0])
    candidates = [
        IdentityCandidate(uid, "5901", SetupPhase.WATCH, 100)
        for uid in SCENARIOS["multiple-active-setups-ambiguous"]["initial"]["active_candidates"]
    ]
    assert resolve_identity(observation, candidates).decision == IdentityDecision.AMBIGUOUS
    explicit = SCENARIOS["multiple-active-setups-explicit-slot-link"]["steps"][0]["explicit_slot_mapping"]
    linked = resolve_identity(observation, candidates, explicit_slot_mapping=explicit)
    assert linked.decision == IdentityDecision.LINK
    assert linked.target_core_setup_uid == explicit

    terminal = SCENARIOS["expired-old-setup-new-mint"]
    minted = resolve_identity(_observation(terminal["steps"][0]), [])
    assert minted.decision == IdentityDecision.MINT
    assert minted.target_core_setup_uid != terminal["initial"]["core_setup_uid"]


def test_boundary_fixture_scenarios():
    for case in SCENARIOS["failure-price-boundary"]["cases"]:
        assert failed_breach(case["close"], case["pivot"]) is case["expect_failed"]
    for case in SCENARIOS["retry-zone-price-boundary"]["cases"]:
        assert retry_zone(case["close"], case["pivot"], case["aligned"]) is case["expect_retry"]
    for case in SCENARIOS["strategy-count-boundary"]["cases"]:
        observation = _observation({"aligned": case["aligned"], "breakouts": case["breakouts"], "close": 1010, "previous_close": 990})
        assert candidate_gate(observation) is case["expect_candidate"]
        assert qualified_cross(990, 1010, 1000, case["breakouts"]) is case["expect_qualified_cross"]
    for case in SCENARIOS["session-expiration-boundaries"]["cases"]:
        if case["kind"] == "pre":
            actual = pre_breakout_expired(case["age"], 0)
        elif case["kind"] == "post":
            actual = post_breakout_expired(case["age"], 0)
        else:
            actual = auto_link_gap_allowed(case["age"], 0)
        expected = (
            case["expect_auto_link"]
            if case["kind"] == "gap"
            else case["expect_expired"]
        )
        assert actual is expected


def test_invalid_input_fixture_fails_closed_at_value_object_boundary():
    for case in SCENARIOS["invalid-price-and-pivot"]["cases"]:
        with pytest.raises((TypeError, ValueError)):
            _observation({"close": case["close"], "pivot": case["pivot"]})


def test_fixture_scenario_registry_is_exhaustive():
    covered = STEP_SCENARIOS | {
        "expired-old-setup-new-mint",
        "multiple-active-setups-ambiguous",
        "multiple-active-setups-explicit-slot-link",
        "failure-price-boundary",
        "retry-zone-price-boundary",
        "strategy-count-boundary",
        "session-expiration-boundaries",
        "invalid-price-and-pivot",
        "fetch-and-analysis-errors",
        "same-day-rerun-and-conflict",
        "ledger-seed-fail-closed",
        "partial-run-then-complete-run",
        "lookahead-prohibited",
        "replay-determinism",
        "atomic-retry",
    }
    assert covered == set(SCENARIOS)
