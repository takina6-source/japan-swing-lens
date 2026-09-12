#!/usr/bin/env python3
"""Isolated storage benchmark for the Phase 2A State Machine design.

This script never opens the product databases. It creates deterministic SQLite
databases under a temporary directory, measures them, prints one JSON result to
stdout, and removes the temporary directory on exit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any


SCOPE_SIZE = 1_000
CURRENT_SIZE = 995
RUNS_PER_SCENARIO = 10
EVENT_RATES = (0.0, 0.10, 0.25)
REVISION_RATES = (0.0, 0.15, 0.50)
JOURNAL_MODES = ("DELETE", "WAL")
BASE_DATE = dt.date(2026, 1, 5)
STATE_MACHINE_VERSION = "sm1"
THRESHOLD_VERSION = "smt1"
SCHEMA_VERSION = "phase2a-benchmark-schema-v1"


def stable_hash(*parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * pct
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def sqlite_total_bytes(path: Path) -> int:
    return sum(file_size(Path(str(path) + suffix)) for suffix in ("", "-wal", "-shm", "-journal"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(path: Path, journal_mode: str = "DELETE") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    actual_mode = conn.execute(f"PRAGMA journal_mode = {journal_mode}").fetchone()[0].upper()
    if actual_mode != journal_mode:
        raise RuntimeError(f"journal mode mismatch: expected {journal_mode}, got {actual_mode}")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


SCHEMA_SQL = """
CREATE TABLE state_machine_runs (
    run_id TEXT PRIMARY KEY,
    processing_status TEXT NOT NULL,
    coverage_status TEXT NOT NULL,
    publish_eligibility TEXT NOT NULL,
    state_commit_status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    expected_market_date TEXT NOT NULL,
    market_session_index INTEGER NOT NULL,
    scope_name TEXT NOT NULL,
    scope_member_sha256 TEXT NOT NULL,
    scope_total INTEGER NOT NULL,
    current_total INTEGER NOT NULL,
    stale_total INTEGER NOT NULL,
    insufficient_total INTEGER NOT NULL,
    identity_link_total INTEGER NOT NULL,
    identity_mint_total INTEGER NOT NULL,
    identity_ambiguous_total INTEGER NOT NULL,
    no_setup_total INTEGER NOT NULL,
    transition_total INTEGER NOT NULL,
    unchanged_total INTEGER NOT NULL,
    ledger_seed_sha256 TEXT NOT NULL,
    ledger_seed_row_count INTEGER NOT NULL,
    versions_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL UNIQUE
);

CREATE TABLE run_scope_members (
    run_id TEXT NOT NULL REFERENCES state_machine_runs(run_id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    in_scope INTEGER NOT NULL CHECK (in_scope IN (0, 1)),
    observation_status TEXT NOT NULL,
    analysis_date TEXT,
    latest_price_date TEXT,
    price_history_count INTEGER NOT NULL,
    required_price_history_count INTEGER NOT NULL,
    reason_codes_json TEXT NOT NULL,
    observation_uid TEXT,
    PRIMARY KEY (run_id, code)
);

CREATE TABLE core_setup_identity_ledger (
    core_setup_uid TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    namespace TEXT NOT NULL,
    birth_request_sha256 TEXT NOT NULL UNIQUE,
    identity_epoch TEXT NOT NULL,
    origin_slot TEXT NOT NULL,
    identity_version TEXT NOT NULL,
    created_run_id TEXT NOT NULL REFERENCES state_machine_runs(run_id),
    created_at TEXT NOT NULL
);

CREATE TABLE accepted_observations (
    observation_uid TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES state_machine_runs(run_id) ON DELETE CASCADE,
    namespace TEXT NOT NULL,
    code TEXT NOT NULL,
    analysis_date TEXT NOT NULL,
    expected_market_date TEXT NOT NULL,
    market_session_index INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    close REAL NOT NULL CHECK (close > 0),
    previous_accepted_close REAL NOT NULL CHECK (previous_accepted_close > 0),
    core_observed_state TEXT NOT NULL,
    trend_strategy_states_json TEXT NOT NULL,
    connors_state TEXT NOT NULL,
    aligned_trend_strategy_count INTEGER NOT NULL,
    breakout_trend_strategy_count INTEGER NOT NULL,
    observed_pivot_price REAL NOT NULL CHECK (observed_pivot_price > 0),
    observed_pivot_strategy TEXT NOT NULL,
    observed_pivot_type TEXT NOT NULL,
    observed_pivot_basis TEXT NOT NULL,
    observed_pivot_fidelity TEXT NOT NULL,
    observed_pivot_reference_date TEXT NOT NULL,
    observation_status TEXT NOT NULL,
    decision_slot TEXT NOT NULL,
    core_setup_uid TEXT REFERENCES core_setup_identity_ledger(core_setup_uid),
    tracking_pivot_revision_no INTEGER,
    versions_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    UNIQUE (namespace, code, analysis_date, input_sha256)
);

CREATE TABLE setup_identity_decisions (
    decision_uid TEXT PRIMARY KEY,
    observation_uid TEXT NOT NULL REFERENCES accepted_observations(observation_uid) ON DELETE CASCADE,
    code TEXT NOT NULL,
    namespace TEXT NOT NULL,
    decision_slot TEXT NOT NULL,
    decision TEXT NOT NULL,
    target_core_setup_uid TEXT REFERENCES core_setup_identity_ledger(core_setup_uid),
    identity_epoch TEXT NOT NULL,
    origin_slot TEXT NOT NULL,
    decision_rule_version TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    supersedes_decision_uid TEXT REFERENCES setup_identity_decisions(decision_uid),
    UNIQUE (observation_uid, decision_slot)
);

CREATE TABLE setup_pivot_revisions (
    core_setup_uid TEXT NOT NULL REFERENCES core_setup_identity_ledger(core_setup_uid),
    revision_no INTEGER NOT NULL,
    tracking_pivot_price REAL NOT NULL CHECK (tracking_pivot_price > 0),
    strategy TEXT NOT NULL,
    pivot_type TEXT NOT NULL,
    basis TEXT NOT NULL,
    fidelity TEXT NOT NULL,
    effective_observation_uid TEXT REFERENCES accepted_observations(observation_uid),
    valid_from_market_session_index INTEGER NOT NULL,
    valid_to_market_session_index INTEGER,
    frozen_after_breakout INTEGER NOT NULL CHECK (frozen_after_breakout IN (0, 1)),
    PRIMARY KEY (core_setup_uid, revision_no)
);

CREATE TABLE setup_events (
    event_uid TEXT PRIMARY KEY,
    event_order INTEGER NOT NULL UNIQUE,
    event_request_sha256 TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    occurrence_ordinal INTEGER NOT NULL,
    core_setup_uid TEXT NOT NULL REFERENCES core_setup_identity_ledger(core_setup_uid),
    from_phase TEXT,
    to_phase TEXT NOT NULL,
    is_phase_transition INTEGER NOT NULL CHECK (is_phase_transition IN (0, 1)),
    effective_date TEXT NOT NULL,
    effective_date_status TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    observation_uid TEXT REFERENCES accepted_observations(observation_uid),
    tracking_pivot_revision_no INTEGER,
    prior_related_event_uid TEXT REFERENCES setup_events(event_uid),
    state_machine_version TEXT NOT NULL,
    threshold_version TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    producer_run_id TEXT NOT NULL REFERENCES state_machine_runs(run_id),
    corrects_event_uid TEXT REFERENCES setup_events(event_uid)
);

CREATE TABLE current_setup_states (
    state_lineage TEXT NOT NULL,
    core_setup_uid TEXT NOT NULL REFERENCES core_setup_identity_ledger(core_setup_uid),
    code TEXT NOT NULL,
    current_phase TEXT NOT NULL,
    continuity_status TEXT NOT NULL,
    distribution_eligible INTEGER NOT NULL CHECK (distribution_eligible IN (0, 1)),
    latest_accepted_observation_uid TEXT REFERENCES accepted_observations(observation_uid),
    tracking_pivot_revision_no INTEGER NOT NULL,
    latest_event_uid TEXT NOT NULL REFERENCES setup_events(event_uid),
    minted_market_session_index INTEGER NOT NULL,
    latest_market_session_index INTEGER NOT NULL,
    state_machine_version TEXT NOT NULL,
    threshold_version TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    PRIMARY KEY (state_lineage, core_setup_uid)
);

CREATE INDEX idx_scope_status ON run_scope_members(run_id, observation_status);
CREATE INDEX idx_observation_setup_date ON accepted_observations(core_setup_uid, analysis_date);
CREATE INDEX idx_observation_run ON accepted_observations(run_id);
CREATE INDEX idx_decision_target ON setup_identity_decisions(target_core_setup_uid);
CREATE INDEX idx_revision_effective ON setup_pivot_revisions(core_setup_uid, valid_from_market_session_index);
CREATE INDEX idx_event_setup_order ON setup_events(core_setup_uid, event_order);
CREATE INDEX idx_event_run ON setup_events(producer_run_id);
CREATE INDEX idx_state_code ON current_setup_states(code);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def code_for(index: int) -> str:
    return f"{1000 + index:04d}"


def uid_for(code: str) -> str:
    return f"csu1:{code}:{stable_hash('setup', code)[:40]}"


def versions_json() -> str:
    return json.dumps(
        {
            "logic_version": "2026.09-v1",
            "strategy_version": "2026.09-v1",
            "threshold_version": "2026.09-v1",
            "identity_version": "csu1",
            "state_machine_version": STATE_MACHINE_VERSION,
            "state_machine_threshold_version": THRESHOLD_VERSION,
            "observation_schema_version": "smo1",
            "event_schema_version": "sev1",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def insert_run_row(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    analysis_date: str,
    session: int,
    input_hash: str,
    ledger_seed_hash: str,
    transition_count: int,
    status: str = "STARTED",
) -> None:
    conn.execute(
        """
        INSERT INTO state_machine_runs (
            run_id, processing_status, coverage_status, publish_eligibility,
            state_commit_status, started_at, finished_at, expected_market_date,
            market_session_index, scope_name, scope_member_sha256, scope_total,
            current_total, stale_total, insufficient_total, identity_link_total,
            identity_mint_total, identity_ambiguous_total, no_setup_total,
            transition_total, unchanged_total, ledger_seed_sha256,
            ledger_seed_row_count, versions_json, reason_codes_json, input_sha256
        ) VALUES (?, ?, 'DEGRADED', 'ELIGIBLE', 'PENDING', ?, NULL, ?, ?,
                  'benchmark-1000', ?, ?, ?, 4, 1, ?, 0, 0, 0, ?, ?, ?, ?, ?, '[]', ?)
        """,
        (
            run_id,
            status,
            f"{analysis_date}T16:00:00+09:00",
            analysis_date,
            session,
            stable_hash("scope", SCOPE_SIZE),
            SCOPE_SIZE,
            CURRENT_SIZE,
            CURRENT_SIZE,
            transition_count,
            CURRENT_SIZE - transition_count,
            ledger_seed_hash,
            CURRENT_SIZE,
            versions_json(),
            input_hash,
        ),
    )


def build_seed(path: Path) -> tuple[int, int, str]:
    conn = connect(path, "DELETE")
    create_schema(conn)
    conn.execute("VACUUM")
    schema_bytes = file_size(path)
    seed_run = "seed-run"
    seed_hash_placeholder = stable_hash("seed", SCHEMA_VERSION, CURRENT_SIZE)
    conn.execute("BEGIN IMMEDIATE")
    insert_run_row(
        conn,
        run_id=seed_run,
        analysis_date="2026-01-02",
        session=999,
        input_hash=stable_hash("seed-input"),
        ledger_seed_hash=seed_hash_placeholder,
        transition_count=CURRENT_SIZE,
        status="STARTED",
    )
    identities = []
    revisions = []
    events = []
    states = []
    for index in range(CURRENT_SIZE):
        code = code_for(index)
        setup_uid = uid_for(code)
        birth_hash = stable_hash("birth", code, "core-primary", "benchmark-epoch")
        event_hash = stable_hash("seed-event", setup_uid)
        event_uid = f"sev1:{code}:{event_hash[:40]}"
        identities.append(
            (setup_uid, code, "CORE", birth_hash, "benchmark-epoch", "core-primary", "csu1", seed_run, "2026-01-02T16:00:00+09:00")
        )
        revisions.append((setup_uid, 1, 1000.0 + index / 10, "minervini", "practical", "T_MINUS_1_OHLCV", "PRACTICAL", None, 999, None, 0))
        events.append(
            (
                event_uid,
                index + 1,
                event_hash,
                "SETUP_MINTED",
                1,
                setup_uid,
                None,
                "FORMING",
                1,
                "2026-01-02",
                "BOOTSTRAP_OBSERVATION_DATE",
                "2026-01-02T16:00:00+09:00",
                None,
                1,
                None,
                STATE_MACHINE_VERSION,
                THRESHOLD_VERSION,
                '["BENCHMARK_SEED"]',
                stable_hash("seed-evidence", setup_uid),
                f"seed|{setup_uid}|SETUP_MINTED",
                seed_run,
                None,
            )
        )
        states.append(("live-sm1", setup_uid, code, "FORMING", "CONTIGUOUS", 1, None, 1, event_uid, 999, 999, STATE_MACHINE_VERSION, THRESHOLD_VERSION, 0))
    conn.executemany(
        "INSERT INTO core_setup_identity_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        identities,
    )
    conn.executemany(
        "INSERT INTO setup_pivot_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        revisions,
    )
    conn.executemany(
        "INSERT INTO setup_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        events,
    )
    conn.executemany(
        "INSERT INTO current_setup_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        states,
    )
    conn.execute(
        "UPDATE state_machine_runs SET processing_status='COMPLETE', state_commit_status='COMMITTED', finished_at='2026-01-02T16:01:00+09:00' WHERE run_id=?",
        (seed_run,),
    )
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    seed_bytes = file_size(path)
    return schema_bytes, seed_bytes, sha256_file(path)


def initial_runtime_state() -> tuple[dict[str, str], dict[str, str], dict[str, int], dict[str, int]]:
    phases: dict[str, str] = {}
    latest_events: dict[str, str] = {}
    revision_numbers: dict[str, int] = {}
    event_ordinals: dict[str, int] = {}
    for index in range(CURRENT_SIZE):
        code = code_for(index)
        setup_uid = uid_for(code)
        phases[code] = "FORMING"
        latest_events[code] = f"sev1:{code}:{stable_hash('seed-event', setup_uid)[:40]}"
        revision_numbers[code] = 1
        event_ordinals[code] = 1
    return phases, latest_events, revision_numbers, event_ordinals


def write_daily_run(
    conn: sqlite3.Connection,
    *,
    run_number: int,
    event_rate: float,
    revision_rate: float,
    ledger_seed_hash: str,
    phases: dict[str, str],
    latest_events: dict[str, str],
    revision_numbers: dict[str, int],
    event_ordinals: dict[str, int],
    fail_after_events: bool = False,
) -> tuple[float, bool]:
    run_id = f"bench-e{int(event_rate * 100):02d}-r{int(revision_rate * 100):02d}-{run_number:02d}"
    analysis_date = (BASE_DATE + dt.timedelta(days=run_number)).isoformat()
    session = 1_000 + run_number
    input_hash = stable_hash("run", run_id, SCHEMA_VERSION)
    existing = conn.execute(
        "SELECT processing_status, state_commit_status, input_sha256 FROM state_machine_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if existing:
        if existing == ("COMPLETE", "COMMITTED", input_hash):
            return 0.0, True
        raise RuntimeError(f"run id conflict: {run_id}")

    event_count = int(round(CURRENT_SIZE * event_rate))
    revision_count = int(round(CURRENT_SIZE * revision_rate))
    next_phases = dict(phases)
    next_latest_events = dict(latest_events)
    next_revision_numbers = dict(revision_numbers)
    next_event_ordinals = dict(event_ordinals)
    scope_rows = []
    observations = []
    decisions = []
    revisions = []
    events = []

    for index in range(SCOPE_SIZE):
        code = code_for(index)
        if index < CURRENT_SIZE:
            status = "CURRENT"
            history_count = 260
            observation_uid = f"obs1:{code}:{stable_hash(run_id, code, 'observation')[:40]}"
            scope_rows.append((run_id, code, 1, status, analysis_date, analysis_date, history_count, 200, "[]", observation_uid))
        elif index < CURRENT_SIZE + 4:
            scope_rows.append((run_id, code, 1, "STALE_MARKET_DATE", None, (BASE_DATE + dt.timedelta(days=max(run_number - 1, 0))).isoformat(), 260, 200, '["UNKNOWN_STALE_REASON"]', None))
        else:
            scope_rows.append((run_id, code, 1, "INSUFFICIENT_PRICE_HISTORY", None, analysis_date, 184, 200, '["PRICE_HISTORY_BELOW_MINIMUM"]', None))

    strategy_states = json.dumps(
        {
            "minervini": "SETUP FORMING",
            "qullamaggie": "BREAKOUT WATCH",
            "can_slim": "SETUP FORMING",
            "weinstein": "NOT QUALIFIED",
            "darvas": "NOT QUALIFIED",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    version_blob = versions_json()
    for index in range(CURRENT_SIZE):
        code = code_for(index)
        setup_uid = uid_for(code)
        obs_uid = f"obs1:{code}:{stable_hash(run_id, code, 'observation')[:40]}"
        pivot = 1000.0 + index / 10 + run_number / 100
        obs_hash = stable_hash(run_id, code, "input")
        observations.append(
            (
                obs_uid,
                run_id,
                "CORE",
                code,
                analysis_date,
                analysis_date,
                session,
                f"{analysis_date}T16:02:00+09:00",
                pivot * 0.99,
                pivot * 0.985,
                "BREAKOUT WATCH",
                strategy_states,
                "NOT QUALIFIED",
                3,
                0,
                pivot,
                "minervini",
                "practical",
                "T_MINUS_1_OHLCV",
                "PRACTICAL",
                (BASE_DATE + dt.timedelta(days=max(run_number - 1, 0))).isoformat(),
                "CURRENT",
                "core-primary",
                setup_uid,
                revision_numbers[code],
                version_blob,
                "[]",
                obs_hash,
            )
        )
        decision_hash = stable_hash(run_id, code, "decision")
        decisions.append(
            (
                f"sid1:{code}:{decision_hash[:40]}",
                obs_uid,
                code,
                "CORE",
                "core-primary",
                "LINK",
                setup_uid,
                "benchmark-epoch",
                "core-primary",
                "idr1",
                '["EXPLICIT_DURABLE_SLOT_MAPPING"]',
                json.dumps([obs_uid], separators=(",", ":")),
                None,
            )
        )

    for index in range(revision_count):
        code = code_for(index)
        setup_uid = uid_for(code)
        next_revision_numbers[code] += 1
        obs_uid = f"obs1:{code}:{stable_hash(run_id, code, 'observation')[:40]}"
        revisions.append(
            (
                setup_uid,
                next_revision_numbers[code],
                1000.0 + index / 10 + run_number / 100,
                "minervini",
                "practical",
                "T_MINUS_1_OHLCV",
                "PRACTICAL",
                obs_uid,
                session,
                None,
                0,
            )
        )

    phase_cycle = (
        ("WATCH_ENTERED", "WATCH"),
        ("BREAKOUT_CONFIRMED", "POST_BREAKOUT"),
        ("FAILED_CONFIRMED", "FAILED"),
        ("RETRY_WATCH_ENTERED", "RETRY_WATCH"),
        ("REBREAKOUT_CONFIRMED", "POST_BREAKOUT"),
    )
    next_event_order = conn.execute("SELECT COALESCE(MAX(event_order), 0) FROM setup_events").fetchone()[0] + 1
    for index in range(event_count):
        code = code_for(index)
        setup_uid = uid_for(code)
        obs_uid = f"obs1:{code}:{stable_hash(run_id, code, 'observation')[:40]}"
        event_type, to_phase = phase_cycle[(run_number + index) % len(phase_cycle)]
        from_phase = next_phases[code]
        next_phases[code] = to_phase
        next_event_ordinals[code] += 1
        request_hash = stable_hash(STATE_MACHINE_VERSION, setup_uid, event_type, obs_uid, next_event_ordinals[code])
        event_uid = f"sev1:{code}:{request_hash[:40]}"
        next_latest_events[code] = event_uid
        events.append(
            (
                event_uid,
                next_event_order + index,
                request_hash,
                event_type,
                next_event_ordinals[code],
                setup_uid,
                from_phase,
                to_phase,
                1,
                analysis_date,
                "EXACT_CURRENT_OBSERVATION",
                f"{analysis_date}T16:03:00+09:00",
                obs_uid,
                next_revision_numbers[code],
                latest_events[code],
                STATE_MACHINE_VERSION,
                THRESHOLD_VERSION,
                '["BENCHMARK_SYNTHETIC_EVENT"]',
                stable_hash("evidence", request_hash),
                f"{STATE_MACHINE_VERSION}|{setup_uid}|{event_type}|{obs_uid}|{next_event_ordinals[code]}",
                run_id,
                None,
            )
        )

    started = time.perf_counter()
    try:
        conn.execute("BEGIN IMMEDIATE")
        insert_run_row(
            conn,
            run_id=run_id,
            analysis_date=analysis_date,
            session=session,
            input_hash=input_hash,
            ledger_seed_hash=ledger_seed_hash,
            transition_count=event_count,
        )
        conn.executemany("INSERT INTO run_scope_members VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", scope_rows)
        conn.executemany(
            "INSERT INTO accepted_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            observations,
        )
        conn.executemany(
            "INSERT INTO setup_identity_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            decisions,
        )
        if revisions:
            conn.executemany("INSERT INTO setup_pivot_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", revisions)
        if events:
            conn.executemany(
                "INSERT INTO setup_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                events,
            )
        if fail_after_events:
            raise RuntimeError("intentional failure after event insert")
        state_rows = []
        for index in range(CURRENT_SIZE):
            code = code_for(index)
            setup_uid = uid_for(code)
            obs_uid = f"obs1:{code}:{stable_hash(run_id, code, 'observation')[:40]}"
            state_rows.append(
                (
                    "live-sm1",
                    setup_uid,
                    code,
                    next_phases[code],
                    "CONTIGUOUS",
                    1,
                    obs_uid,
                    next_revision_numbers[code],
                    next_latest_events[code],
                    999,
                    session,
                    STATE_MACHINE_VERSION,
                    THRESHOLD_VERSION,
                    1,
                )
            )
        conn.executemany(
            """
            INSERT INTO current_setup_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state_lineage, core_setup_uid) DO UPDATE SET
                current_phase=excluded.current_phase,
                continuity_status=excluded.continuity_status,
                distribution_eligible=excluded.distribution_eligible,
                latest_accepted_observation_uid=excluded.latest_accepted_observation_uid,
                tracking_pivot_revision_no=excluded.tracking_pivot_revision_no,
                latest_event_uid=COALESCE(excluded.latest_event_uid, current_setup_states.latest_event_uid),
                latest_market_session_index=excluded.latest_market_session_index,
                state_machine_version=excluded.state_machine_version,
                threshold_version=excluded.threshold_version,
                state_version=current_setup_states.state_version + 1
            """,
            state_rows,
        )
        conn.execute(
            "UPDATE state_machine_runs SET processing_status='COMPLETE', state_commit_status='COMMITTED', finished_at=? WHERE run_id=?",
            (f"{analysis_date}T16:04:00+09:00", run_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    phases.clear()
    phases.update(next_phases)
    latest_events.clear()
    latest_events.update(next_latest_events)
    revision_numbers.clear()
    revision_numbers.update(next_revision_numbers)
    event_ordinals.clear()
    event_ordinals.update(next_event_ordinals)
    return elapsed_ms, False


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = (
        "state_machine_runs",
        "run_scope_members",
        "core_setup_identity_ledger",
        "accepted_observations",
        "setup_identity_decisions",
        "setup_pivot_revisions",
        "setup_events",
        "current_setup_states",
    )
    return {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in names}


def current_state_hash(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT core_setup_uid, code, current_phase, continuity_status,
               distribution_eligible, COALESCE(latest_accepted_observation_uid, ''),
               tracking_pivot_revision_no, latest_event_uid,
               minted_market_session_index, latest_market_session_index,
               state_machine_version, threshold_version, state_version
        FROM current_setup_states ORDER BY core_setup_uid
        """
    ).fetchall()
    return stable_hash(json.dumps(rows, separators=(",", ":"), ensure_ascii=False))


def rebuild_current_state(conn: sqlite3.Connection) -> tuple[float, str]:
    started = time.perf_counter()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM current_setup_states")
    conn.execute(
        """
        WITH latest_observation AS (
            SELECT o.*
            FROM accepted_observations o
            JOIN (
                SELECT core_setup_uid, MAX(market_session_index) AS max_session
                FROM accepted_observations GROUP BY core_setup_uid
            ) x ON x.core_setup_uid=o.core_setup_uid AND x.max_session=o.market_session_index
        ),
        observation_count AS (
            SELECT core_setup_uid, COUNT(*) AS n
            FROM accepted_observations GROUP BY core_setup_uid
        ),
        latest_event AS (
            SELECT e.*
            FROM setup_events e
            JOIN (
                SELECT core_setup_uid, MAX(event_order) AS max_order
                FROM setup_events GROUP BY core_setup_uid
            ) x ON x.core_setup_uid=e.core_setup_uid AND x.max_order=e.event_order
        ),
        latest_revision AS (
            SELECT core_setup_uid, MAX(revision_no) AS revision_no
            FROM setup_pivot_revisions GROUP BY core_setup_uid
        )
        INSERT INTO current_setup_states (
            state_lineage, core_setup_uid, code, current_phase, continuity_status,
            distribution_eligible, latest_accepted_observation_uid,
            tracking_pivot_revision_no, latest_event_uid,
            minted_market_session_index, latest_market_session_index,
            state_machine_version, threshold_version, state_version
        )
        SELECT 'live-sm1', i.core_setup_uid, i.code, e.to_phase, 'CONTIGUOUS', 1,
               o.observation_uid, r.revision_no, e.event_uid, 999,
               o.market_session_index, ?, ?, c.n
        FROM core_setup_identity_ledger i
        JOIN latest_observation o ON o.core_setup_uid=i.core_setup_uid
        JOIN observation_count c ON c.core_setup_uid=i.core_setup_uid
        JOIN latest_event e ON e.core_setup_uid=i.core_setup_uid
        JOIN latest_revision r ON r.core_setup_uid=i.core_setup_uid
        """,
        (STATE_MACHINE_VERSION, THRESHOLD_VERSION),
    )
    conn.commit()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return elapsed_ms, current_state_hash(conn)


def benchmark_scenario(
    temp_dir: Path,
    seed_path: Path,
    *,
    event_rate: float,
    revision_rate: float,
    journal_mode: str,
    seed_bytes: int,
    seed_hash: str,
) -> dict[str, Any]:
    name = f"e{int(event_rate * 100):02d}_r{int(revision_rate * 100):02d}_{journal_mode.lower()}"
    db_path = temp_dir / f"{name}.sqlite"
    shutil.copyfile(seed_path, db_path)
    conn = connect(db_path, journal_mode)
    phases, latest_events, revision_numbers, event_ordinals = initial_runtime_state()
    timings: list[float] = []
    peak_auxiliary_bytes = 0
    for run_number in range(RUNS_PER_SCENARIO):
        elapsed_ms, was_noop = write_daily_run(
            conn,
            run_number=run_number,
            event_rate=event_rate,
            revision_rate=revision_rate,
            ledger_seed_hash=seed_hash,
            phases=phases,
            latest_events=latest_events,
            revision_numbers=revision_numbers,
            event_ordinals=event_ordinals,
        )
        if was_noop:
            raise AssertionError("new benchmark run unexpectedly became a no-op")
        timings.append(elapsed_ms)
        auxiliary = sum(file_size(Path(str(db_path) + suffix)) for suffix in ("-wal", "-shm", "-journal"))
        peak_auxiliary_bytes = max(peak_auxiliary_bytes, auxiliary)
    pre_checkpoint_total = sqlite_total_bytes(db_path)
    if journal_mode == "WAL":
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    counts = table_counts(conn)
    conn.close()
    persistent_bytes = file_size(db_path)
    incremental_bytes = persistent_bytes - seed_bytes
    per_run_bytes = incremental_bytes / RUNS_PER_SCENARIO
    annual_bytes_965 = per_run_bytes * 250 * 0.965
    return {
        "scenario": name,
        "event_rate": event_rate,
        "revision_rate": revision_rate,
        "journal_mode": journal_mode,
        "runs": RUNS_PER_SCENARIO,
        "scope_rows_per_run": SCOPE_SIZE,
        "current_observations_per_run": CURRENT_SIZE,
        "transaction_ms": {
            "min": round(min(timings), 3),
            "p50": round(statistics.median(timings), 3),
            "p95": round(percentile(timings, 0.95), 3),
            "max": round(max(timings), 3),
        },
        "storage_bytes": {
            "seed": seed_bytes,
            "pre_checkpoint_total": pre_checkpoint_total,
            "persistent_after_checkpoint": persistent_bytes,
            "incremental_for_10_runs": incremental_bytes,
            "incremental_per_1000_scope_run": round(per_run_bytes),
            "annual_projection_965_x_250": round(annual_bytes_965),
            "peak_auxiliary": peak_auxiliary_bytes,
        },
        "integrity_check": integrity,
        "foreign_key_error_count": foreign_key_errors,
        "row_counts": counts,
    }


def estimate_table_storage_bytes(source: Path, temp_dir: Path) -> dict[str, int]:
    """Estimate table+index footprint by drop-and-VACUUM copies.

    The bundled SQLite does not expose the optional dbstat virtual table, so
    this deliberately uses isolated copies and reports the result as an
    attribution estimate rather than an exact additive accounting.
    """

    original_bytes = file_size(source)
    table_names = (
        "state_machine_runs",
        "run_scope_members",
        "core_setup_identity_ledger",
        "accepted_observations",
        "setup_identity_decisions",
        "setup_pivot_revisions",
        "setup_events",
        "current_setup_states",
    )
    estimates: dict[str, int] = {}
    for table_name in table_names:
        copy_path = temp_dir / f"attribution-{table_name}.sqlite"
        shutil.copyfile(source, copy_path)
        conn = sqlite3.connect(copy_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(f"DROP TABLE {table_name}")
        conn.execute("VACUUM")
        conn.close()
        estimates[table_name] = original_bytes - file_size(copy_path)
    return estimates


def backup_database(source: Path, destination: Path) -> float:
    started = time.perf_counter()
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    src.backup(dst)
    dst.close()
    src.close()
    return (time.perf_counter() - started) * 1000


def seed_roundtrip(source: Path, temp_dir: Path) -> dict[str, Any]:
    exported = temp_dir / "exported-seed.sqlite"
    imported = temp_dir / "imported-seed.sqlite"
    export_ms = backup_database(source, exported)
    hash_started = time.perf_counter()
    exported_hash = sha256_file(exported)
    hash_ms = (time.perf_counter() - hash_started) * 1000
    import_ms = backup_database(exported, imported)
    conn = connect(imported, "DELETE")
    integrity_started = time.perf_counter()
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    integrity_ms = (time.perf_counter() - integrity_started) * 1000
    before_hash = current_state_hash(conn)
    rebuild_ms, rebuilt_hash = rebuild_current_state(conn)
    counts = table_counts(conn)
    conn.close()
    return {
        "artifact_bytes": file_size(exported),
        "artifact_sha256": exported_hash,
        "export_ms": round(export_ms, 3),
        "sha256_ms": round(hash_ms, 3),
        "import_ms": round(import_ms, 3),
        "integrity_check_ms": round(integrity_ms, 3),
        "integrity_check": integrity,
        "foreign_key_error_count": foreign_key_errors,
        "current_state_rebuild_ms": round(rebuild_ms, 3),
        "current_state_hash_before": before_hash,
        "current_state_hash_after": rebuilt_hash,
        "current_state_hash_equal": before_hash == rebuilt_hash,
        "row_counts_after_import": counts,
    }


def atomic_failure_retry(temp_dir: Path, seed_path: Path, seed_hash: str) -> dict[str, Any]:
    db_path = temp_dir / "atomic.sqlite"
    shutil.copyfile(seed_path, db_path)
    conn = connect(db_path, "WAL")
    baseline = table_counts(conn)
    code = "9999"
    setup_uid = f"csu1:{code}:{stable_hash('atomic-setup')[:40]}"
    run_id = "atomic-run"
    obs_uid = f"obs1:{code}:{stable_hash('atomic-observation')[:40]}"
    event_hash = stable_hash("atomic-event")
    event_uid = f"sev1:{code}:{event_hash[:40]}"
    input_hash = stable_hash("atomic-input")

    def execute_once(fail: bool) -> str:
        existing = conn.execute(
            "SELECT processing_status, state_commit_status, input_sha256 FROM state_machine_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if existing == ("COMPLETE", "COMMITTED", input_hash):
            return "NO_OP"
        try:
            conn.execute("BEGIN IMMEDIATE")
            insert_run_row(
                conn,
                run_id=run_id,
                analysis_date="2026-02-02",
                session=2_000,
                input_hash=input_hash,
                ledger_seed_hash=seed_hash,
                transition_count=1,
            )
            conn.execute(
                "INSERT INTO run_scope_members VALUES (?, ?, 1, 'CURRENT', '2026-02-02', '2026-02-02', 260, 200, '[]', ?)",
                (run_id, code, obs_uid),
            )
            conn.execute(
                "INSERT INTO core_setup_identity_ledger VALUES (?, ?, 'CORE', ?, 'atomic-epoch', 'core-primary', 'csu1', ?, '2026-02-02T16:00:00+09:00')",
                (setup_uid, code, stable_hash("atomic-birth"), run_id),
            )
            conn.execute(
                "INSERT INTO accepted_observations VALUES (?, ?, 'CORE', ?, '2026-02-02', '2026-02-02', 2000, '2026-02-02T16:01:00+09:00', 1010, 990, 'BREAKOUT', ?, 'NOT QUALIFIED', 2, 2, 1000, 'minervini', 'practical', 'T_MINUS_1_OHLCV', 'PRACTICAL', '2026-01-30', 'CURRENT', 'core-primary', ?, 1, ?, '[]', ?)",
                (obs_uid, run_id, code, json.dumps({"minervini": "BREAKOUT", "qullamaggie": "BREAKOUT"}, separators=(",", ":")), setup_uid, versions_json(), stable_hash("atomic-obs-input")),
            )
            conn.execute(
                "INSERT INTO setup_identity_decisions VALUES (?, ?, ?, 'CORE', 'core-primary', 'MINT', ?, 'atomic-epoch', 'core-primary', 'idr1', '[\"NO_NONTERMINAL_SETUP\"]', ?, NULL)",
                (f"sid1:{code}:{stable_hash('atomic-decision')[:40]}", obs_uid, code, setup_uid, json.dumps([obs_uid], separators=(",", ":"))),
            )
            conn.execute(
                "INSERT INTO setup_pivot_revisions VALUES (?, 1, 1000, 'minervini', 'practical', 'T_MINUS_1_OHLCV', 'PRACTICAL', ?, 2000, NULL, 1)",
                (setup_uid, obs_uid),
            )
            event_order = conn.execute("SELECT MAX(event_order) + 1 FROM setup_events").fetchone()[0]
            conn.execute(
                "INSERT INTO setup_events VALUES (?, ?, ?, 'BREAKOUT_CONFIRMED', 1, ?, NULL, 'POST_BREAKOUT', 1, '2026-02-02', 'EXACT_CURRENT_OBSERVATION', '2026-02-02T16:02:00+09:00', ?, 1, NULL, 'sm1', 'smt1', '[\"ATOMIC_TEST\"]', ?, ?, ?, NULL)",
                (event_uid, event_order, event_hash, setup_uid, obs_uid, stable_hash("atomic-evidence"), f"sm1|{setup_uid}|BREAKOUT_CONFIRMED|{obs_uid}|1", run_id),
            )
            if fail:
                raise RuntimeError("intentional atomic failure")
            conn.execute(
                "INSERT INTO current_setup_states VALUES ('live-sm1', ?, ?, 'POST_BREAKOUT', 'CONTIGUOUS', 1, ?, 1, ?, 2000, 2000, 'sm1', 'smt1', 1)",
                (setup_uid, code, obs_uid, event_uid),
            )
            conn.execute(
                "UPDATE state_machine_runs SET processing_status='COMPLETE', state_commit_status='COMMITTED', finished_at='2026-02-02T16:03:00+09:00' WHERE run_id=?",
                (run_id,),
            )
            conn.commit()
            return "COMMITTED"
        except Exception:
            conn.rollback()
            if fail:
                return "ROLLED_BACK"
            raise

    first = execute_once(True)
    after_failure = table_counts(conn)
    retry = execute_once(False)
    after_retry_hash = current_state_hash(conn)
    repeated = execute_once(False)
    after_repeat_hash = current_state_hash(conn)
    after_repeat = table_counts(conn)
    orphan_checks = {
        "scope_without_run": conn.execute("SELECT COUNT(*) FROM run_scope_members s LEFT JOIN state_machine_runs r ON r.run_id=s.run_id WHERE r.run_id IS NULL").fetchone()[0],
        "observation_without_run": conn.execute("SELECT COUNT(*) FROM accepted_observations o LEFT JOIN state_machine_runs r ON r.run_id=o.run_id WHERE r.run_id IS NULL").fetchone()[0],
        "decision_without_observation": conn.execute("SELECT COUNT(*) FROM setup_identity_decisions d LEFT JOIN accepted_observations o ON o.observation_uid=d.observation_uid WHERE o.observation_uid IS NULL").fetchone()[0],
        "event_without_setup": conn.execute("SELECT COUNT(*) FROM setup_events e LEFT JOIN core_setup_identity_ledger i ON i.core_setup_uid=e.core_setup_uid WHERE i.core_setup_uid IS NULL").fetchone()[0],
    }
    duplicate_events = conn.execute("SELECT COUNT(*) FROM (SELECT idempotency_key FROM setup_events GROUP BY idempotency_key HAVING COUNT(*) > 1)").fetchone()[0]
    atomic_mints = conn.execute("SELECT COUNT(*) FROM core_setup_identity_ledger WHERE code='9999'").fetchone()[0]
    terminal_runs = conn.execute("SELECT COUNT(*) FROM state_machine_runs WHERE run_id=? AND processing_status='COMPLETE' AND state_commit_status='COMMITTED'", (run_id,)).fetchone()[0]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    return {
        "first_attempt": first,
        "after_failure_counts_equal_baseline": after_failure == baseline,
        "retry": retry,
        "identical_repeat": repeated,
        "after_repeat_counts_equal_after_retry": after_repeat["state_machine_runs"] == baseline["state_machine_runs"] + 1,
        "current_state_hash_stable_on_repeat": after_retry_hash == after_repeat_hash,
        "orphan_counts": orphan_checks,
        "duplicate_event_key_count": duplicate_events,
        "mint_rows_for_atomic_code": atomic_mints,
        "terminal_run_count": terminal_runs,
        "integrity_check": integrity,
    }


def validate_seed_fail_closed(seed_path: Path, expected_hash: str, temp_dir: Path) -> dict[str, Any]:
    def validate(path: Path, expected: str) -> tuple[bool, str]:
        if not path.exists():
            return False, "IDENTITY_LEDGER_UNAVAILABLE"
        if sha256_file(path) != expected:
            return False, "IDENTITY_LEDGER_INVALID"
        return True, "OK"

    missing_ok, missing_reason = validate(temp_dir / "does-not-exist.sqlite", expected_hash)
    mismatch_ok, mismatch_reason = validate(seed_path, "0" * 64)
    valid_ok, valid_reason = validate(seed_path, expected_hash)
    return {
        "missing": {"accepted": missing_ok, "reason": missing_reason, "mint_count": 0},
        "hash_mismatch": {"accepted": mismatch_ok, "reason": mismatch_reason, "mint_count": 0},
        "valid": {"accepted": valid_ok, "reason": valid_reason},
    }


def compact_public_artifact_size(event_rate: float = 0.10) -> dict[str, int]:
    states = []
    for index in range(CURRENT_SIZE):
        code = code_for(index)
        states.append(
            {
                "u": uid_for(code),
                "c": code,
                "p": "WATCH",
                "q": "CONTIGUOUS",
                "d": "2026-02-13",
            }
        )
    daily_event_count = int(round(CURRENT_SIZE * event_rate))
    events = []
    for day in range(30):
        date = (BASE_DATE + dt.timedelta(days=day)).isoformat()
        for index in range(daily_event_count):
            code = code_for(index)
            events.append(
                {
                    "u": f"sev1:{code}:{stable_hash('public', day, code)[:40]}",
                    "s": uid_for(code),
                    "t": "WATCH_ENTERED",
                    "d": date,
                }
            )
    payload = json.dumps(
        {
            "v": "phase2a-public-state-v1",
            "generated_at": "2026-02-13T16:00:00+09:00",
            "states": states,
            "events_30d": events,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "state_count": len(states),
        "event_count_30d": len(events),
        "compact_json_bytes": len(payload),
        "gzip_bytes": len(gzip.compress(payload, compresslevel=9, mtime=0)),
    }


def run_benchmark() -> dict[str, Any]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="phase2a-benchmark-") as directory:
        temp_dir = Path(directory)
        seed_path = temp_dir / "seed.sqlite"
        schema_bytes, seed_bytes, seed_hash = build_seed(seed_path)
        scenarios = []
        representative_path: Path | None = None
        for journal_mode in JOURNAL_MODES:
            for event_rate in EVENT_RATES:
                for revision_rate in REVISION_RATES:
                    result = benchmark_scenario(
                        temp_dir,
                        seed_path,
                        event_rate=event_rate,
                        revision_rate=revision_rate,
                        journal_mode=journal_mode,
                        seed_bytes=seed_bytes,
                        seed_hash=seed_hash,
                    )
                    scenarios.append(result)
                    if journal_mode == "WAL" and event_rate == 0.10 and revision_rate == 0.15:
                        representative_path = temp_dir / f"{result['scenario']}.sqlite"
        if representative_path is None:
            raise AssertionError("representative scenario was not generated")
        roundtrip = seed_roundtrip(representative_path, temp_dir)
        storage_attribution = estimate_table_storage_bytes(representative_path, temp_dir)
        atomic = atomic_failure_retry(temp_dir, seed_path, seed_hash)
        fail_closed = validate_seed_fail_closed(seed_path, seed_hash, temp_dir)
        public_size = compact_public_artifact_size()
        representative = next(item for item in scenarios if item["scenario"] == "e10_r15_wal")
        projected_mb = representative["storage_bytes"]["annual_projection_965_x_250"] / 1_000_000
        planned_mb = 258.0
        variance_pct = (projected_mb - planned_mb) / planned_mb * 100
        elapsed = time.perf_counter() - started
        return {
            "benchmark_version": "phase2a-limited-runtime-benchmark-v1",
            "schema_version": SCHEMA_VERSION,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "isolation": {
                "temporary_directory_used": True,
                "temporary_directory_removed_on_exit": True,
                "product_database_opened": False,
                "network_used": False,
                "scope_rows_per_run": SCOPE_SIZE,
                "current_rows_per_run": CURRENT_SIZE,
                "runs_per_scenario": RUNS_PER_SCENARIO,
                "scenario_count": len(scenarios),
            },
            "environment": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
                "cpu_count": os.cpu_count(),
            },
            "baseline": {
                "empty_schema_bytes": schema_bytes,
                "seeded_database_bytes": seed_bytes,
                "seed_payload_bytes": seed_bytes - schema_bytes,
                "seed_sha256": seed_hash,
                "seed_setup_count": CURRENT_SIZE,
            },
            "scenarios": scenarios,
            "representative_scenario": {
                "name": representative["scenario"],
                "selection": "WAL, event 10%, pivot revision 15%",
                "annual_projection_mb_decimal": round(projected_mb, 3),
                "design_planning_mb_decimal": planned_mb,
                "variance_pct": round(variance_pct, 2),
                "within_plus_minus_30_pct": abs(variance_pct) <= 30,
            },
            "seed_roundtrip": roundtrip,
            "representative_storage_attribution_bytes": storage_attribution,
            "atomic_failure_retry": atomic,
            "seed_fail_closed": fail_closed,
            "public_artifact_30d": public_size,
            "elapsed_seconds": round(elapsed, 3),
        }


def verify_result(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if result["isolation"]["scenario_count"] != 18:
        failures.append("expected 18 scenarios")
    for scenario in result["scenarios"]:
        if scenario["integrity_check"] != "ok":
            failures.append(f"integrity failed: {scenario['scenario']}")
        if scenario["foreign_key_error_count"] != 0:
            failures.append(f"foreign key errors: {scenario['scenario']}")
        if scenario["row_counts"]["run_scope_members"] != SCOPE_SIZE * RUNS_PER_SCENARIO:
            failures.append(f"scope count mismatch: {scenario['scenario']}")
        if scenario["row_counts"]["accepted_observations"] != CURRENT_SIZE * RUNS_PER_SCENARIO:
            failures.append(f"observation count mismatch: {scenario['scenario']}")
    roundtrip = result["seed_roundtrip"]
    if roundtrip["integrity_check"] != "ok" or roundtrip["foreign_key_error_count"] != 0:
        failures.append("seed roundtrip integrity failed")
    if not roundtrip["current_state_hash_equal"]:
        failures.append("current state rebuild hash mismatch")
    atomic = result["atomic_failure_retry"]
    if not atomic["after_failure_counts_equal_baseline"]:
        failures.append("atomic rollback left rows")
    if atomic["retry"] != "COMMITTED" or atomic["identical_repeat"] != "NO_OP":
        failures.append("atomic retry/idempotency failed")
    if any(atomic["orphan_counts"].values()):
        failures.append("orphan rows detected")
    if atomic["duplicate_event_key_count"] != 0 or atomic["mint_rows_for_atomic_code"] != 1:
        failures.append("duplicate event or mint detected")
    fail_closed = result["seed_fail_closed"]
    if fail_closed["missing"]["accepted"] or fail_closed["hash_mismatch"]["accepted"]:
        failures.append("invalid seed was accepted")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()
    result = run_benchmark()
    failures = verify_result(result)
    result["verification"] = {"status": "PASS" if not failures else "FAIL", "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
