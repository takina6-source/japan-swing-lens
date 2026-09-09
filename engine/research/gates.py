from __future__ import annotations

from typing import Any

from .statistics import holm_adjust


FINAL_DECISIONS = {
    "CONFIRMED", "EQUIVALENCE_CONFIRMED", "INCONCLUSIVE",
    "CONTRADICTED", "INSUFFICIENT_SAMPLE",
}


def minimums_met(actual: dict[str, int | float], minimums: dict[str, int | float]) -> bool:
    return all(float(actual.get(key, 0) or 0) >= float(value)
               for key, value in minimums.items())


def candidate_gate(hypothesis: dict, stats: dict[str, Any]) -> dict[str, Any]:
    sample_pass = minimums_met(stats, hypothesis["sample_gate"])
    effect = stats.get("effect_estimate")
    low, high = stats.get("ci_90_lower"), stats.get("ci_90_upper")
    claim = hypothesis.get("candidate_claim")
    metric_pass = False
    if effect is not None and low is not None and high is not None:
        if hypothesis["hypothesis_id"] in {"H1", "H2"}:
            metric_pass = effect >= float(hypothesis["sesoi"]) and low > 0
        elif hypothesis["hypothesis_id"] == "H4":
            metric_pass = effect <= float(hypothesis["sesoi"]) and high < 0
        elif claim == "EQUIVALENCE":
            margin = abs(float(hypothesis["sesoi"]))
            metric_pass = low >= -margin and high <= margin
        else:
            margin = abs(float(hypothesis["sesoi"]))
            metric_pass = abs(effect) >= margin and (low > 0 or high < 0)
    return {
        "candidate_status": "CANDIDATE" if sample_pass and metric_pass else "DISCOVERY_CONTINUES",
        "sample_gate": "PASS" if sample_pass else "FAIL",
        "effect_gate": "PASS" if metric_pass else "FAIL",
        "candidate_claim": claim,
        "candidate_basis": candidate_basis(stats),
        **{key: stats.get(key) for key in (
            "total_n", "legacy_n", "live_pre_candidate_n", "legacy_share_pct",
            "effect_estimate", "ci_90_lower", "ci_90_upper")},
    }


def candidate_basis(stats: dict[str, Any]) -> str:
    legacy = int(stats.get("legacy_n") or 0)
    live = int(stats.get("live_pre_candidate_n") or 0)
    total = legacy + live
    if total == 0:
        return "NO_DISCOVERY_DATA"
    share = legacy / total
    if live == 0:
        return "LEGACY_ONLY"
    if share >= 0.75:
        return "LEGACY_DOMINANT"
    if share <= 0.25:
        return "LIVE_DOMINANT"
    return "MIXED_DISCOVERY"


def checkpoint_gate(hypothesis: dict, name: str,
                    holdout_stats: dict[str, Any]) -> dict[str, Any]:
    checkpoint = hypothesis["checkpoint_schedule"][name]
    reached = minimums_met(holdout_stats, checkpoint["minimums"])
    if name == "A":
        result = _diagnostic_result(hypothesis, holdout_stats) if reached else "NOT_REACHED"
    else:
        result = "READY_FOR_ONE_TIME_FORMAL_TEST" if reached else "NOT_REACHED"
    return {
        "checkpoint_name": name,
        "purpose": checkpoint["purpose"],
        "formal_decision": bool(checkpoint["formal_decision"]),
        "reached": reached,
        "effective_n": int(holdout_stats.get("effective_n") or
                           holdout_stats.get("events") or
                           holdout_stats.get("paired_events") or 0),
        "unique_stocks": int(holdout_stats.get("unique_stocks") or 0),
        "unique_dates": int(holdout_stats.get("unique_dates") or 0),
        "result": result,
    }


def family_correction(raw_results: dict[str, dict[str, Any]], planned_size: int = 4,
                      alpha: float = 0.05) -> dict[str, dict[str, Any]]:
    primary = {version: _primary_p(row) for version, row in raw_results.items()}
    adjusted = holm_adjust(primary, planned_size)
    return {version: {**row, "holm_adjusted_p": adjusted.get(version),
                      "multiplicity_gate": (
                          "PASS" if adjusted.get(version) is not None
                          and adjusted[version] < alpha else
                          "FAIL" if adjusted.get(version) is not None else "NOT_TESTED")}
            for version, row in raw_results.items()}


def final_decision(hypothesis: dict, result: dict[str, Any], *, family_closed: bool,
                   alpha: float = 0.05) -> str:
    if result.get("test_execution_status") == "INSUFFICIENT_SAMPLE":
        return "INSUFFICIENT_SAMPLE"
    if not family_closed:
        return "AWAITING_FAMILY_CORRECTION"
    adjusted = result.get("holm_adjusted_p")
    multiplicity = adjusted is not None and float(adjusted) < alpha
    claim = hypothesis.get("candidate_claim")
    if hypothesis["hypothesis_id"] == "H3" and claim == "EQUIVALENCE":
        # Non-confirmation never switches post-hoc to a difference claim.
        if (multiplicity
                and result.get("equivalence_test_pass") is True
                and result.get("equivalence_ci_containment_gate") == "PASS"
                and result.get("high_cost_robustness_gate") == "PASS"):
            return "EQUIVALENCE_CONFIRMED"
        return "INCONCLUSIVE"
    if (result.get("direction_gate") == "EXPECTED"
            and result.get("effect_size_gate") == "PASS"
            and result.get("ci_gate") == "PASS" and multiplicity
            and result.get("high_cost_robustness_gate", "PASS") == "PASS"):
        return "CONFIRMED"
    if (result.get("direction_gate") == "OPPOSITE"
            and result.get("opposite_effect_size_gate") == "PASS"
            and result.get("opposite_ci_gate") == "PASS" and multiplicity):
        return "CONTRADICTED"
    return "INCONCLUSIVE"


def _primary_p(result: dict[str, Any]) -> float | None:
    field = result.get("primary_family_p_field")
    if field:
        return result.get(field)
    return result.get("raw_primary_p")


def _diagnostic_result(hypothesis: dict, stats: dict[str, Any]) -> str:
    if stats.get("data_quality_alert"):
        return "DATA_QUALITY_ALERT"
    effect = stats.get("effect_estimate")
    if effect is None:
        return "INCONCLUSIVE" if hypothesis["hypothesis_id"] == "H3" else "UNCLEAR"
    if hypothesis["hypothesis_id"] == "H3":
        margin = abs(float(hypothesis["sesoi"]))
        low, high = stats.get("ci_95_lower"), stats.get("ci_95_upper")
        if low is not None and high is not None and low >= -margin and high <= margin:
            return "EQUIVALENCE_TREND"
        return "DIFFERENT_TREND" if abs(float(effect)) >= margin else "INCONCLUSIVE"
    expected = float(effect) > 0 if hypothesis["direction"] == "POSITIVE" else float(effect) < 0
    return "SUPPORTIVE_TREND" if expected else "CONTRARY_TREND"
