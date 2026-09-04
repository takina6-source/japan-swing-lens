from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from engine.annual_eps import (REASON_CODES, annual_eps_profile, diagnostic_row,
                               merge_annual_eps, normalize_record, resolve_with_fallback,
                               SourceFetchError, update_queue_metadata)
from engine.config import load_config
from engine.data.edinet import parse_edinet_csv
from engine.database import Database
from engine.fundamentals import annual_earnings_condition
from engine.models import Verdict


def row(year, eps, source="EDINET_STANDARD", **extra):
    return {"fiscal_year": str(year), "eps": eps, "source": source, **extra}


def edinet_zip(records):
    frame = pd.DataFrame(records)
    payload = frame.to_csv(index=False).encode("utf-8-sig")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("x.csv", payload)
    return output.getvalue()


def test_priority_keeps_edinet_over_other_sources():
    selected, _ = merge_annual_eps([row(2024, 90, "YAHOO"), row(2024, 100)])
    assert selected[0]["eps"] == 100


def test_same_source_prefers_latest_published_revision():
    selected, _ = merge_annual_eps([
        row(2024, 90, published_date="2025-05-01"),
        row(2024, 100, published_date="2025-06-01"),
    ])
    assert selected[0]["eps"] == 100


def test_mixed_sources_can_complete_four_years():
    p = annual_eps_profile([row(2021, 10), row(2022, 15),
                            row(2023, 20, "JQUANTS"), row(2024, 25, "YAHOO")])
    assert p["status"] == "COMPLETE" and p["fallback_used"]


def test_complete_edinet_cache_calls_no_fallback():
    called = []
    result = resolve_with_fallback(
        [row(2022, 10), row(2023, 20), row(2024, 30)],
        [("JQUANTS", lambda: called.append("JQ") or []),
         ("YAHOO", lambda: called.append("Y") or [])])
    assert result["profile"]["years_available"] == 3 and called == []


def test_jquants_completes_before_yahoo_is_called():
    called = []
    result = resolve_with_fallback(
        [row(2022, 10)],
        [("JQUANTS", lambda: called.append("JQ") or [row(2023, 20, "JQUANTS"),
                                                       row(2024, 30, "JQUANTS")]),
         ("YAHOO", lambda: called.append("Y") or [])])
    assert result["profile"]["years_available"] == 3 and called == ["JQ"]


def test_yahoo_follows_insufficient_jquants():
    result = resolve_with_fallback(
        [row(2022, 10)],
        [("JQUANTS", lambda: [row(2023, 20, "JQUANTS")]),
         ("YAHOO", lambda: [row(2024, 30, "YAHOO")])])
    assert result["attempted_sources"] == ["JQUANTS", "YAHOO"]
    assert result["profile"]["years_available"] == 3


def test_provider_failure_does_not_stop_later_fallback():
    def failed():
        raise RuntimeError("temporary API failure")
    result = resolve_with_fallback(
        [row(2022, 10)], [("JQUANTS", failed),
                          ("YAHOO", lambda: [row(2023, 20, "YAHOO"),
                                              row(2024, 30, "YAHOO")])])
    assert result["profile"]["years_available"] == 3
    assert "JQUANTS_API_ERROR" in result["reason_codes"]


@pytest.mark.parametrize(("exc", "reason"), [
    (RuntimeError("401 unauthorized"), "JQUANTS_AUTH_ERROR"),
    (RuntimeError("temporary outage"), "JQUANTS_API_ERROR"),
    (SourceFetchError("JQUANTS_PARSE_ERROR"), "JQUANTS_PARSE_ERROR"),
])
def test_jquants_failure_reasons_are_distinct_and_safe(exc, reason):
    result = resolve_with_fallback([], [("JQUANTS", lambda: (_ for _ in ()).throw(exc))])
    assert result["reason_codes"][0] == reason
    assert result["source_attempts"] == {"JQUANTS": reason}
    assert result["errors"] == [f"JQUANTS: {reason}"]


def test_no_data_and_invalid_items_are_not_conflated():
    no_data = resolve_with_fallback([], [("JQUANTS", lambda: [])])
    invalid = resolve_with_fallback([], [("JQUANTS", lambda: [row(2024, 1, "UNKNOWN")])])
    assert "JQUANTS_NO_DATA" in no_data["reason_codes"]
    assert "JQUANTS_PARSE_ERROR" in invalid["reason_codes"]


def test_update_limit_queue_is_distinct_from_an_attempted_failure():
    queue = update_queue_metadata(["1000", "2000", "3000"], 1)
    assert queue["1000"] == {"update_state": "SELECTED", "next_update_rank": None}
    assert queue["2000"] == {"update_state": "QUEUED_UPDATE_LIMIT",
                              "next_update_rank": 2}


def test_diagnostic_includes_per_year_provenance_and_queue_state():
    profile = annual_eps_profile([row(2024, 10, "YAHOO", fidelity="PRACTICAL",
                                      retrieved_at="2026-09-04")])
    item = diagnostic_row("1234", profile, 0, ["EDINET", "JQUANTS"],
                          update_state="QUEUED_UPDATE_LIMIT", next_update_rank=8,
                          source_attempts={"JQUANTS": "JQUANTS_NOT_CONFIGURED"})
    assert item["details"]["selected_years"][0]["source"] == "YAHOO"
    assert item["details"]["selected_years"][0]["fidelity"] == "PRACTICAL"
    assert item["details"]["next_update_rank"] == 8


@pytest.mark.parametrize("bad", [None, "not-a-number", float("inf")])
def test_invalid_eps_is_rejected(bad):
    assert normalize_record(row(2024, bad)) is None


def test_quarter_is_never_accepted_as_annual():
    assert normalize_record(row(2024, 10, period_type="Q3")) is None


def test_future_filing_is_not_used_for_historical_signal():
    p = annual_eps_profile([
        row(2022, 10, published_date="2023-05-01"),
        row(2023, 20, published_date="2024-05-01"),
    ], as_of="2023-12-31")
    assert [item["fiscal_year"] for item in p["records"]] == ["2022"]


def test_conflict_is_reported_but_priority_is_preserved():
    selected, diagnostics = merge_annual_eps([row(2024, 100), row(2024, 50, "YAHOO")])
    assert selected[0]["eps"] == 100
    assert diagnostics[0]["reason_code"] == "DATA_CONFLICT"


def test_four_standard_edinet_years_are_strict():
    p = annual_eps_profile([row(year, year - 2000) for year in range(2021, 2025)])
    assert (p["status"], p["fidelity"]) == ("COMPLETE", "STRICT")


def test_three_years_are_partial_but_usable():
    p = annual_eps_profile([row(2022, 10), row(2023, 11), row(2024, 12)])
    assert (p["status"], p["years_available"]) == ("PARTIAL", 3)


def test_one_year_is_insufficient_with_specific_reason():
    p = annual_eps_profile([row(2024, 10)], attempted_reasons=["EDINET_INSUFFICIENT_YEARS"])
    assert p["status"] == "INSUFFICIENT"
    assert p["reason_code"] == "EDINET_INSUFFICIENT_YEARS"


def test_no_years_is_failed():
    p = annual_eps_profile([], attempted_reasons=["YAHOO_NOT_AVAILABLE"])
    assert p["status"] == "FAILED" and p["fidelity"] == "N/A"


def test_reason_code_catalog_contains_required_diagnostics():
    assert {"EDINET_EPS_TAG_NOT_FOUND", "JQUANTS_AUTH_ERROR", "YAHOO_PARSE_ERROR",
            "INSUFFICIENT_TOTAL_YEARS", "DATA_CONFLICT"} <= REASON_CODES


def test_diagnostic_database_roundtrip(tmp_path):
    db = Database(tmp_path / "test.db")
    p = annual_eps_profile([row(2024, 10)], attempted_reasons=["JQUANTS_NOT_CONFIGURED"])
    db.save_fundamental_diagnostic(diagnostic_row("1234", p, 0, ["YAHOO"]), "test")
    loaded = db.load_fundamental_diagnostics(["1234"])[0]
    assert loaded["reason_codes"][-1] == "INSUFFICIENT_TOTAL_YEARS"


def test_database_preserves_per_year_source_metadata(tmp_path):
    db = Database(tmp_path / "test.db")
    db.save_annual_eps("1234", [row(2024, 10, "JQUANTS", fidelity="PRACTICAL")], "JQ")
    loaded = db.load_annual_eps(["1234"])["1234"][0]
    assert loaded["source"] == "JQUANTS" and loaded["fidelity"] == "PRACTICAL"


def test_edinet_prefers_basic_and_excludes_diluted():
    records = [
        {"要素ID": "BasicEarningsLossPerShare", "項目名": "基本EPS",
         "コンテキストID": "CurrentYearDuration", "値": "120"},
        {"要素ID": "DilutedEarningsPerShare", "項目名": "希薄化EPS",
         "コンテキストID": "CurrentYearDuration", "値": "80"},
        {"要素ID": "BasicEarningsLossPerShare", "項目名": "基本EPS",
         "コンテキストID": "Prior1YearDuration", "値": "100"},
    ]
    parsed = parse_edinet_csv(edinet_zip(records), "1234", "2025-06-20")
    assert parsed["annual_eps"][-1]["eps"] == 120
    assert parsed["annual_eps"][-1]["source"] == "EDINET_STANDARD"


def test_edinet_extension_tag_is_marked_practical():
    records = [
        {"要素ID": "CompanyEarningsPerShare", "項目名": "1株当たり当期純利益",
         "コンテキストID": "CurrentYearDuration", "値": "30"},
        {"要素ID": "CompanyEarningsPerShare", "項目名": "1株当たり当期純利益",
         "コンテキストID": "Prior1YearDuration", "値": "20"},
    ]
    parsed = parse_edinet_csv(edinet_zip(records), "1234", "2025-06-20")
    assert parsed["annual_eps"][-1]["source"] == "EDINET_EXTENSION"


def test_canslim_na_note_contains_diagnostic_reason():
    cfg = load_config()
    result = annual_earnings_condition({"annual_eps": [], "annual_eps_profile": {
        "reason_code": "YAHOO_NOT_AVAILABLE"}}, cfg)
    assert result.verdict == Verdict.NA and "YAHOO_NOT_AVAILABLE" in result.note


def test_canslim_condition_excludes_future_published_eps():
    cfg = load_config()
    result = annual_earnings_condition({"as_of": "2023-12-31", "annual_eps": [
        row(2021, 10, published_date="2022-05-01"),
        row(2022, 20, published_date="2023-05-01"),
        row(2023, 30, published_date="2024-05-01"),
    ]}, cfg)
    assert result.verdict == Verdict.NA
