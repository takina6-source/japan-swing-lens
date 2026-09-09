import json

import pandas as pd

from engine.config import load_config
from engine.database import Database
from engine.research.pipeline import (_evidence_rows, _research_events,
                                      export_research, run_research)
from engine.research.registry import RESEARCH_LOGIC_VERSION
from engine.research.storage import ResearchStore


def _frame(start="2026-09-10", periods=25, price=100.0):
    index = pd.bdate_range(start, periods=periods)
    close = [price + offset for offset in range(periods)]
    return pd.DataFrame({
        "open": [value - .2 for value in close],
        "high": [value + 1 for value in close],
        "low": [value - 1 for value in close],
        "close": close,
        "volume": [100_000.0] * periods,
        "atr14": [2.0] * periods,
    }, index=index)


def _long_frame(price=100.0):
    index = pd.bdate_range("2026-08-03", "2026-10-16")
    close = [price + offset * .1 for offset in range(len(index))]
    return pd.DataFrame({
        "open": [value - .1 for value in close], "high": [value + 1 for value in close],
        "low": [value - 1 for value in close], "close": close,
        "volume": [100_000.0] * len(index), "atr14": [2.0] * len(index),
    }, index=index)


def _insert_watch_snapshot(db, code="1000", aligned=5):
    signal_id = f"signal-{code}"
    pivot = {"Minervini": {"price": 100.5, "formed_date": "2026-09-08"}}
    plan = {"stop": 95.0}
    with db.connect() as con:
        con.execute("""INSERT INTO signal_snapshots
        (signal_id,setup_id,signal_date,code,stock_name,close,consensus_state,
         breakout_count,aligned_count,confluence,coverage,confidence,pivot_fidelity,
         momentum_percentile,trading_value_20d,current_trading_value,liquidity_level,
         benchmark_close,strategy_pivots_json,trade_plan_json,strategy_version,
         threshold_version,schema_version)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (signal_id, f"setup-{code}", "2026-09-10", code, f"Stock {code}",
                     100.0, "BREAKOUT WATCH", 0, aligned, aligned, 100.0, "HIGH",
                     "PRACTICAL", 90.0, 1e9, 1e9, "HIGH", 100.0,
                     json.dumps(pivot), json.dumps(plan), "core-v1", "threshold-v1", "3.1"))
        con.execute("""INSERT INTO signal_history
        (signal_id,date,session_offset,close,return_abs,benchmark_relative_return,
         mfe,mae,consensus_state,breakout_count,aligned_count,coverage,confidence)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (signal_id, "2026-09-11", 1, 101.0, 1.0, 1.0, 1.2, -.5,
                     "BREAKOUT", 2, aligned, 100.0, "HIGH"))
    return signal_id


def _core_bytes(db):
    tables = ("signal_snapshots", "signal_history", "control_members", "control_history",
              "experimental_snapshots", "experimental_history")
    with db.connect() as con:
        return {table: list(con.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                for table in tables}


def test_research_run_is_additive_idempotent_and_exports_required_files(tmp_path):
    db = Database(tmp_path / "research.db")
    _insert_watch_snapshot(db)
    frame = _frame()
    benchmark = _frame(price=2000)
    cfg = load_config()
    before = _core_bytes(db)

    first = run_research(db, [], {"1000": frame}, benchmark,
                         {"1000": {"market": "Prime", "size_class": "Mid"}}, cfg)
    second = run_research(db, [], {"1000": frame}, benchmark,
                          {"1000": {"market": "Prime", "size_class": "Mid"}}, cfg)

    assert _core_bytes(db) == before
    assert first["performance_metrics"]["new_research_events"] > 0
    assert second["performance_metrics"]["new_research_events"] == 0
    store = ResearchStore(db)
    assert len(store.rows("research_hypotheses")) == 4
    assert {row["data_origin"] for row in store.rows("validation_subjects")
            if row["subject_type"] == "RESEARCH_ENTRY_EVENT"} == {"LIVE_FORWARD"}
    assert {row["evaluation_phase"] for row in store.rows("validation_subjects")
            if row["subject_type"] == "RESEARCH_ENTRY_EVENT"} == {"HOLDOUT"}

    output = tmp_path / "research"
    index = export_research(db, output, cfg, second)
    required = {"index.json", "hypotheses.json", "families.json", "events.json",
                "events.csv", "performance.json", "performance.csv", "checkpoints.json",
                "intraday_diagnostics.json", "storage_metrics.json",
                "performance_metrics.json", "state.json"}
    assert required <= {path.name for path in output.iterdir()}
    assert index["core_ranking_affected"] is False
    assert index["trading_session_progress"] == {
        "elapsed": 25,
        "total": 90,
        "source": "BENCHMARK_TRADING_DATES",
        "latest_market_date": "2026-10-14",
    }
    assert index["event_data_origin_counts"]["LIVE_FORWARD"] > 0
    assert index["event_data_origin_counts"]["RECONSTRUCTED_LEGACY"] == 0
    assert index["formal_validation_event_count"] > 0
    assert index["formal_validation_subject_count"] > 0
    assert json.loads((output / "index.json").read_text())["hypothesis_count"] == 4


def test_h2_requires_five_of_five_watch_to_breakout(tmp_path):
    db = Database(tmp_path / "h2.db")
    _insert_watch_snapshot(db, "1000", aligned=5)
    _insert_watch_snapshot(db, "1001", aligned=4)
    frames = {"1000": _frame(), "1001": _frame(price=120)}
    snapshots, histories = db.validation_rows()
    events, subjects = _research_events(snapshots, histories, frames, _frame(price=2000),
                                         {}, load_config())
    store = ResearchStore(db)
    store.save_events(events)
    store.save_subjects([subject.to_dict() for subject in subjects])
    payloads = [event.get("payload") or json.loads(event["payload_json"])
                for event in store.rows("research_events")
                if event["event_type"] == "ENTRY_NEXT_OPEN"]
    assert sorted(payload["watch_to_breakout_5of5"] for payload in payloads) == [False, True]
    # Without controls/history neither event is outcome evidence; the eligibility flag is frozen.
    assert _evidence_rows(db, store)["H2"] == []


def test_research_controls_use_breakout_close_features_and_are_not_reselected(tmp_path):
    db = Database(tmp_path / "controls.db")
    _insert_watch_snapshot(db)
    frames = {code: _long_frame(price) for code, price in {
        "1000": 100, "1001": 90, "1002": 110, "1003": 130}.items()}
    cfg = load_config()
    cfg["controls"]["random_count"] = 1
    cfg["controls"]["matched_count"] = 1
    meta = {code: {"name": f"Stock {code}", "market": "Prime", "size_class": "Mid"}
            for code in frames}
    run_research(db, [], frames, _long_frame(2000), meta, cfg)
    store = ResearchStore(db)
    before = store.rows("research_control_members", "control_type,control_code")
    assert len(before) == 2
    assert all(row["feature_snapshot"]["selection_date"] == "2026-09-11" for row in before)
    assert all(row["feature_snapshot"]["execution_anchor_date"] == "2026-09-14"
               for row in before)
    hashes = [(row["control_code"], row["feature_hash"], row["anchor_price"])
              for row in before]
    frames["1001"].loc[pd.Timestamp("2026-09-14"), "close"] *= 10
    run_research(db, [], frames, _long_frame(2000), meta, cfg)
    after = store.rows("research_control_members", "control_type,control_code")
    assert hashes == [(row["control_code"], row["feature_hash"], row["anchor_price"])
                      for row in after]


def test_legacy_subject_never_becomes_holdout(tmp_path):
    db = Database(tmp_path / "legacy.db")
    with db.connect() as con:
        con.execute("""INSERT INTO signal_snapshots
        (signal_id,setup_id,signal_date,code,stock_name,close,consensus_state,
         breakout_count,aligned_count,confluence,coverage,confidence,pivot_fidelity,
         strategy_pivots_json,trade_plan_json,strategy_version,threshold_version,schema_version)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("legacy", "setup", "2026-09-04", "1000", "Legacy", 100,
                     "BREAKOUT", 2, 5, 5, 100, "HIGH", "PRACTICAL",
                     json.dumps({"x": {"price": 100, "formed_date": "2026-09-01"}}),
                     json.dumps({"stop": 95}), "core-v1", "threshold-v1", "3.1"))
    frame = _frame(start="2026-09-04")
    snapshots, histories = db.validation_rows()
    _, subjects = _research_events(snapshots, histories, {"1000": frame},
                                    _frame(start="2026-09-04", price=2000), {}, load_config())
    assert subjects
    assert all(subject.data_origin == "RECONSTRUCTED_LEGACY" for subject in subjects)
    assert all(subject.evaluation_phase == "DISCOVERY" for subject in subjects)


def test_hot_history_compaction_keeps_membership_and_fixed_horizons(tmp_path):
    db = Database(tmp_path / "compact.db")
    store = ResearchStore(db)
    store.save_control_members([{
        "control_group_id": "g1", "validation_subject_id": "s1", "control_code": "2000",
        "control_name": "Control", "control_type": "MATCHED", "control_rank": 1,
        "anchor_date": "2026-09-10", "anchor_price": 100, "price_basis": "OPEN",
        "selection_version": "research-control-v1", "feature_snapshot_json": "{}",
        "feature_hash": "fixed",
    }])
    store.save_control_members([{
        "control_group_id": "g1", "validation_subject_id": "s1", "control_code": "2000",
        "control_name": "Changed name", "control_type": "MATCHED", "control_rank": 1,
        "anchor_date": "2026-09-10", "anchor_price": 999, "price_basis": "OPEN",
        "selection_version": "research-control-v1", "feature_snapshot_json": "{}",
        "feature_hash": "changed",
    }])
    assert store.rows("research_control_members")[0]["anchor_price"] == 100
    dates = pd.bdate_range("2026-09-10", periods=21)
    store.save_control_history([{
        "control_group_id": "g1", "validation_subject_id": "s1", "control_code": "2000",
        "date": str(date.date()), "session_offset": offset, "close": 100 + offset,
        "return_abs": float(offset), "benchmark_relative_return": float(offset),
        "mfe": float(offset), "mae": 0.0,
    } for offset, date in enumerate(dates)])
    assert store.compact_matured_history() == 17
    assert [row["session_offset"] for row in store.rows(
        "research_control_history", "session_offset")] == [1, 5, 10, 20]
    assert len(store.rows("research_control_members")) == 1
    assert all(row["matured"] == 1 for row in store.rows("research_control_history"))


def test_public_state_round_trip_keeps_frozen_registry_and_events(tmp_path):
    source = Database(tmp_path / "source.db")
    _insert_watch_snapshot(source)
    result = run_research(source, [], {"1000": _frame()}, _frame(price=2000), {}, load_config())
    output = tmp_path / "research"
    export_research(source, output, load_config(), result)
    target = Database(tmp_path / "target.db")
    ResearchStore(target).import_state(json.loads((output / "state.json").read_text()))
    source_store, target_store = ResearchStore(source), ResearchStore(target)
    assert [(row["hypothesis_version"], row["definition_hash"])
            for row in target_store.rows("research_hypotheses", "hypothesis_version")] == [
        (row["hypothesis_version"], row["definition_hash"])
        for row in source_store.rows("research_hypotheses", "hypothesis_version")]
    assert [row["research_event_id"] for row in target_store.rows("research_events")] == [
        row["research_event_id"] for row in source_store.rows("research_events")]
    assert all(row["research_version"] == RESEARCH_LOGIC_VERSION
               for row in target_store.rows("research_events"))
