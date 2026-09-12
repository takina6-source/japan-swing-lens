#!/usr/bin/env python3
"""Benchmark the final Phase 2A store with deterministic isolated SQLite files."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from engine.state_machine.ids import canonical_json, event_uid
from engine.state_machine.storage import Phase2ASeedError, Phase2AStore, RunBundle


SCOPE_SIZE = 1_000
CURRENT_SIZE = 995
RUNS = 10
EVENT_RATES = (0.0, 0.10, 0.25)
REVISION_RATES = (0.0, 0.15, 0.50)
JOURNAL_MODES = ("DELETE", "WAL")
BASE_DATE = dt.date(2026, 1, 5)


def digest(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    point = (len(ordered) - 1) * pct
    low = int(point)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] * (1 - (point - low)) + ordered[high] * (point - low)


def code_at(index: int) -> str:
    return f"{1000 + index:04d}"


def setup_uid(code: str) -> str:
    return f"csu1:{code}:{digest('setup', code)[:40]}"


def strategy_uid(code: str, strategy: str) -> str:
    return f"ssu1:{code}:{strategy}:{digest('strategy', code, strategy)[:40]}"


def observation_id(run_id: str, code: str) -> str:
    return f"obs1:{code}:{digest('observation', run_id, code)[:40]}"


def run_row(
    run_id: str, date: str, session: int, input_hash: str, *, transition_total: int,
) -> dict[str, Any]:
    return {
        "run_id": run_id, "run_kind": "BENCHMARK", "state_lineage": "benchmark-sm1",
        "processing_status": "COMPLETE", "coverage_status": "DEGRADED",
        "publish_eligibility": "ELIGIBLE", "state_commit_status": "COMMITTED",
        "started_at": f"{date}T07:00:00+00:00", "finished_at": f"{date}T07:01:00+00:00",
        "expected_market_date": date, "market_session_index": session,
        "scope_name": "benchmark-1000", "scope_member_sha256": digest("scope-1000"),
        "scope_total": SCOPE_SIZE, "current_total": CURRENT_SIZE,
        "no_new_market_total": 0, "stale_total": 4, "insufficient_total": 1,
        "fetch_failed_total": 0, "analysis_failed_total": 0, "out_of_scope_total": 0,
        "invalid_input_total": 0, "identity_link_total": CURRENT_SIZE,
        "identity_mint_total": 0, "identity_ambiguous_total": 0,
        "no_setup_total": 0, "transition_total": transition_total,
        "unchanged_total": CURRENT_SIZE, "rejected_total": 0,
        "input_sha256": input_hash, "seed_sha256": digest("benchmark-seed"),
        "seed_schema_version": "phase2a-seed-v1", "seed_row_count": CURRENT_SIZE,
        "versions_json": canonical_json({
            "state_machine_version": "sm1", "threshold_version": "smt1",
            "observation_schema_version": "smo1", "event_schema_version": "sev1",
        }),
        "reason_codes_json": "[]", "structured_errors_json": "[]",
    }


def scope_rows(run_id: str, date: str) -> tuple[dict[str, Any], ...]:
    rows = []
    for index in range(SCOPE_SIZE):
        current = index < CURRENT_SIZE
        status = "CURRENT" if current else ("STALE_MARKET_DATE" if index < 999 else "INSUFFICIENT_PRICE_HISTORY")
        rows.append({
            "run_id": run_id, "code": code_at(index), "in_scope": 1,
            "observation_status": status,
            "analysis_date": date if current else None,
            "latest_price_date": date if current else "2026-01-02",
            "price_history_count": 260 if index < 999 else 184,
            "required_price_history_count": 200,
            "reason_codes_json": "[]" if current else canonical_json([status]),
            "structured_error_ref": None,
            "observation_uid": observation_id(run_id, code_at(index)) if current else None,
        })
    return tuple(rows)


def observation_row(run_id: str, date: str, session: int, index: int, revision: int) -> dict[str, Any]:
    code = code_at(index)
    uid = setup_uid(code)
    obs = observation_id(run_id, code)
    price = 1000.0 + index / 10
    return {
        "observation_uid": obs, "run_id": run_id, "namespace": "CORE", "code": code,
        "analysis_date": date, "expected_market_date": date,
        "market_session_index": session, "observed_at": f"{date}T07:00:00+00:00",
        "close": price, "previous_accepted_close": price - 1,
        "core_observed_state": "BREAKOUT WATCH",
        "trend_strategy_states_json": canonical_json({
            "Minervini": "BREAKOUT WATCH", "Qullamaggie": "SETUP FORMING",
            "CAN SLIM": "NOT QUALIFIED", "Weinstein": "NOT QUALIFIED",
            "Darvas": "NOT QUALIFIED",
        }),
        "connors_state": "NOT QUALIFIED", "aligned_trend_strategy_count": 2,
        "breakout_trend_strategy_count": 0, "observed_pivot_price": price + revision,
        "observed_pivot_strategy": "minervini", "observed_pivot_type": "practical",
        "observed_pivot_basis": "T_MINUS_1_OHLCV", "observed_pivot_fidelity": "PRACTICAL",
        "observed_pivot_reference_date": "2026-01-02", "observation_status": "CURRENT",
        "decision_slot": "core-primary", "core_setup_uid": uid,
        "tracking_pivot_revision_no": revision,
        "source_versions_json": canonical_json({"logic_version": "benchmark-v1"}),
        "legacy_refs_json": canonical_json({"legacy_setup_id": f"legacy-{code}-{session}"}),
        "reason_codes_json": "[]", "input_sha256": digest(run_id, code, revision),
    }


def decision_row(run_id: str, index: int, *, mint: bool) -> dict[str, Any]:
    code = code_at(index)
    obs = observation_id(run_id, code)
    return {
        "decision_uid": f"sid1:{code}:{digest('decision', run_id, code)[:40]}",
        "observation_uid": obs, "code": code, "namespace": "CORE",
        "decision_slot": "core-primary", "decision": "MINT" if mint else "LINK",
        "target_core_setup_uid": setup_uid(code),
        "identity_epoch": "benchmark-seed" if mint else None,
        "origin_slot": "core-primary", "decision_rule_version": "idr1",
        "blocker": "NONE", "reason_codes_json": "[]",
        "evidence_refs_json": canonical_json([obs]), "supersedes_decision_uid": None,
    }


def event_row(
    run_id: str, date: str, session: int, index: int, event_type: str,
    ordinal: int, transition: bool,
) -> dict[str, Any]:
    code = code_at(index)
    uid = setup_uid(code)
    obs = observation_id(run_id, code)
    event_id, request_hash = event_uid(
        code=code, core_setup_uid=uid, event_type=event_type,
        observation_uid_value=obs, state_machine_version="sm1", occurrence_ordinal=ordinal,
    )
    evidence = digest("evidence", event_id)
    return {
        "event_uid": event_id, "event_request_sha256": request_hash,
        "event_type": event_type, "occurrence_ordinal": ordinal,
        "core_setup_uid": uid, "from_phase": None if event_type == "SETUP_MINTED" else "WATCH",
        "to_phase": "WATCH", "is_phase_transition": int(transition),
        "effective_date": date, "effective_date_status": "EXACT_CURRENT_OBSERVATION",
        "detected_at": f"{date}T07:01:00+00:00", "observation_uid": obs,
        "permanent_exit_evidence_ref": None, "tracking_pivot_revision_no": ordinal,
        "prior_related_event_uid": None, "state_machine_version": "sm1",
        "threshold_version": "smt1", "transition_id": "T02" if transition else "T17",
        "reason_codes_json": "[]", "evidence_sha256": evidence,
        "idempotency_key": f"sm1|{uid}|{event_type}|{obs}|{ordinal}",
        "producer_run_id": run_id, "corrects_event_uid": None,
    }


def seed_bundle() -> tuple[RunBundle, dict[str, int], dict[str, int], dict[str, str]]:
    date = "2026-01-02"
    run_id = "benchmark-seed-run"
    versions = {code_at(index): 1 for index in range(CURRENT_SIZE)}
    revisions = {code_at(index): 1 for index in range(CURRENT_SIZE)}
    latest_events: dict[str, str] = {}
    ledger = []
    strategy_ledger = []
    pivots = []
    memberships = []
    events = []
    states = []
    anchors = []
    for index in range(CURRENT_SIZE):
        code = code_at(index)
        uid = setup_uid(code)
        obs = observation_id(run_id, code)
        mint_hash = digest("mint", code)
        ledger.append({
            "core_setup_uid": uid, "code": code, "namespace": "CORE_SETUP",
            "mint_request_sha256": mint_hash,
            "canonical_mint_request": canonical_json({"code": code, "birth": obs}),
            "identity_epoch": "benchmark-seed", "origin_observation_uid": obs,
            "origin_slot": "core-primary", "identity_version": "csu1",
            "created_run_id": run_id, "created_at": f"{date}T07:00:00+00:00", "terminal": 0,
        })
        for strategy in ("minervini", "qullamaggie"):
            strategy_ledger.append({
                "strategy_setup_uid": strategy_uid(code, strategy), "code": code,
                "strategy_code": strategy, "mint_request_sha256": digest("smint", code, strategy),
                "canonical_mint_request": canonical_json({"code": code, "strategy": strategy, "birth": obs}),
                "identity_epoch": "benchmark-seed", "origin_observation_uid": obs,
                "identity_version": "ssu1", "created_run_id": run_id,
                "created_at": f"{date}T07:00:00+00:00",
            })
            memberships.append({
                "core_setup_uid": uid, "strategy_code": strategy,
                "strategy_setup_uid": strategy_uid(code, strategy),
                "member_from_observation_uid": obs, "member_to_observation_uid": None,
                "valid_from_market_session_index": 0, "valid_to_market_session_index": None,
                "evidence_sha256": digest("membership", code, strategy),
            })
        pivots.append({
            "core_setup_uid": uid, "revision_no": 1,
            "tracking_pivot_price": 1000.0 + index / 10, "strategy": "minervini",
            "pivot_type": "practical", "basis": "T_MINUS_1_OHLCV", "fidelity": "PRACTICAL",
            "reference_date": "2025-12-30", "effective_observation_uid": obs,
            "valid_from_market_session_index": 0, "valid_to_market_session_index": None,
            "frozen_after_breakout": 0, "evidence_sha256": digest("pivot", code, 1),
        })
        event = event_row(run_id, date, 0, index, "SETUP_MINTED", 1, True)
        events.append(event)
        latest_events[code] = event["event_uid"]
        anchors.append({
            "core_setup_uid": uid, "anchor_type": "MINT", "event_uid": event["event_uid"],
            "effective_date": date, "market_session_index": 0,
            "occurrence_ordinal": 1, "evidence_sha256": event["evidence_sha256"],
        })
        states.append(state_row(run_id, date, 0, index, 1, 1, latest_events[code], expected=0))
    run = run_row(run_id, date, 0, digest("seed-input"), transition_total=CURRENT_SIZE)
    run["identity_link_total"] = 0
    run["identity_mint_total"] = CURRENT_SIZE
    return RunBundle(
        run=run, scope_members=scope_rows(run_id, date),
        observations=tuple(observation_row(run_id, date, 0, i, 1) for i in range(CURRENT_SIZE)),
        ledger_rows=tuple(ledger), strategy_ledger_rows=tuple(strategy_ledger),
        decisions=tuple(decision_row(run_id, i, mint=True) for i in range(CURRENT_SIZE)),
        pivot_revisions=tuple(pivots), memberships=tuple(memberships),
        events=tuple(events), current_states=tuple(states), event_anchors=tuple(anchors),
    ), versions, revisions, latest_events


def state_row(
    run_id: str, date: str, session: int, index: int, state_version: int,
    revision: int, latest_event: str, *, expected: int,
) -> dict[str, Any]:
    code = code_at(index)
    return {
        "state_lineage": "benchmark-sm1", "core_setup_uid": setup_uid(code), "code": code,
        "current_phase": "WATCH", "continuity_status": "CONTIGUOUS",
        "distribution_eligible": 1, "phase_entered_observation_uid": observation_id("benchmark-seed-run", code),
        "phase_entered_effective_date": "2026-01-02",
        "latest_accepted_observation_uid": observation_id(run_id, code),
        "latest_accepted_market_session_index": session, "latest_accepted_date": date,
        "latest_input_sha256": digest(run_id, code, revision),
        "latest_accepted_close": 1000.0 + index / 10,
        "tracking_pivot_revision_no": revision, "latest_event_uid": latest_event,
        "latest_breakout_event_uid": None, "latest_failure_event_uid": None,
        "minted_market_session_index": 0, "latest_breakout_market_session_index": None,
        "breakout_count": 0, "failure_cycle_no": 0, "closure_reason_code": None,
        "closure_evidence_ref": None, "state_machine_version": "sm1",
        "threshold_version": "smt1", "state_version": state_version,
        "updated_run_id": run_id, "expected_state_version": expected,
    }


def daily_bundle(
    run_number: int, event_rate: float, revision_rate: float,
    versions: dict[str, int], revisions: dict[str, int], latest_events: dict[str, str],
) -> RunBundle:
    date = (BASE_DATE + dt.timedelta(days=run_number)).isoformat()
    session = run_number + 1
    run_id = f"benchmark-run-{run_number + 1:02d}"
    event_count = int(CURRENT_SIZE * event_rate)
    revision_count = int(CURRENT_SIZE * revision_rate)
    observations = []
    decisions = []
    pivots = []
    pivot_closures = []
    events = []
    states = []
    for index in range(CURRENT_SIZE):
        code = code_at(index)
        old_revision = revisions[code]
        if index < revision_count:
            revisions[code] += 1
            pivot_closures.append({
                "core_setup_uid": setup_uid(code), "revision_no": old_revision,
                "valid_to_market_session_index": session - 1,
            })
            pivots.append({
                "core_setup_uid": setup_uid(code), "revision_no": revisions[code],
                "tracking_pivot_price": 1000.0 + index / 10 + revisions[code],
                "strategy": "minervini", "pivot_type": "practical",
                "basis": "T_MINUS_1_OHLCV", "fidelity": "PRACTICAL",
                "reference_date": (BASE_DATE + dt.timedelta(days=run_number - 1)).isoformat(),
                "effective_observation_uid": observation_id(run_id, code),
                "valid_from_market_session_index": session,
                "valid_to_market_session_index": None, "frozen_after_breakout": 0,
                "evidence_sha256": digest("pivot", code, revisions[code]),
            })
        observations.append(observation_row(run_id, date, session, index, revisions[code]))
        decisions.append(decision_row(run_id, index, mint=False))
        if index < event_count:
            event = event_row(
                run_id, date, session, index, "CORRECTION_RECORDED",
                run_number + 1, False,
            )
            events.append(event)
            latest_events[code] = event["event_uid"]
        expected = versions[code]
        versions[code] += 1
        states.append(state_row(
            run_id, date, session, index, versions[code], revisions[code],
            latest_events[code], expected=expected,
        ))
    input_hash = digest("run", run_number, event_rate, revision_rate)
    return RunBundle(
        run=run_row(run_id, date, session, input_hash, transition_total=0),
        scope_members=scope_rows(run_id, date), observations=tuple(observations),
        decisions=tuple(decisions), pivot_revisions=tuple(pivots),
        pivot_closures=tuple(pivot_closures), events=tuple(events),
        current_states=tuple(states),
    )


def file_bytes(path: Path) -> int:
    return sum(
        candidate.stat().st_size for suffix in ("", "-wal", "-shm", "-journal")
        if (candidate := Path(str(path) + suffix)).exists()
    )


def attribution(store: Phase2AStore) -> dict[str, int]:
    with store.connect() as conn:
        try:
            rows = conn.execute(
                "SELECT name,sum(pgsize) FROM dbstat WHERE name LIKE 'phase2a_%' GROUP BY name"
            ).fetchall()
        except Exception:
            return {}
    return {row[0]: int(row[1]) for row in rows}


def fallback_attribution(path: Path, root: Path) -> dict[str, int]:
    """Non-additive table estimates for SQLite builds without dbstat."""
    original = path.stat().st_size
    tables = (
        "phase2a_runs", "phase2a_scope_members", "phase2a_identity_ledger",
        "phase2a_strategy_identity_ledger", "phase2a_observations",
        "phase2a_identity_decisions", "phase2a_pivot_revisions",
        "phase2a_strategy_memberships", "phase2a_events", "phase2a_current_states",
    )
    result: dict[str, int] = {}
    for table in tables:
        copy = root / f"attribution-{table}.db"
        shutil.copy2(path, copy)
        import sqlite3
        conn = sqlite3.connect(copy)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(f"DROP TABLE {table}")
        conn.execute("VACUUM")
        conn.close()
        result[table] = original - copy.stat().st_size
        copy.unlink()
    return result


def run_scenario(root: Path, journal: str, event_rate: float, revision_rate: float) -> dict[str, Any]:
    name = f"e{int(event_rate * 100):02d}_r{int(revision_rate * 100):02d}_{journal.lower()}"
    path = root / f"{name}.db"
    store = Phase2AStore(path, journal_mode=journal)
    store.migrate(applied_at="2026-01-02T07:00:00+00:00")
    seed, versions, revisions, latest_events = seed_bundle()
    store.commit_run(seed)
    with store.connect() as conn:
        if journal == "WAL":
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    seed_bytes = file_bytes(path)
    timings = []
    peak_aux = 0
    last_bundle = None
    for run_number in range(RUNS):
        bundle = daily_bundle(
            run_number, event_rate, revision_rate, versions, revisions, latest_events
        )
        started = time.perf_counter()
        store.commit_run(bundle)
        timings.append((time.perf_counter() - started) * 1000)
        peak_aux = max(
            peak_aux,
            sum(Path(str(path) + suffix).stat().st_size for suffix in ("-wal", "-shm") if Path(str(path) + suffix).exists()),
        )
        last_bundle = bundle
    with store.connect() as conn:
        if journal == "WAL":
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    final_bytes = file_bytes(path)
    increment = final_bytes - seed_bytes
    annual = increment / RUNS / SCOPE_SIZE * 965 * 250
    return {
        "name": name, "journal_mode": journal, "event_rate": event_rate,
        "revision_rate": revision_rate, "seed_bytes": seed_bytes,
        "incremental_10_run_bytes": increment, "annualized_bytes": annual,
        "p50_ms": percentile(timings, 0.50), "p95_ms": percentile(timings, 0.95),
        "peak_wal_shm_bytes": peak_aux, "integrity": store.integrity(),
        "table_bytes": attribution(store), "path": str(path),
        "last_bundle": last_bundle,
    }


def public_artifact_size() -> dict[str, int]:
    payload = {
        "schema_version": "phase2a-shadow-public-equivalent-v1",
        "setups": [
            {"core_setup_uid": setup_uid(code_at(i)), "code": code_at(i), "phase": "WATCH"}
            for i in range(CURRENT_SIZE)
        ],
        "events": [
            {"event_uid": digest("public-event", i), "code": code_at(i % CURRENT_SIZE), "type": "WATCH_ENTERED"}
            for i in range(3_000)
        ],
    }
    raw = canonical_json(payload).encode()
    return {"raw_bytes": len(raw), "gzip_bytes": len(gzip.compress(raw, compresslevel=9))}


def seed_roundtrip(store: Phase2AStore, root: Path) -> dict[str, Any]:
    before_hash = store.state_hash(lineage="benchmark-sm1")
    started = time.perf_counter()
    manifest = store.export_seed(root / "representative-seed", lineage="benchmark-sm1")
    export_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    Phase2AStore.verify_seed(root / "representative-seed")
    verify_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    restored = Phase2AStore.restore_seed(
        root / "representative-seed", root / "restored.db", journal_mode="WAL"
    )
    restore_ms = (time.perf_counter() - started) * 1000
    after_hash = restored.state_hash(lineage="benchmark-sm1")
    return {
        "export_ms": export_ms, "verify_ms": verify_ms, "restore_ms": restore_ms,
        "seed_bytes": (root / "representative-seed/seed.sqlite").stat().st_size,
        "row_counts": manifest["row_counts"], "state_hash_equal": before_hash == after_hash,
        "integrity": restored.integrity(),
    }


def atomic_retry(source_path: Path, root: Path, bundle: RunBundle) -> dict[str, Any]:
    path = root / "atomic.db"
    shutil.copy2(source_path, path)
    store = Phase2AStore(path, journal_mode="WAL")
    before = store.table_counts()
    failed = False
    try:
        store.commit_run(bundle, fail_after_stage="EVENT_INSERT")
    except Exception:
        failed = True
    after_failure = store.table_counts()
    committed = store.commit_run(bundle)
    no_op = store.commit_run(bundle)
    after_retry = store.table_counts()
    return {
        "failure_injected": failed, "rollback_counts_equal": before == after_failure,
        "retry_result": committed, "repeat_result": no_op,
        "run_delta": after_retry["phase2a_runs"] - before["phase2a_runs"],
        "integrity": store.integrity(),
    }


def benchmark() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="phase2a-final-benchmark-") as temporary:
        root = Path(temporary)
        scenarios = []
        representative = None
        for journal in JOURNAL_MODES:
            for event_rate in EVENT_RATES:
                for revision_rate in REVISION_RATES:
                    scenario = run_scenario(root, journal, event_rate, revision_rate)
                    scenarios.append(scenario)
                    if scenario["name"] == "e10_r15_wal":
                        representative = scenario
        assert representative is not None
        rep_store = Phase2AStore(representative["path"], journal_mode="WAL")
        if not representative["table_bytes"]:
            representative["table_bytes"] = fallback_attribution(
                Path(representative["path"]), root
            )
        seed_result = seed_roundtrip(rep_store, root)
        retry_bundle = daily_bundle(
            RUNS + 1, 0.10, 0.15,
            {code_at(i): RUNS + 1 for i in range(CURRENT_SIZE)},
            {code_at(i): 1 + int(CURRENT_SIZE * 0.15 > i) * RUNS for i in range(CURRENT_SIZE)},
            {code_at(i): event_row("benchmark-seed-run", "2026-01-02", 0, i, "SETUP_MINTED", 1, True)["event_uid"] for i in range(CURRENT_SIZE)},
        )
        atomic = atomic_retry(Path(representative["path"]), root, retry_bundle)
        missing_seed_failed = False
        try:
            Phase2AStore.verify_seed(root / "missing-seed")
        except Phase2ASeedError:
            missing_seed_failed = True
        clean_scenarios = [
            {key: value for key, value in scenario.items() if key not in {"path", "last_bundle"}}
            for scenario in scenarios
        ]
        annual_values = [row["annualized_bytes"] for row in clean_scenarios]
        result = {
            "benchmark_version": "phase2a-final-physical-v1",
            "scope_size": SCOPE_SIZE, "current_size": CURRENT_SIZE,
            "runs_per_scenario": RUNS, "scenario_count": len(clean_scenarios),
            "scenarios": clean_scenarios,
            "representative": next(row for row in clean_scenarios if row["name"] == "e10_r15_wal"),
            "annualized_range_bytes": {"min": min(annual_values), "max": max(annual_values)},
            "seed_roundtrip": seed_result, "atomic_retry": atomic,
            "missing_seed_failed_closed": missing_seed_failed,
            "public_artifact": public_artifact_size(),
            "capacity_gate": "PASS" if max(annual_values) <= 540_000_000 else "REVIEW_REQUIRED",
        }
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["capacity_gate"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
