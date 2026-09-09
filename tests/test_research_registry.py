import copy

import pytest

from engine.database import Database
from engine.research.gates import (candidate_gate, checkpoint_gate,
                                   family_correction, final_decision)
from engine.research.registry import (cost_model, family_registry,
                                      hypothesis_registry, validate_family_sync)
from engine.research.storage import ResearchStore
from engine.research.pipeline import _formal_result, _maybe_close_family


def _hypothesis(hypothesis_id):
    return next(row for row in hypothesis_registry() if row["hypothesis_id"] == hypothesis_id)


def test_registry_is_data_informed_hashed_and_family_holdout_is_synchronous():
    hypotheses = hypothesis_registry()
    family = family_registry()
    validate_family_sync(hypotheses, family)
    assert {row["origin"] for row in hypotheses} == {"DATA_INFORMED"}
    assert {row["holdout_start"] for row in hypotheses} == {family["family_holdout_start"]}
    assert len({row["frozen_at"][:10] for row in hypotheses}) == 1
    assert all(len(row["definition_hash"]) == 64 for row in hypotheses)
    assert family["planned_family_size"] == 4
    assert family["holm_n_policy"] == "PLANNED_FAMILY_SIZE"


def test_family_rejects_member_changes_or_async_holdout():
    hypotheses, family = hypothesis_registry(), family_registry()
    with pytest.raises(ValueError):
        validate_family_sync(hypotheses[:-1], family)
    changed = copy.deepcopy(hypotheses)
    changed[0]["holdout_start"] = "2026-09-11"
    with pytest.raises(ValueError):
        validate_family_sync(changed, family)


def test_frozen_registry_version_cannot_be_rewritten(tmp_path):
    store = ResearchStore(Database(tmp_path / "research.db"))
    rows = hypothesis_registry()
    store.register_hypotheses(rows)
    changed = copy.deepcopy(rows)
    changed[0]["definition_hash"] = "0" * 64
    with pytest.raises(ValueError):
        store.register_hypotheses(changed)


@pytest.mark.parametrize(("hypothesis_id", "stats"), [
    ("H1", {"group_4plus": 100, "group_3": 40, "unique_stocks": 80,
            "unique_dates": 20, "effect_estimate": .6,
            "ci_90_lower": .1, "ci_90_upper": 1.1}),
    ("H2", {"events": 50, "unique_stocks": 30, "unique_dates": 15,
            "effect_estimate": .8, "ci_90_lower": .1, "ci_90_upper": 1.5}),
    ("H3", {"paired_events": 50, "unique_stocks": 30, "unique_dates": 15,
            "effect_estimate": .4, "ci_90_lower": .1, "ci_90_upper": .7}),
    ("H4", {"group_5": 30, "group_4": 30, "unique_stocks": 40,
            "unique_dates": 15, "effect_estimate": -9,
            "ci_90_lower": -13, "ci_90_upper": -1}),
])
def test_h1_to_h4_candidate_gates_are_all_conditions(hypothesis_id, stats):
    stats.update(total_n=100, legacy_n=100, live_pre_candidate_n=0,
                 legacy_share_pct=100)
    assert candidate_gate(_hypothesis(hypothesis_id), stats)["candidate_status"] == "CANDIDATE"
    stats["unique_dates"] = 0
    assert candidate_gate(_hypothesis(hypothesis_id), stats)["candidate_status"] == "DISCOVERY_CONTINUES"


def test_h3_difference_and_equivalence_candidate_claims_are_distinct():
    difference = _hypothesis("H3")
    equivalence = copy.deepcopy(difference)
    equivalence["candidate_claim"] = "EQUIVALENCE"
    common = {"paired_events": 50, "unique_stocks": 30, "unique_dates": 15,
              "total_n": 50, "legacy_n": 50, "live_pre_candidate_n": 0}
    assert candidate_gate(difference, {**common, "effect_estimate": .4,
        "ci_90_lower": .1, "ci_90_upper": .6})["candidate_status"] == "CANDIDATE"
    assert candidate_gate(equivalence, {**common, "effect_estimate": .01,
        "ci_90_lower": -.1, "ci_90_upper": .1})["candidate_status"] == "CANDIDATE"
    assert candidate_gate(equivalence, {**common, "effect_estimate": .01,
        "ci_90_lower": -.4, "ci_90_upper": .1})["candidate_status"] == "DISCOVERY_CONTINUES"


def test_checkpoint_a_is_diagnostic_and_b_uses_all_rows_when_first_crossed():
    h2 = _hypothesis("H2")
    a = checkpoint_gate(h2, "A", {"events": 40, "unique_stocks": 25,
                                  "unique_dates": 12, "effect_estimate": .4})
    assert a["formal_decision"] is False
    assert a["result"] == "SUPPORTIVE_TREND"
    assert checkpoint_gate(h2, "B", {"events": 74, "unique_stocks": 50,
        "unique_dates": 25})["reached"] is False
    crossed = checkpoint_gate(h2, "B", {"events": 79, "unique_stocks": 50,
                                        "unique_dates": 25})
    assert crossed["reached"] is True
    assert crossed["effective_n"] == 79


def test_checkpoint_b_is_inserted_once(tmp_path):
    store = ResearchStore(Database(tmp_path / "research.db"))
    row = {"hypothesis_version": "H2-v1", "checkpoint_name": "B",
           "reached_at": "one", "evaluated_at": "one", "effective_n": 79,
           "unique_stocks": 50, "unique_dates": 25, "result": "READY"}
    assert store.save_checkpoint_once(row) is True
    assert store.save_checkpoint_once({**row, "effective_n": 100, "evaluated_at": "two"}) is False
    assert store.checkpoint("H2-v1", "B")["effective_n"] == 79


def test_holm_uses_planned_four_and_includes_all_executed_primary_tests():
    raw = {
        "H1-v1": {"primary_family_p_field": "difference_primary_p", "difference_primary_p": .01},
        "H2-v1": {"primary_family_p_field": "difference_primary_p", "difference_primary_p": .20},
        "H3-v1": {"primary_family_p_field": "equivalence_primary_p",
                  "equivalence_primary_p": .02, "difference_primary_p": .001},
        "H4-v1": {"primary_family_p_field": "difference_primary_p", "difference_primary_p": None},
    }
    adjusted = family_correction(raw, planned_size=4)
    assert adjusted["H1-v1"]["holm_adjusted_p"] == pytest.approx(.04)
    assert adjusted["H3-v1"]["holm_adjusted_p"] == pytest.approx(.06)
    assert adjusted["H2-v1"]["holm_adjusted_p"] == pytest.approx(.4)
    assert adjusted["H4-v1"]["holm_adjusted_p"] is None


def test_all_final_decisions_require_all_gates():
    h1 = _hypothesis("H1")
    passing = {"test_execution_status": "EXECUTED", "holm_adjusted_p": .01,
               "direction_gate": "EXPECTED", "effect_size_gate": "PASS",
               "ci_gate": "PASS", "high_cost_robustness_gate": "PASS"}
    assert final_decision(h1, passing, family_closed=True) == "CONFIRMED"
    assert final_decision(h1, {**passing, "effect_size_gate": "FAIL"}, family_closed=True) == "INCONCLUSIVE"
    opposite = {**passing, "direction_gate": "OPPOSITE", "effect_size_gate": "FAIL",
                "ci_gate": "FAIL", "opposite_effect_size_gate": "PASS",
                "opposite_ci_gate": "PASS"}
    assert final_decision(h1, opposite, family_closed=True) == "CONTRADICTED"
    assert final_decision(h1, {**opposite, "opposite_ci_gate": "FAIL"}, family_closed=True) == "INCONCLUSIVE"
    assert final_decision(h1, {"test_execution_status": "INSUFFICIENT_SAMPLE"}, family_closed=True) == "INSUFFICIENT_SAMPLE"
    assert final_decision(h1, passing, family_closed=False) == "AWAITING_FAMILY_CORRECTION"


def test_equivalence_primary_p_and_ci_robustness_are_independent_no_claim_switching():
    h3 = copy.deepcopy(_hypothesis("H3"))
    h3.update(candidate_claim="EQUIVALENCE", primary_family_p_field="equivalence_primary_p",
              primary_test_type="EQUIVALENCE_TOST")
    passing = {"test_execution_status": "EXECUTED", "holm_adjusted_p": .01,
               "equivalence_test_pass": True, "equivalence_ci_containment_gate": "PASS",
               "high_cost_robustness_gate": "PASS"}
    assert final_decision(h3, passing, family_closed=True) == "EQUIVALENCE_CONFIRMED"
    assert final_decision(h3, {**passing, "equivalence_ci_containment_gate": "FAIL"}, family_closed=True) == "INCONCLUSIVE"
    assert final_decision(h3, {**passing, "holm_adjusted_p": .09}, family_closed=True) == "INCONCLUSIVE"
    assert final_decision(h3, {**passing, "high_cost_robustness_gate": "FAIL"}, family_closed=True) == "INCONCLUSIVE"
    # A significant difference diagnostic cannot post-hoc replace the frozen TOST claim.
    switched = {**passing, "equivalence_test_pass": False, "difference_primary_p": .0001,
                "direction_gate": "OPPOSITE", "opposite_effect_size_gate": "PASS",
                "opposite_ci_gate": "PASS"}
    assert final_decision(h3, switched, family_closed=True) == "INCONCLUSIVE"


def test_cost_model_has_frozen_low_base_high_and_final_sesoi():
    model = cost_model()
    assert model["LOW"]["total"] < model["BASE"]["total"] < model["HIGH"]["total"]
    assert model["final_sesoi_pp"] == max(.30, 2 * model["incremental_cost_uncertainty"])


def test_h3_high_cost_reversal_blocks_difference_confirmation():
    h3 = _hypothesis("H3")
    result = _formal_result(h3, {
        "effect_estimate": .40, "standard_error": .05,
        "ci_95_lower": .20, "ci_95_upper": .60, "high_cost_effect": -.05,
    })
    assert result["high_cost_robustness_gate"] == "FAIL"
    result["holm_adjusted_p"] = .01
    assert final_decision(h3, result, family_closed=True) == "INCONCLUSIVE"


def test_equivalence_formal_test_keeps_tost_p_ci_and_cost_as_separate_gates():
    h3 = copy.deepcopy(_hypothesis("H3"))
    h3.update(candidate_claim="EQUIVALENCE", primary_family_p_field="equivalence_primary_p",
              primary_test_type="EQUIVALENCE_TOST")
    result = _formal_result(h3, {
        "effect_estimate": 0.0, "standard_error": .05,
        "ci_95_lower": -.10, "ci_95_upper": .10, "high_cost_effect": .40,
    })
    assert result["raw_primary_p"] == result["equivalence_primary_p"]
    assert result["equivalence_ci_containment_gate"] == "PASS"
    assert result["high_cost_robustness_gate"] == "FAIL"
    result["holm_adjusted_p"] = .01
    assert final_decision(h3, result, family_closed=True) == "INCONCLUSIVE"


def test_family_has_no_adjusted_result_before_close_and_missing_members_keep_null_p(tmp_path):
    store = ResearchStore(Database(tmp_path / "family.db"))
    hypotheses, family = hypothesis_registry(), family_registry()
    store.register_hypotheses(hypotheses)
    store.register_family(family)
    assert _maybe_close_family(store, hypotheses, family, "2027-01-25") is None
    closed = _maybe_close_family(store, hypotheses, family, "2027-01-26")
    assert closed["family_status"] == "CLOSED"
    assert len(closed["members"]) == 4
    assert all(row["raw_primary_p"] is None and row["holm_adjusted_p"] is None
               and row["final_decision"] == "INSUFFICIENT_SAMPLE"
               for row in closed["members"].values())
