from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.state_machine.config import DEFAULT_THRESHOLDS
from engine.state_machine.guards import (
    candidate_gate,
    failed_breach,
    post_breakout_expired,
    pre_breakout_expired,
    qualified_cross,
    retry_zone,
)
from engine.state_machine.identity_resolver import resolve_identity
from engine.state_machine.ids import mint_core_setup_uid
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
UID = "csu1:5901:" + "1" * 40
PIVOT = PivotFact(1000, "minervini", "practical", "T_MINUS_1", "PRACTICAL", "2025-12-31")


def obs(
    *, date="2026-01-02", session=101, status=ObservationStatus.CURRENT,
    core="SETUP FORMING", close=980.0, previous=975.0, pivot=PIVOT,
    aligned=2, breakouts=0, input_hash="new",
):
    return CoreObservation(
        code="5901", analysis_date=date if status == ObservationStatus.CURRENT else None,
        expected_market_date=date, market_session_index=session, status=status,
        close=close if status == ObservationStatus.CURRENT else None,
        previous_accepted_close=previous if status == ObservationStatus.CURRENT else None,
        core_observed_state=core if status == ObservationStatus.CURRENT else None,
        aligned_trend_strategy_count=aligned if status == ObservationStatus.CURRENT else None,
        breakout_trend_strategy_count=breakouts if status == ObservationStatus.CURRENT else None,
        observed_primary_pivot=pivot if status == ObservationStatus.CURRENT else None,
        input_sha256=input_hash,
    )


def setup_state(
    phase=SetupPhase.WATCH, *, minted=100, latest_session=100,
    latest_date="2026-01-01", latest_hash="old", latest_close=975,
    latest_breakout=None, breakout_count=0, continuity=ContinuityStatus.CONTIGUOUS,
    pivot=PIVOT,
):
    return SetupStateRecord(
        core_setup_uid=UID, code="5901", phase=phase, tracking_pivot=pivot,
        tracking_pivot_revision_no=1, minted_market_session_index=minted,
        latest_accepted_market_session_index=latest_session,
        latest_accepted_date=latest_date, latest_input_sha256=latest_hash,
        latest_accepted_close=latest_close,
        latest_breakout_market_session_index=latest_breakout,
        breakout_count=breakout_count, continuity_status=continuity,
    )


def link():
    return IdentityResolution(IdentityDecision.LINK, UID, ("TEST_LINK",))


def mint():
    return IdentityResolution(IdentityDecision.MINT, None, ("TEST_MINT",))


def test_fixture_enums_and_transition_contract_are_complete():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["enums"]["phases"] == [phase.value for phase in SetupPhase]
    ids = [item["id"] for item in payload["transition_contract"]]
    assert ids == [f"T{index:02d}" for index in range(1, 27)]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "close,expected", [(970.01, False), (970.0, False), (969.99, True)],
)
def test_failure_boundary(close, expected):
    assert failed_breach(close, 1000) is expected


@pytest.mark.parametrize(
    "close,expected", [(969.99, False), (970.0, True), (1000.0, True), (1000.01, False)],
)
def test_retry_boundary(close, expected):
    assert retry_zone(close, 1000, 2) is expected


def test_strategy_and_expiry_boundaries():
    assert qualified_cross(990, 1010, 1000, 1) is False
    assert qualified_cross(990, 1010, 1000, 2) is True
    assert qualified_cross(1000, 1000.01, 1000, 2) is True
    assert qualified_cross(1000.01, 1010, 1000, 2) is False
    assert pre_breakout_expired(190, 100) is False
    assert pre_breakout_expired(191, 100) is True
    assert post_breakout_expired(120, 100) is False
    assert post_breakout_expired(121, 100) is True


@pytest.mark.parametrize("bad", [None, 0, -1, float("nan"), float("inf"), float("-inf")])
def test_non_positive_or_non_finite_prices_fail_closed(bad):
    with pytest.raises((TypeError, ValueError)):
        PivotFact(bad, "minervini", "practical", "T_MINUS_1", "PRACTICAL")


def test_transition_ids_t01_to_t26():
    cases = {
        "T01": evaluate_transition(obs(), mint(), None),
        "T02": evaluate_transition(obs(core="BREAKOUT WATCH"), mint(), None),
        "T03": evaluate_transition(
            obs(core="BREAKOUT", close=1010, previous=990, breakouts=2), mint(), None
        ),
        "T04": evaluate_transition(
            obs(core="BREAKOUT", close=1010, previous=None, breakouts=2), mint(), None,
            cutover_initialization=True,
        ),
        "T05": evaluate_transition(obs(core="BREAKOUT WATCH"), link(), setup_state(SetupPhase.FORMING)),
        "T06": evaluate_transition(
            obs(core="BREAKOUT", close=1010, previous=990, breakouts=2), link(), setup_state()
        ),
        "T07": evaluate_transition(obs(core="SETUP FORMING"), link(), setup_state()),
        "T08": evaluate_transition(obs(session=191), link(), setup_state(minted=100, latest_session=190)),
        "T09": evaluate_transition(
            obs(close=969, previous=1010), link(),
            setup_state(SetupPhase.POST_BREAKOUT, latest_breakout=100, breakout_count=1),
        ),
        "T10": evaluate_transition(
            obs(close=1040, previous=1010), link(),
            setup_state(SetupPhase.POST_BREAKOUT, latest_breakout=100, breakout_count=1),
        ),
        "T11": evaluate_transition(
            obs(close=980, previous=960), link(),
            setup_state(SetupPhase.FAILED, latest_breakout=100, breakout_count=1),
        ),
        "T12": evaluate_transition(
            obs(close=1010, previous=960, breakouts=2), link(),
            setup_state(SetupPhase.FAILED, latest_breakout=100, breakout_count=1),
        ),
        "T13": evaluate_transition(
            obs(close=1010, previous=990, breakouts=2), link(),
            setup_state(SetupPhase.RETRY_WATCH, latest_breakout=100, breakout_count=1),
        ),
        "T14": evaluate_transition(
            obs(close=969, previous=980), link(),
            setup_state(SetupPhase.RETRY_WATCH, latest_breakout=100, breakout_count=1),
        ),
        "T15": evaluate_transition(
            obs(close=1001, previous=1005), link(),
            setup_state(SetupPhase.RETRY_WATCH, latest_breakout=100, breakout_count=1),
        ),
        "T16": evaluate_transition(
            obs(session=121, close=960, previous=970), link(),
            setup_state(SetupPhase.FAILED, latest_breakout=100, breakout_count=1),
        ),
        "T17": evaluate_transition(obs(), link(), setup_state(SetupPhase.FORMING)),
        "T18": evaluate_transition(obs(), link(), setup_state(SetupPhase.EXPIRED)),
        "T19": evaluate_transition(
            obs(status=ObservationStatus.STALE_MARKET_DATE),
            IdentityResolution(None, None, ("STALE_MARKET_DATE",)), setup_state(),
        ),
        "T20": evaluate_transition(
            obs(date="2026-01-02"), link(), setup_state(latest_date="2026-01-03")
        ),
        "T21": evaluate_transition(
            obs(date="2026-01-02", input_hash="same"), link(),
            setup_state(latest_date="2026-01-02", latest_hash="same"),
        ),
        "T22": evaluate_transition(
            obs(date="2026-01-02", input_hash="different"), link(),
            setup_state(latest_date="2026-01-02", latest_hash="same"),
        ),
        "T23": evaluate_transition(
            obs(pivot=PivotFact(1010, "minervini", "practical", "T_MINUS_1", "PRACTICAL")),
            link(), setup_state(SetupPhase.FORMING),
        ),
        "T24": evaluate_transition(
            obs(pivot=PivotFact(1010, "minervini", "practical", "T_MINUS_1", "PRACTICAL")),
            link(), setup_state(SetupPhase.POST_BREAKOUT, latest_breakout=100, breakout_count=1),
        ),
        "T25": evaluate_transition(obs(), link(), setup_state(), input_state_machine_version="sm2"),
        "T26": evaluate_transition(
            obs(status=ObservationStatus.OUT_OF_SCOPE), IdentityResolution(None, None, ()), setup_state(),
            permanent_exit_evidence=PermanentExitEvidence(
                PermanentExitType.DELISTED_CONFIRMED, "master:5901", "2026-01-02"
            ),
        ),
    }
    assert {key: value.transition_id for key, value in cases.items()} == {
        key: key for key in cases
    }


def test_cross_wins_on_first_over_limit_session():
    result = evaluate_transition(
        obs(session=191, core="BREAKOUT", close=1010, previous=990, breakouts=2),
        link(), setup_state(minted=100, latest_session=190),
    )
    assert result.transition_id == "T06"
    assert EventType.BREAKOUT_CONFIRMED in result.events
    assert EventType.SETUP_EXPIRED not in result.events


def test_long_gap_is_ambiguous_and_does_not_infer_cross():
    observation = obs(session=106, core="BREAKOUT", close=1010, previous=990, breakouts=2)
    resolution = resolve_identity(
        observation,
        [IdentityCandidate(UID, "5901", SetupPhase.WATCH, 100)],
    )
    assert resolution.decision == IdentityDecision.AMBIGUOUS
    result = evaluate_transition(observation, resolution, setup_state(latest_session=100))
    assert result.to_phase == SetupPhase.WATCH
    assert result.events == ()
    assert result.continuity_status == ContinuityStatus.SUSPENDED_GAP


def test_short_scope_pause_resumes_without_changing_phase():
    state = setup_state(latest_session=50, continuity=ContinuityStatus.PAUSED)
    observation = obs(session=55, close=990, previous=980, core="BREAKOUT WATCH")
    resolution = resolve_identity(
        observation, [IdentityCandidate(UID, "5901", SetupPhase.WATCH, 50)],
        scope_returned=True,
    )
    assert resolution.decision == IdentityDecision.LINK
    result = evaluate_transition(observation, resolution, state)
    assert result.to_phase == SetupPhase.WATCH
    assert result.events == (EventType.CONTINUITY_RESUMED,)


def test_uid_does_not_include_daily_state_or_pivot():
    first = mint_core_setup_uid(
        code="5901", identity_epoch="phase2a-cutover-1",
        origin_observation_uid="obs1:5901:abc", origin_slot="core-primary",
    )
    second = mint_core_setup_uid(
        code="5901", identity_epoch="phase2a-cutover-1",
        origin_observation_uid="obs1:5901:abc", origin_slot="core-primary",
    )
    assert first == second
    assert first[0].startswith("csu1:5901:")
    assert len(first[0].split(":")[-1]) == 40
    assert first == (
        "csu1:5901:d6f290afc5dc2c45d7ec2a63da7f23b120b62259",
        "d6f290afc5dc2c45d7ec2a63da7f23b120b62259704b2d4003b68c774a07a6b9",
        '{"code":"5901","identity_epoch":"phase2a-cutover-1",'
        '"identity_schema_version":1,"namespace":"CORE_SETUP",'
        '"origin_observation_uid":"obs1:5901:abc","origin_slot":"core-primary"}',
    )
