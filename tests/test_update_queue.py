from engine.database import Database
from engine.update_queue import (attempt_record, build_fair_update_queue,
                                 classify_attempt_outcome)


def test_queue_prioritizes_never_attempted_then_oldest_then_least_data():
    attempts = {
        "B": [attempt_record("B", "ANNUAL_EPS", "YAHOO", "YAHOO_NO_DATA",
                             "2026-08-01T00:00:00+00:00")],
        "C": [attempt_record("C", "ANNUAL_EPS", "YAHOO", "YAHOO_NO_DATA",
                             "2026-08-10T00:00:00+00:00")],
    }
    ordered, meta = build_fair_update_queue(
        ["A", "B", "C"], attempts, {"A": 2, "B": 1, "C": 0},
        {"A": 10, "B": 90, "C": 100}, 2,
        {code: ["YAHOO"] for code in ("A", "B", "C")}, "2026-09-10")
    assert ordered == ["A", "B", "C"]
    assert meta["A"]["queue_reason"] == "NEVER_ATTEMPTED"
    assert meta["C"]["update_state"] == "QUEUED_UPDATE_LIMIT"


def test_momentum_is_only_the_final_tie_breaker():
    ordered, _ = build_fair_update_queue(
        ["LOW", "HIGH"], {}, {"LOW": 0, "HIGH": 0},
        {"LOW": 5, "HIGH": 95}, 2,
        {"LOW": ["YAHOO"], "HIGH": ["YAHOO"]}, "2026-09-10")
    assert ordered == ["HIGH", "LOW"]


def test_cooldown_keeps_transient_and_configuration_failures_distinct():
    assert classify_attempt_outcome("YAHOO_API_ERROR") == "TRANSIENT_FAILURE"
    assert classify_attempt_outcome("JQUANTS_NOT_CONFIGURED") == "CONFIGURATION_REQUIRED"
    transient = attempt_record("A", "QUARTERLY_FUNDAMENTALS", "YAHOO",
                               "YAHOO_API_ERROR", "2026-09-10T00:00:00+00:00")
    configured = attempt_record("B", "QUARTERLY_FUNDAMENTALS", "JQUANTS",
                                "JQUANTS_NOT_CONFIGURED", "2026-09-10T00:00:00+00:00")
    assert transient["next_eligible_at"] == "2026-09-11"
    assert configured["next_eligible_at"] == "2026-10-10"


def test_update_attempt_database_migrates_and_roundtrips(tmp_path):
    db = Database(tmp_path / "queue.db")
    row = attempt_record("1000", "ANNUAL_EPS", "YAHOO", "SUCCESS",
                         "2026-09-01T00:00:00+00:00")
    db.save_update_attempt(row)
    db.save_update_attempt({**row, "last_attempt_at": "2026-09-02T00:00:00+00:00"})
    loaded = db.load_update_attempts("ANNUAL_EPS", ["1000"])["1000"][0]
    assert loaded["outcome"] == "SUCCESS"
    assert loaded["attempt_count"] == 2
    assert loaded["last_attempt_at"].startswith("2026-09-02")
