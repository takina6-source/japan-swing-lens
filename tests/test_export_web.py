import math

from engine.config import load_config
from engine.validation import annual_eps_coverage_summary
from scripts.export_web import clean, universe_diagnostics


def test_clean_converts_nested_nan_to_json_null():
    value = {"a": float("nan"), "b": [1.0, float("inf")], "c": "○"}
    assert clean(value) == {"a": None, "b": [1.0, None], "c": "○"}


def test_clean_keeps_finite_numbers():
    assert math.isclose(clean(12.5), 12.5)


def test_annual_coverage_separates_complete_from_usable():
    cfg = load_config()
    rows = [
        {"status": "COMPLETE", "fidelity": "STRICT", "years_available": 4,
         "initial_years": 4, "source_summary": "EDINET_STANDARD 4期", "details": {}},
        {"status": "PARTIAL", "fidelity": "PRACTICAL", "years_available": 3,
         "initial_years": 2, "source_summary": "YAHOO 3期", "fallback_used": 1,
         "details": {"source_attempts": {"YAHOO": "SUCCESS"}}},
        {"status": "INSUFFICIENT", "fidelity": "PARTIAL", "years_available": 2,
         "source_summary": "JQUANTS 2期",
         "details": {"update_state": "QUEUED_UPDATE_LIMIT",
                     "source_attempts": {"JQUANTS": "JQUANTS_INSUFFICIENT_HISTORY"}}},
        {"status": "FAILED", "fidelity": "N/A", "years_available": 0,
         "source_summary": "N/A", "details": {}},
    ]
    result = annual_eps_coverage_summary(rows, cfg)
    assert result["complete_4y"] == 1
    assert result["usable_3y_plus"] == 2
    assert result["partial_3y"] == 1
    assert result["insufficient_under_3y"] == 2
    assert result["status_breakdown"] == {
        "COMPLETE": 1, "PARTIAL": 1, "INSUFFICIENT": 1, "FAILED": 1}
    assert result["fidelity_breakdown"]["STRICT"] == 1
    assert result["source_attempt_status"]["EDINET"]["NOT_ATTEMPTED"] == 4
    assert result["source_attempt_status"]["YAHOO"]["SUCCESS"] == 1
    assert not any("fixable" in key for key in result)


def test_universe_diagnostic_explains_denominator_difference():
    result = universe_diagnostics(
        {"1", "2", "3", "4"}, {"1", "2", "3"}, {"1", "2"}, {"1"}, {"1"})
    assert result["diagnostic_total"] == 4 and result["ranked_total"] == 1
    assert result["excluded_total"] == 3
    assert result["excluded_from_ranking"] == {
        "outside_current_scope": 1, "insufficient_price_history": 1,
        "indicator_preparation_failed": 1, "analysis_or_metadata_error": 0}
