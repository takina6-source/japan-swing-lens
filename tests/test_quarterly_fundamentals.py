import pandas as pd

from engine.config import load_config
from engine.database import Database
from engine.data.yahoo import (merge_yahoo_reported_eps,
                               normalize_yahoo_quarterly_statement)
from engine.quarterly_fundamentals import (normalize_quarterly_records,
                                           quarterly_diagnostic, quarterly_profile)


def record(end, eps, sales, operating, *, published="2026-08-01",
           source="JQUANTS", period_type="QUARTER", quarter=None, year=None):
    yyyy, mm, _ = end.split("-")
    return {"period_end": end, "fiscal_year": year or yyyy,
            "fiscal_quarter": quarter or f"Q{(int(mm) - 1) // 3 + 1}",
            "basic_eps": eps, "revenue": sales, "operating_profit": operating,
            "net_income": operating * .8 if operating is not None else None,
            "published_date": published, "source": source, "fidelity": "STRICT",
            "period_type": period_type, "publication_date_known": 1}


def test_standalone_quarters_produce_yoy_and_positive_acceleration():
    cfg = load_config()
    rows = [
        record("2024-12-31", 20, 100, 10), record("2025-03-31", 20, 100, 10),
        record("2025-06-30", 20, 100, 10), record("2025-09-30", 20, 100, 10),
        record("2025-12-31", 24, 118, 12), record("2026-03-31", 27, 122, 14),
        record("2026-06-30", 31, 130, 16), record("2026-09-30", 35, 135, 18),
    ]
    profile = quarterly_profile(rows, cfg, "2026-12-01")
    assert round(profile["eps_growth"], 1) == 75.0
    assert round(profile["previous_eps_growth"], 1) == 55.0
    assert round(profile["eps_acceleration"], 1) == 20.0
    assert round(profile["sales_growth"], 1) == 35.0
    assert profile["coverage"] == 100


def test_ytd_profit_is_derived_but_eps_is_not_subtracted():
    rows = [record("2026-03-31", 10, 100, 20, period_type="YTD", quarter="Q1", year="2026"),
            record("2026-06-30", 22, 230, 50, period_type="YTD", quarter="Q2", year="2026")]
    normalized = normalize_quarterly_records(rows)
    q2 = next(row for row in normalized if row["period_type"] == "QUARTER"
              and row["fiscal_quarter"] == "Q2")
    assert q2["revenue"] == 130 and q2["operating_profit"] == 30
    assert q2["basic_eps"] is None and q2["is_derived"] == 1


def test_missing_prior_year_and_future_publication_are_na():
    cfg = load_config()
    rows = [record("2026-06-30", 30, 130, 20, published="2026-08-10")]
    before = quarterly_profile(rows, cfg, "2026-08-01")
    assert before["coverage"] == 0
    assert "FUTURE_PUBLICATION_EXCLUDED" in before["reason_codes"]
    after = quarterly_profile(rows, cfg, "2026-09-01")
    assert after["eps_growth"] is None


def test_turnaround_and_near_zero_are_not_exaggerated_growth():
    cfg = load_config()
    turnaround = [record("2025-06-30", -5, 100, 10),
                  record("2026-06-30", 20, 120, 15)]
    profile = quarterly_profile(turnaround, cfg, "2026-09-01")
    assert profile["turnaround_flag"] and profile["eps_growth"] is None
    anomaly = [record("2025-06-30", .1, 100, 10),
               record("2026-06-30", 20, 120, 15)]
    profile = quarterly_profile(anomaly, cfg, "2026-09-01")
    assert profile["anomaly_flag"] and profile["eps_growth"] is None


def test_database_preserves_sources_and_diagnostics(tmp_path):
    cfg = load_config()
    db = Database(tmp_path / "quarterly.db")
    rows = [record("2025-06-30", 20, 100, 10),
            record("2026-06-30", 30, 125, 14)]
    rows[0]["field_diagnostics"] = {
        "revenue": {"status": "AVAILABLE", "item_name": "Sales", "reason": None}}
    db.save_quarterly_fundamentals("1000", rows, "JQUANTS")
    loaded = db.load_quarterly_fundamentals(["1000"])["1000"]
    assert len(loaded) == 2 and loaded[0]["source"] == "JQUANTS"
    assert loaded[0]["field_diagnostics"]["revenue"]["item_name"] == "Sales"
    profile = quarterly_profile(loaded, cfg, "2026-09-01")
    db.save_quarterly_diagnostic({"code": "1000", "status": "PARTIAL",
        "coverage": profile["coverage"], "quarters_available": len(loaded),
        "source_summary": "JQUANTS", "fidelity": "STRICT",
        "reason_codes": profile["reason_codes"], "attempted_sources": ["JQUANTS"],
        "details": {"missing": profile["missing"]}}, cfg["logic_version"])
    assert db.load_quarterly_diagnostics(["1000"])[0]["attempted_sources"] == ["JQUANTS"]


def test_complete_official_series_wins_over_yahoo_fallback():
    cfg = load_config()
    official = [record(f"{year}-{month:02d}-30", 20 + i, 100 + i, 10 + i,
                       source="JQUANTS", period_type="YTD", quarter=f"Q{(i % 3) + 1}",
                       year=str(year))
                for i, (year, month) in enumerate(((2025, 3), (2025, 6), (2025, 9),
                                                   (2026, 3), (2026, 6), (2026, 9)))]
    yahoo = [record(f"{year}-{month:02d}-28", 100 + i, 500 + i, 80 + i,
                    source="YAHOO_QUARTERLY")
             for i, (year, month) in enumerate(((2025, 3), (2025, 6), (2025, 9),
                                                (2025, 12), (2026, 3), (2026, 6)))]
    profile = quarterly_profile([*official, *yahoo], cfg, "2026-09-01")
    assert profile["source"] == "JQUANTS"


def test_yahoo_statement_rows_are_normalized_with_item_diagnostics():
    statement = pd.DataFrame({
        pd.Timestamp("2026-06-30"): [31.0, 1300, 160, 120],
        pd.Timestamp("2025-06-30"): [20.0, 1000, 100, 80],
    }, index=["Basic EPS", "Total Revenue", "Operating Income", "Net Income"])
    rows, diagnostics = normalize_yahoo_quarterly_statement(statement, "2026-09-04")
    assert rows[-1]["basic_eps"] == 31
    assert rows[-1]["revenue"] == 1300
    assert rows[-1]["operating_profit"] == 160
    assert rows[-1]["net_income"] == 120
    assert diagnostics["revenue"] == {
        "status": "AVAILABLE", "item_name": "Total Revenue", "reason": None}


def test_yahoo_transposed_statement_and_empty_column_are_safe():
    statement = pd.DataFrame({
        "BasicEPS": [20.0, None], "Revenue": [1000, None],
        "OperatingEarnings": [100, None], "NetIncome": [80, None],
    }, index=[pd.Timestamp("2026-06-30"), "not-a-period"])
    rows, diagnostics = normalize_yahoo_quarterly_statement(statement, "2026-09-04")
    assert len(rows) == 1 and rows[0]["revenue"] == 1000
    assert diagnostics["operating_profit"]["item_name"] == "OperatingEarnings"


def test_yahoo_missing_item_is_not_inferred():
    statement = pd.DataFrame({pd.Timestamp("2026-06-30"): [20.0]}, index=["Basic EPS"])
    rows, diagnostics = normalize_yahoo_quarterly_statement(statement, "2026-09-04")
    assert rows[0]["revenue"] is None
    assert diagnostics["revenue"]["reason"] == "ITEM_NAME_NOT_FOUND"


def test_yahoo_empty_statement_keeps_field_level_missing_reason():
    rows, diagnostics = normalize_yahoo_quarterly_statement(pd.DataFrame(), "2026-09-04")
    assert rows == []
    assert all(item["reason"] == "STATEMENT_EMPTY" for item in diagnostics.values())


def test_yahoo_proxy_without_publication_date_is_not_used_before_retrieval():
    cfg = load_config()
    statement = pd.DataFrame({
        pd.Timestamp("2025-06-30"): [20.0, 1000, 100, 80],
        pd.Timestamp("2026-06-30"): [30.0, 1250, 140, 110],
    }, index=["BasicEPS", "TotalRevenue", "OperatingIncome", "NetIncome"])
    rows, _ = normalize_yahoo_quarterly_statement(statement, "2026-09-04")
    assert quarterly_profile(rows, cfg, "2026-09-03")["coverage"] == 0
    profile = quarterly_profile(rows, cfg, "2026-09-04")
    assert profile["available_from"] == "2026-09-04"
    assert profile["fidelity"] == "PROXY"


def test_quarterly_diagnostic_distinguishes_queue_and_source_failure():
    cfg = load_config()
    profile = quarterly_profile([], cfg, "2026-09-04")
    item = quarterly_diagnostic(
        "1000", profile, ["JQUANTS"], update_state="QUEUED_UPDATE_LIMIT",
        next_update_rank=81, source_attempts={"JQUANTS": "JQUANTS_AUTH_ERROR"},
        additional_reasons=["UPDATE_LIMIT_NOT_ATTEMPTED"])
    assert item["details"]["next_update_rank"] == 81
    assert item["details"]["source_attempts"]["JQUANTS"] == "JQUANTS_AUTH_ERROR"
    assert "UPDATE_LIMIT_NOT_ATTEMPTED" in item["reason_codes"]


def test_yahoo_calendar_eps_without_explicit_period_is_not_ordinally_assigned():
    rows = [record("2026-06-30", None, 100, 10, source="YAHOO_QUARTERLY")]
    earnings = pd.DataFrame({"Reported EPS": [30.0]},
                            index=[pd.Timestamp("2026-08-10")])
    merged, diagnostics = merge_yahoo_reported_eps(rows, earnings)
    assert merged[0]["basic_eps"] is None
    assert diagnostics["period_match_counts"] == {
        "MATCHED": 0, "AMBIGUOUS": 0, "UNMATCHED": 1}


def test_yahoo_calendar_eps_is_used_only_for_an_explicit_exact_period():
    rows = [record("2026-06-30", None, 100, 10, source="YAHOO_QUARTERLY")]
    earnings = pd.DataFrame({"Reported EPS": [30.0],
                             "Period End": [pd.Timestamp("2026-06-30")]},
                            index=[pd.Timestamp("2026-08-10")])
    merged, diagnostics = merge_yahoo_reported_eps(rows, earnings)
    assert merged[0]["basic_eps"] == 30
    assert merged[0]["eps_period_match_status"] == "MATCHED"
    assert diagnostics["period_match_counts"]["MATCHED"] == 1


def test_stock_split_warning_prevents_eps_growth_signal():
    cfg = load_config()
    rows = [record("2025-06-30", 10, 100, 10, source="YAHOO_QUARTERLY"),
            record("2026-06-30", None, 120, 12, source="YAHOO_QUARTERLY")]
    earnings = pd.DataFrame({"Reported EPS": [30.0],
                             "Period End": [pd.Timestamp("2026-06-30")]},
                            index=[pd.Timestamp("2026-08-10")])
    merged, _ = merge_yahoo_reported_eps(rows, earnings, [pd.Timestamp("2026-07-15")])
    profile = quarterly_profile(merged, cfg, "2026-09-01")
    assert profile["eps_growth"] is None
    assert "EPS_STOCK_SPLIT_WARNING" in profile["reason_codes"]


def test_legacy_yahoo_eps_without_match_metadata_is_not_used():
    cfg = load_config()
    rows = [record("2025-06-30", 10, 100, 10, source="YAHOO_QUARTERLY"),
            record("2026-06-30", 20, 120, 12, source="YAHOO_QUARTERLY")]
    profile = quarterly_profile(rows, cfg, "2026-09-01")
    assert profile["eps_growth"] is None
    assert "EPS_PERIOD_UNMATCHED" in profile["reason_codes"]


def test_split_between_comparable_statement_periods_is_flagged():
    rows = [record("2025-06-30", 10, 100, 10, source="YAHOO_QUARTERLY"),
            record("2026-06-30", 20, 120, 12, source="YAHOO_QUARTERLY")]
    for row in rows:
        row["eps_period_match_status"] = "MATCHED"
    merged, diagnostics = merge_yahoo_reported_eps(
        rows, pd.DataFrame(), [pd.Timestamp("2026-01-15")])
    assert merged[-1]["eps_continuity_warning"] == \
        "STOCK_SPLIT_BETWEEN_COMPARABLE_PERIODS"
    assert diagnostics["split_warning_count"] == 1
