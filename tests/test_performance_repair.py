import json

import pandas as pd
import pytest

from engine.database import Database
from engine.performance_repair import action_candidates, repair_validation, revalue_observation
from engine.validation import _summary_rows, export_validation
from engine.config import load_config


def frame(first, second):
    return pd.DataFrame({
        "open": [first, second], "high": [first, second],
        "low": [first, second], "close": [first, second],
        "volume": [1000, 1000],
    }, index=pd.to_datetime(["2026-09-29", "2026-09-30"]))


@pytest.mark.parametrize("raw_entry,forward,adjusted_entry,ratio,expected", [
    (6000, 2100, 2000, 3.0, 5.0),
    (500, 2600, 2500, .2, 4.0),
])
def test_split_and_reverse_split(raw_entry, forward, adjusted_entry, ratio, expected):
    result = revalue_observation(
        {"date": "2026-09-30", "close": forward},
        {"date": "2026-09-29", "price": raw_entry},
        frame(adjusted_entry, forward), frame(100, 101),
        [("2026-09-30", ratio)])
    assert result["return_abs"] == pytest.approx(expected)
    assert result["performance_status"] == "CORPORATE_ACTION_ADJUSTED"
    assert result["corporate_action_flag"] == 1
    assert result["split_ratio"] == ratio


def test_no_action_unchanged_and_bad_adjusted_path_excluded():
    normal = revalue_observation(
        {"date": "2026-09-30", "close": 105},
        {"date": "2026-09-29", "price": 100}, frame(100, 105))
    assert normal["return_abs"] == pytest.approx(5)
    assert normal["performance_status"] == "OK"
    bad = revalue_observation(
        {"date": "2026-09-30", "close": 1_000_000},
        {"date": "2026-09-29", "price": 100}, frame(100, 1_000_000))
    assert bad["return_abs"] is None
    assert bad["performance_status"] == "ANOMALY_EXCLUDED"


def test_research_pivot_trigger_keeps_entry_rule_after_adjustment():
    result = revalue_observation(
        {"date": "2026-09-30", "close": 2100},
        {"date": "2026-09-29", "price": 6000, "adjusted_price": 2000},
        frame(1900, 2100), actions=[("2026-09-30", 3.0)])
    assert result["return_abs"] == pytest.approx(5)


def test_signal_random_matched_and_rerun(tmp_path):
    db = Database(tmp_path / "test.db")
    with db.connect() as con:
        con.execute("""INSERT INTO signal_snapshots
            (signal_id,signal_date,code,stock_name,close,consensus_state,
             breakout_count,aligned_count,coverage,confidence,pivot_fidelity)
             VALUES('s','2026-09-29','8766','test',6000,'BREAKOUT',2,3,100,'HIGH','PRACTICAL')""")
        con.execute("""INSERT INTO signal_history
            (signal_id,date,session_offset,close,return_abs)
            VALUES('s','2026-09-30',1,2100,-65)""")
        for kind in ("RANDOM", "MATCHED"):
            con.execute("""INSERT INTO control_members
                (control_group_id,signal_id,signal_date,control_code,control_type,initial_close)
                VALUES(?,?,?,?,?,?)""", (kind, "s", "2026-09-29", "8766", kind, 6000))
            con.execute("""INSERT INTO control_history
                (control_group_id,signal_id,control_code,control_type,date,
                 session_offset,close,return_abs)
                VALUES(?,?,?,?,?,?,?,?)""", (kind, "s", "8766", kind,
                                               "2026-09-30", 1, 2100, -65))
    frames = {"8766": frame(2000, 2100)}
    before_index = export_validation(db, tmp_path / "before", load_config())
    assert action_candidates(db, frames) == {"8766"}
    actions = {"8766": [("2026-09-30", 3.0)]}
    first = repair_validation(db, frames, frame(100, 101), actions)
    signals, history = db.validation_rows()
    controls, control_history = db.control_validation_rows()
    assert len(signals) == 1
    assert history[0]["return_abs"] == pytest.approx(5)
    assert all(row["return_abs"] == pytest.approx(5) for row in control_history)
    assert first["corporate_action_adjusted"] == 3
    after_index = export_validation(db, tmp_path / "after", load_config(), first)
    for key in ("signal_count", "history_count", "performance_count",
                "control_count", "control_performance_count", "summary_count"):
        assert after_index[key] == before_index[key]
    after_performance = json.loads((tmp_path / "after" / "performance.json").read_text())
    assert after_performance[0]["return_1d_pct"] == pytest.approx(5)
    assert after_performance[0]["random_return_mean_1d_pct"] == pytest.approx(5)
    assert after_performance[0]["matched_return_mean_1d_pct"] == pytest.approx(5)
    before = json.dumps((history, control_history), sort_keys=True)
    second = repair_validation(db, frames, frame(100, 101), actions)
    after = json.dumps((db.validation_rows()[1], db.control_validation_rows()[1]),
                       sort_keys=True)
    assert before == after
    assert first == second


def test_summary_excludes_anomaly_but_keeps_count():
    cfg = load_config()
    rows = [{"app_version": "v", "strategy_version": "v",
             "threshold_version": "v", "schema_version": "v",
             "initial_consensus_state": "BREAKOUT", "initial_breakout_count": 2,
             "return_1d_pct": 5, "performance_status_1d": "CORPORATE_ACTION_ADJUSTED"},
            {"app_version": "v", "strategy_version": "v",
             "threshold_version": "v", "schema_version": "v",
             "initial_consensus_state": "BREAKOUT", "initial_breakout_count": 2,
             "return_1d_pct": None, "performance_status_1d": "ANOMALY_EXCLUDED"}]
    summary = _summary_rows(rows, cfg)
    all_1d = next(row for row in summary if row["dimension"] == "ALL"
                  and row["horizon_days"] == 1)
    assert all_1d["sample_count"] == all_1d["valid_n"] == 1
    assert all_1d["excluded_anomaly_n"] == 1
    assert all_1d["average_return_pct"] == 5
