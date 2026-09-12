"""Explicit, isolated persistence for the Phase 2A state machine."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import SCHEMA_VERSION
from .ids import canonical_json
from .models import (
    ContinuityStatus,
    IdentityCandidate,
    PivotFact,
    SetupPhase,
    SetupStateRecord,
)
from .schema import SCHEMA_SHA256, SCHEMA_SQL, SEED_SCHEMA_SQL, SEED_SCHEMA_VERSION


class Phase2AStorageError(RuntimeError):
    pass


class Phase2AInputConflict(Phase2AStorageError):
    pass


class Phase2AOptimisticConflict(Phase2AStorageError):
    pass


class Phase2ASeedError(Phase2AStorageError):
    pass


@dataclass(frozen=True)
class RunBundle:
    run: dict[str, Any]
    scope_members: tuple[dict[str, Any], ...]
    observations: tuple[dict[str, Any], ...] = ()
    ledger_rows: tuple[dict[str, Any], ...] = ()
    strategy_ledger_rows: tuple[dict[str, Any], ...] = ()
    decisions: tuple[dict[str, Any], ...] = ()
    pivot_revisions: tuple[dict[str, Any], ...] = ()
    pivot_closures: tuple[dict[str, Any], ...] = ()
    pivot_freezes: tuple[dict[str, Any], ...] = ()
    memberships: tuple[dict[str, Any], ...] = ()
    membership_closures: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    current_states: tuple[dict[str, Any], ...] = ()
    rejections: tuple[dict[str, Any], ...] = ()
    event_anchors: tuple[dict[str, Any], ...] = ()
    terminal_uids: tuple[str, ...] = ()


RUN_COLUMNS = (
    "run_id", "run_kind", "state_lineage", "processing_status", "coverage_status",
    "publish_eligibility", "state_commit_status", "started_at", "finished_at",
    "expected_market_date", "market_session_index", "scope_name", "scope_member_sha256",
    "scope_total", "current_total", "no_new_market_total", "stale_total",
    "insufficient_total", "fetch_failed_total", "analysis_failed_total", "out_of_scope_total",
    "invalid_input_total", "identity_link_total", "identity_mint_total",
    "identity_ambiguous_total", "no_setup_total", "transition_total", "unchanged_total",
    "rejected_total", "input_sha256", "seed_sha256", "seed_schema_version",
    "seed_row_count", "versions_json", "reason_codes_json", "structured_errors_json",
)


TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "phase2a_scope_members": (
        "run_id", "code", "in_scope", "observation_status", "analysis_date",
        "latest_price_date", "price_history_count", "required_price_history_count",
        "reason_codes_json", "structured_error_ref", "observation_uid",
    ),
    "phase2a_identity_ledger": (
        "core_setup_uid", "code", "namespace", "mint_request_sha256",
        "canonical_mint_request", "identity_epoch", "origin_observation_uid",
        "origin_slot", "identity_version", "created_run_id", "created_at", "terminal",
    ),
    "phase2a_strategy_identity_ledger": (
        "strategy_setup_uid", "code", "strategy_code", "mint_request_sha256",
        "canonical_mint_request", "identity_epoch", "origin_observation_uid",
        "identity_version", "created_run_id", "created_at",
    ),
    "phase2a_observations": (
        "observation_uid", "run_id", "namespace", "code", "analysis_date",
        "expected_market_date", "market_session_index", "observed_at", "close",
        "previous_accepted_close", "core_observed_state", "trend_strategy_states_json",
        "connors_state", "aligned_trend_strategy_count", "breakout_trend_strategy_count",
        "observed_pivot_price", "observed_pivot_strategy", "observed_pivot_type",
        "observed_pivot_basis", "observed_pivot_fidelity", "observed_pivot_reference_date",
        "observation_status", "decision_slot", "core_setup_uid",
        "tracking_pivot_revision_no", "source_versions_json", "legacy_refs_json",
        "reason_codes_json", "input_sha256",
    ),
    "phase2a_identity_decisions": (
        "decision_uid", "observation_uid", "code", "namespace", "decision_slot",
        "decision", "target_core_setup_uid", "identity_epoch", "origin_slot",
        "decision_rule_version", "blocker", "reason_codes_json", "evidence_refs_json",
        "supersedes_decision_uid",
    ),
    "phase2a_pivot_revisions": (
        "core_setup_uid", "revision_no", "tracking_pivot_price", "strategy",
        "pivot_type", "basis", "fidelity", "reference_date", "effective_observation_uid",
        "valid_from_market_session_index", "valid_to_market_session_index",
        "frozen_after_breakout", "evidence_sha256",
    ),
    "phase2a_strategy_memberships": (
        "core_setup_uid", "strategy_code", "strategy_setup_uid",
        "member_from_observation_uid", "member_to_observation_uid",
        "valid_from_market_session_index", "valid_to_market_session_index", "evidence_sha256",
    ),
    "phase2a_events": (
        "event_uid", "event_request_sha256", "event_type", "occurrence_ordinal",
        "core_setup_uid", "from_phase", "to_phase", "is_phase_transition",
        "effective_date", "effective_date_status", "detected_at", "observation_uid",
        "permanent_exit_evidence_ref", "tracking_pivot_revision_no",
        "prior_related_event_uid", "state_machine_version", "threshold_version",
        "transition_id", "reason_codes_json", "evidence_sha256", "idempotency_key",
        "producer_run_id", "corrects_event_uid",
    ),
    "phase2a_rejections": (
        "rejection_uid", "run_id", "observation_uid", "candidate_core_setup_uid",
        "action", "transition_id", "reason_codes_json", "route",
        "state_machine_version", "created_at",
    ),
    "phase2a_event_anchors": (
        "core_setup_uid", "anchor_type", "event_uid", "effective_date",
        "market_session_index", "occurrence_ordinal", "evidence_sha256",
    ),
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _insert(conn: sqlite3.Connection, table: str, columns: tuple[str, ...], row: dict[str, Any]) -> None:
    missing = [column for column in columns if column not in row]
    if missing:
        raise Phase2AStorageError(f"{table} missing columns: {missing}")
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        tuple(row[column] for column in columns),
    )


class Phase2AStore:
    """A store that only opens the path explicitly supplied by its caller."""

    def __init__(self, path: str | Path, *, journal_mode: str = "WAL") -> None:
        self.path = Path(path)
        self.journal_mode = journal_mode.upper()
        if self.journal_mode not in {"WAL", "DELETE"}:
            raise ValueError("journal_mode must be WAL or DELETE")

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        actual = conn.execute(f"PRAGMA journal_mode={self.journal_mode}").fetchone()[0].upper()
        if actual != self.journal_mode:
            conn.close()
            raise Phase2AStorageError(f"journal mode mismatch: {actual}")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def migrate(self, *, applied_at: str | None = None) -> None:
        applied_at = applied_at or _utc_now()
        with self.connect() as conn:
            migration_table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='phase2a_schema_migrations'"
            ).fetchone()
            if migration_table_exists:
                existing = conn.execute(
                    "SELECT schema_sha256 FROM phase2a_schema_migrations "
                    "WHERE schema_version=?",
                    (SCHEMA_VERSION,),
                ).fetchone()
                if existing is not None and existing[0] != SCHEMA_SHA256:
                    raise Phase2AStorageError("schema version exists with a different hash")
            conn.executescript(SCHEMA_SQL)
            existing = conn.execute(
                "SELECT schema_sha256 FROM phase2a_schema_migrations WHERE schema_version=?",
                (SCHEMA_VERSION,),
            ).fetchone()
            if existing is not None and existing[0] != SCHEMA_SHA256:
                raise Phase2AStorageError("schema version exists with a different hash")
            conn.execute(
                "INSERT OR IGNORE INTO phase2a_schema_migrations VALUES (?,?,?)",
                (SCHEMA_VERSION, SCHEMA_SHA256, applied_at),
            )

    def schema_verified(self) -> bool:
        if not self.path.exists():
            return False
        with self.connect() as conn:
            try:
                row = conn.execute(
                    "SELECT schema_sha256 FROM phase2a_schema_migrations WHERE schema_version=?",
                    (SCHEMA_VERSION,),
                ).fetchone()
            except sqlite3.OperationalError:
                return False
            return row is not None and row[0] == SCHEMA_SHA256

    def integrity(self) -> dict[str, Any]:
        with self.connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = [dict(row) for row in conn.execute("PRAGMA foreign_key_check")]
        return {"integrity_check": integrity, "foreign_key_errors": foreign_keys}

    def commit_run(self, bundle: RunBundle, *, fail_after_stage: str | None = None) -> str:
        self._validate_bundle(bundle)
        run_id = bundle.run["run_id"]
        with self.connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT input_sha256,state_commit_status FROM phase2a_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if existing is not None:
                    if existing[0] == bundle.run["input_sha256"] and existing[1] == "COMMITTED":
                        conn.rollback()
                        return "NO_OP"
                    raise Phase2AInputConflict("run id already exists with different or incomplete input")
                same_day = conn.execute(
                    "SELECT run_id,input_sha256,state_commit_status FROM phase2a_runs "
                    "WHERE state_lineage=? AND expected_market_date=?",
                    (bundle.run["state_lineage"], bundle.run["expected_market_date"]),
                ).fetchone()
                if same_day is not None:
                    if same_day[1] == bundle.run["input_sha256"] and same_day[2] == "COMMITTED":
                        conn.rollback()
                        return "NO_OP"
                    raise Phase2AInputConflict("same-day input conflict")

                staging = dict(bundle.run)
                staging["processing_status"] = "STARTED"
                staging["state_commit_status"] = "STAGING"
                staging["finished_at"] = None
                _insert(conn, "phase2a_runs", RUN_COLUMNS, staging)
                self._maybe_fail("RUN_INSERT", fail_after_stage)

                for row in bundle.scope_members:
                    _insert(conn, "phase2a_scope_members", TABLE_COLUMNS["phase2a_scope_members"], row)
                self._maybe_fail("SCOPE_INSERT", fail_after_stage)

                # Observations are initially nullable-linkable. MINT ledger rows are inserted next.
                for row in bundle.observations:
                    initial = dict(row)
                    if initial.get("core_setup_uid") and any(
                        item["core_setup_uid"] == initial["core_setup_uid"] for item in bundle.ledger_rows
                    ):
                        initial["core_setup_uid"] = None
                    _insert(conn, "phase2a_observations", TABLE_COLUMNS["phase2a_observations"], initial)
                self._maybe_fail("OBSERVATION_INSERT", fail_after_stage)

                for row in bundle.ledger_rows:
                    self._insert_ledger_idempotent(conn, row)
                for row in bundle.strategy_ledger_rows:
                    self._insert_strategy_ledger_idempotent(conn, row)
                for row in bundle.observations:
                    if row.get("core_setup_uid"):
                        conn.execute(
                            "UPDATE phase2a_observations SET core_setup_uid=? WHERE observation_uid=?",
                            (row["core_setup_uid"], row["observation_uid"]),
                        )
                for row in bundle.decisions:
                    _insert(conn, "phase2a_identity_decisions", TABLE_COLUMNS["phase2a_identity_decisions"], row)
                self._maybe_fail("IDENTITY_INSERT", fail_after_stage)

                for row in bundle.pivot_revisions:
                    _insert(conn, "phase2a_pivot_revisions", TABLE_COLUMNS["phase2a_pivot_revisions"], row)
                for row in bundle.pivot_closures:
                    cursor = conn.execute(
                        "UPDATE phase2a_pivot_revisions SET valid_to_market_session_index=? "
                        "WHERE core_setup_uid=? AND revision_no=? AND valid_to_market_session_index IS NULL",
                        (
                            row["valid_to_market_session_index"], row["core_setup_uid"],
                            row["revision_no"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise Phase2AStorageError("active pivot revision closure failed")
                for row in bundle.pivot_freezes:
                    cursor = conn.execute(
                        "UPDATE phase2a_pivot_revisions SET frozen_after_breakout=1 "
                        "WHERE core_setup_uid=? AND revision_no=?",
                        (row["core_setup_uid"], row["revision_no"]),
                    )
                    if cursor.rowcount != 1:
                        raise Phase2AStorageError("tracking pivot freeze failed")
                for row in bundle.memberships:
                    _insert(conn, "phase2a_strategy_memberships", TABLE_COLUMNS["phase2a_strategy_memberships"], row)
                for row in bundle.membership_closures:
                    cursor = conn.execute(
                        "UPDATE phase2a_strategy_memberships SET member_to_observation_uid=?,"
                        "valid_to_market_session_index=? WHERE core_setup_uid=? AND strategy_code=? "
                        "AND strategy_setup_uid=? AND member_from_observation_uid=? "
                        "AND valid_to_market_session_index IS NULL",
                        (
                            row["member_to_observation_uid"], row["valid_to_market_session_index"],
                            row["core_setup_uid"], row["strategy_code"], row["strategy_setup_uid"],
                            row["member_from_observation_uid"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise Phase2AStorageError("active strategy membership closure failed")
                self._maybe_fail("PIVOT_MEMBERSHIP_INSERT", fail_after_stage)

                for row in bundle.events:
                    self._insert_event_idempotent(conn, row)
                self._maybe_fail("EVENT_INSERT", fail_after_stage)

                for row in bundle.current_states:
                    self._upsert_state(conn, row)
                for core_setup_uid in bundle.terminal_uids:
                    cursor = conn.execute(
                        "UPDATE phase2a_identity_ledger SET terminal=1 WHERE core_setup_uid=?",
                        (core_setup_uid,),
                    )
                    if cursor.rowcount != 1:
                        raise Phase2AStorageError("terminal identity update failed")
                for row in bundle.event_anchors:
                    self._upsert_anchor(conn, row)
                self._maybe_fail("STATE_UPDATE", fail_after_stage)

                for row in bundle.rejections:
                    _insert(conn, "phase2a_rejections", TABLE_COLUMNS["phase2a_rejections"], row)
                self._assert_run_invariants(conn, bundle)
                self._maybe_fail("INVARIANT_CHECK", fail_after_stage)

                conn.execute(
                    "UPDATE phase2a_runs SET processing_status=?,coverage_status=?,"
                    "publish_eligibility=?,state_commit_status='COMMITTED',finished_at=? WHERE run_id=?",
                    (
                        bundle.run["processing_status"], bundle.run["coverage_status"],
                        bundle.run["publish_eligibility"], bundle.run["finished_at"], run_id,
                    ),
                )
                conn.commit()
                return "COMMITTED"
            except Exception:
                conn.rollback()
                raise

    def record_failed_run(
        self, run: dict[str, Any], *, stage: str | None, reason_codes: Iterable[str],
        error_digest: str,
    ) -> None:
        failed_at = run.get("finished_at") or _utc_now()
        failure_id = hashlib.sha256(
            f"{run['run_id']}\x1f{failed_at}\x1f{error_digest}".encode("utf-8")
        ).hexdigest()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO phase2a_failed_run_manifests VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    failure_id, run["run_id"], run["state_lineage"],
                    run["expected_market_date"], run["input_sha256"], failed_at,
                    stage, canonical_json(list(reason_codes)), error_digest,
                ),
            )

    def _insert_ledger_idempotent(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        existing = conn.execute(
            "SELECT core_setup_uid,mint_request_sha256,canonical_mint_request "
            "FROM phase2a_identity_ledger WHERE core_setup_uid=? OR mint_request_sha256=?",
            (row["core_setup_uid"], row["mint_request_sha256"]),
        ).fetchone()
        if existing is None:
            _insert(conn, "phase2a_identity_ledger", TABLE_COLUMNS["phase2a_identity_ledger"], row)
            return
        if (
            existing[0] != row["core_setup_uid"]
            or existing[1] != row["mint_request_sha256"]
            or existing[2] != row["canonical_mint_request"]
        ):
            raise Phase2AStorageError("identity truncation/hash collision")

    def _insert_event_idempotent(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        existing = conn.execute(
            "SELECT event_uid,event_request_sha256,idempotency_key FROM phase2a_events "
            "WHERE event_uid=? OR event_request_sha256=? OR idempotency_key=?",
            (row["event_uid"], row["event_request_sha256"], row["idempotency_key"]),
        ).fetchone()
        if existing is None:
            _insert(conn, "phase2a_events", TABLE_COLUMNS["phase2a_events"], row)
            return
        if tuple(existing) != (
            row["event_uid"], row["event_request_sha256"], row["idempotency_key"]
        ):
            raise Phase2AStorageError("event truncation/hash/idempotency collision")

    def _insert_strategy_ledger_idempotent(
        self, conn: sqlite3.Connection, row: dict[str, Any]
    ) -> None:
        existing = conn.execute(
            "SELECT strategy_setup_uid,mint_request_sha256,canonical_mint_request "
            "FROM phase2a_strategy_identity_ledger WHERE strategy_setup_uid=? OR mint_request_sha256=?",
            (row["strategy_setup_uid"], row["mint_request_sha256"]),
        ).fetchone()
        if existing is None:
            _insert(
                conn, "phase2a_strategy_identity_ledger",
                TABLE_COLUMNS["phase2a_strategy_identity_ledger"], row,
            )
            return
        if tuple(existing) != (
            row["strategy_setup_uid"], row["mint_request_sha256"], row["canonical_mint_request"]
        ):
            raise Phase2AStorageError("strategy identity truncation/hash collision")

    def _upsert_state(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        row = dict(row)
        expected = int(row.pop("expected_state_version")) if "expected_state_version" in row else 0
        existing = conn.execute(
            "SELECT state_version FROM phase2a_current_states WHERE state_lineage=? AND core_setup_uid=?",
            (row["state_lineage"], row["core_setup_uid"]),
        ).fetchone()
        columns = tuple(row.keys())
        if existing is None:
            if expected != 0 or row["state_version"] != 1:
                raise Phase2AOptimisticConflict("new state must start at version 1")
            _insert(conn, "phase2a_current_states", columns, row)
            return
        if existing[0] != expected or row["state_version"] != expected + 1:
            raise Phase2AOptimisticConflict("state version conflict")
        assignments = ",".join(f"{column}=?" for column in columns if column not in {"state_lineage", "core_setup_uid"})
        update_values = [row[column] for column in columns if column not in {"state_lineage", "core_setup_uid"}]
        cursor = conn.execute(
            f"UPDATE phase2a_current_states SET {assignments} "
            "WHERE state_lineage=? AND core_setup_uid=? AND state_version=?",
            (*update_values, row["state_lineage"], row["core_setup_uid"], expected),
        )
        if cursor.rowcount != 1:
            raise Phase2AOptimisticConflict("state changed concurrently")

    def _upsert_anchor(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
        columns = TABLE_COLUMNS["phase2a_event_anchors"]
        placeholders = ",".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO phase2a_event_anchors ({','.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT(core_setup_uid,anchor_type) DO UPDATE SET "
            "event_uid=excluded.event_uid,effective_date=excluded.effective_date,"
            "market_session_index=excluded.market_session_index,"
            "occurrence_ordinal=excluded.occurrence_ordinal,evidence_sha256=excluded.evidence_sha256",
            tuple(row[column] for column in columns),
        )

    def _validate_bundle(self, bundle: RunBundle) -> None:
        run = bundle.run
        missing = [column for column in RUN_COLUMNS if column not in run]
        if missing:
            raise Phase2AStorageError(f"run missing columns: {missing}")
        in_scope = sum(int(row["in_scope"]) for row in bundle.scope_members)
        if run["scope_total"] != in_scope:
            raise Phase2AStorageError("scope_total does not match in-scope rows")
        status_total = sum(
            run[name]
            for name in (
                "current_total", "no_new_market_total", "stale_total", "insufficient_total",
                "fetch_failed_total", "analysis_failed_total", "invalid_input_total",
            )
        )
        if status_total != run["scope_total"]:
            raise Phase2AStorageError("terminal in-scope observation status counts do not match scope")

    def _assert_run_invariants(self, conn: sqlite3.Connection, bundle: RunBundle) -> None:
        run_id = bundle.run["run_id"]
        scope = conn.execute(
            "SELECT count(*) FROM phase2a_scope_members WHERE run_id=? AND in_scope=1", (run_id,)
        ).fetchone()[0]
        if scope != bundle.run["scope_total"]:
            raise Phase2AStorageError("committed scope count invariant failed")
        decision_count = conn.execute(
            "SELECT count(*) FROM phase2a_identity_decisions WHERE observation_uid IN "
            "(SELECT observation_uid FROM phase2a_observations WHERE run_id=?)", (run_id,)
        ).fetchone()[0]
        expected_decisions = sum(
            bundle.run[name]
            for name in ("identity_link_total", "identity_mint_total", "identity_ambiguous_total", "no_setup_total")
        )
        if decision_count != expected_decisions:
            raise Phase2AStorageError("identity decision count invariant failed")
        event_transitions = conn.execute(
            "SELECT count(*) FROM phase2a_events WHERE producer_run_id=? AND is_phase_transition=1",
            (run_id,),
        ).fetchone()[0]
        if event_transitions != bundle.run["transition_total"]:
            raise Phase2AStorageError("transition count invariant failed")

    @staticmethod
    def _maybe_fail(stage: str, requested: str | None) -> None:
        if requested == stage:
            raise Phase2AStorageError(f"injected failure after {stage}")

    def load_identity_candidates(self, code: str, *, lineage: str = "live-sm1") -> list[IdentityCandidate]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT s.core_setup_uid,s.code,s.current_phase,s.latest_accepted_market_session_index "
                "FROM phase2a_current_states s JOIN phase2a_identity_ledger i USING(core_setup_uid) "
                "WHERE s.state_lineage=? AND s.code=? AND i.terminal=0 "
                "AND s.current_phase NOT IN ('EXPIRED','CLOSED') ORDER BY s.core_setup_uid",
                (lineage, code),
            ).fetchall()
        return [
            IdentityCandidate(row[0], row[1], SetupPhase(row[2]), row[3]) for row in rows
        ]

    def load_state(self, core_setup_uid: str, *, lineage: str = "live-sm1") -> SetupStateRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT s.*,p.tracking_pivot_price,p.strategy,p.pivot_type,p.basis,p.fidelity,p.reference_date "
                "FROM phase2a_current_states s JOIN phase2a_pivot_revisions p "
                "ON p.core_setup_uid=s.core_setup_uid AND p.revision_no=s.tracking_pivot_revision_no "
                "WHERE s.state_lineage=? AND s.core_setup_uid=?",
                (lineage, core_setup_uid),
            ).fetchone()
        if row is None:
            return None
        return SetupStateRecord(
            core_setup_uid=row["core_setup_uid"], code=row["code"],
            phase=SetupPhase(row["current_phase"]),
            tracking_pivot=PivotFact(
                row["tracking_pivot_price"], row["strategy"], row["pivot_type"],
                row["basis"], row["fidelity"], row["reference_date"],
            ),
            tracking_pivot_revision_no=row["tracking_pivot_revision_no"],
            minted_market_session_index=row["minted_market_session_index"],
            latest_accepted_market_session_index=row["latest_accepted_market_session_index"],
            latest_accepted_date=row["latest_accepted_date"],
            latest_input_sha256=row["latest_input_sha256"],
            latest_accepted_close=row["latest_accepted_close"],
            latest_accepted_observation_uid=row["latest_accepted_observation_uid"],
            latest_breakout_market_session_index=row["latest_breakout_market_session_index"],
            breakout_count=row["breakout_count"], failure_cycle_no=row["failure_cycle_no"],
            continuity_status=ContinuityStatus(row["continuity_status"]),
            distribution_eligible=bool(row["distribution_eligible"]),
            state_machine_version=row["state_machine_version"],
            threshold_version=row["threshold_version"], state_version=row["state_version"],
            latest_event_uid=row["latest_event_uid"],
            latest_breakout_event_uid=row["latest_breakout_event_uid"],
            latest_failure_event_uid=row["latest_failure_event_uid"],
            phase_entered_observation_uid=row["phase_entered_observation_uid"],
            phase_entered_effective_date=row["phase_entered_effective_date"],
            closure_reason_code=row["closure_reason_code"],
            closure_evidence_ref=row["closure_evidence_ref"],
        )

    def table_counts(self) -> dict[str, int]:
        tables = (
            "phase2a_runs", "phase2a_scope_members", "phase2a_identity_ledger",
            "phase2a_strategy_identity_ledger",
            "phase2a_observations", "phase2a_identity_decisions", "phase2a_pivot_revisions",
            "phase2a_strategy_memberships", "phase2a_events", "phase2a_current_states",
            "phase2a_rejections", "phase2a_event_anchors",
        )
        with self.connect() as conn:
            return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}

    def has_committed_run(self, run_id: str, input_sha256: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM phase2a_runs WHERE run_id=? AND input_sha256=? "
                "AND state_commit_status='COMMITTED'",
                (run_id, input_sha256),
            ).fetchone()
        return row is not None

    def load_active_memberships(self, core_setup_uid: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM phase2a_strategy_memberships WHERE core_setup_uid=? "
                "AND valid_to_market_session_index IS NULL ORDER BY strategy_code,strategy_setup_uid",
                (core_setup_uid,),
            )]

    def create_replay_lineage(
        self, *, source_lineage: str, replay_lineage: str, requested_at: str,
        reason: str, approved_manifest_sha256: str | None = None,
    ) -> None:
        if replay_lineage == source_lineage or not replay_lineage.startswith("replay-"):
            raise Phase2AStorageError("replay lineage must be separate and start with replay-")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO phase2a_replay_lineages VALUES (?,?,?,?,?,0)",
                (
                    replay_lineage, source_lineage, requested_at, reason,
                    approved_manifest_sha256,
                ),
            )

    def export_seed(self, output_dir: str | Path, *, lineage: str = "live-sm1") -> dict[str, Any]:
        if not self.schema_verified():
            raise Phase2ASeedError("operational schema is not verified")
        output = Path(output_dir)
        if output.exists():
            raise Phase2ASeedError(f"seed output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix=".phase2a-seed-", dir=output.parent))
        try:
            seed_path = temp_root / "seed.sqlite"
            seed = sqlite3.connect(seed_path)
            seed.row_factory = sqlite3.Row
            seed.executescript(SEED_SCHEMA_SQL)
            row_counts: dict[str, int] = {}
            with self.connect() as source:
                identities = source.execute(
                    "SELECT * FROM phase2a_identity_ledger ORDER BY core_setup_uid"
                ).fetchall()
                for row in identities:
                    seed.execute(
                        "INSERT INTO seed_identity_ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        tuple(row),
                    )
                row_counts["identity_ledger"] = len(identities)

                strategy_identities = source.execute(
                    "SELECT * FROM phase2a_strategy_identity_ledger ORDER BY strategy_setup_uid"
                ).fetchall()
                for row in strategy_identities:
                    seed.execute(
                        "INSERT INTO seed_strategy_identity_ledger VALUES (?,?,?,?,?,?,?,?,?,?)",
                        tuple(row),
                    )
                row_counts["strategy_identity_ledger"] = len(strategy_identities)

                states = source.execute(
                    "SELECT * FROM phase2a_current_states WHERE state_lineage=? ORDER BY core_setup_uid",
                    (lineage,),
                ).fetchall()
                for row in states:
                    data = dict(row)
                    seed.execute(
                        "INSERT INTO seed_current_states VALUES (?,?,?)",
                        (lineage, data["core_setup_uid"], canonical_json(data)),
                    )
                row_counts["current_states"] = len(states)
                active_uids = [row["core_setup_uid"] for row in states]

                pivots: list[sqlite3.Row] = []
                memberships: list[sqlite3.Row] = []
                anchors: list[sqlite3.Row] = []
                if active_uids:
                    placeholders = ",".join("?" for _ in active_uids)
                    pivots = source.execute(
                        f"SELECT * FROM phase2a_pivot_revisions WHERE core_setup_uid IN ({placeholders}) "
                        "ORDER BY core_setup_uid,revision_no", active_uids,
                    ).fetchall()
                    memberships = source.execute(
                        f"SELECT * FROM phase2a_strategy_memberships WHERE core_setup_uid IN ({placeholders}) "
                        "AND valid_to_market_session_index IS NULL ORDER BY core_setup_uid,strategy_code",
                        active_uids,
                    ).fetchall()
                    anchors = source.execute(
                        f"SELECT * FROM phase2a_event_anchors WHERE core_setup_uid IN ({placeholders}) "
                        "ORDER BY core_setup_uid,anchor_type", active_uids,
                    ).fetchall()
                for row in pivots:
                    data = dict(row)
                    seed.execute(
                        "INSERT INTO seed_pivot_revisions VALUES (?,?,?)",
                        (data["core_setup_uid"], data["revision_no"], canonical_json(data)),
                    )
                for row in memberships:
                    data = dict(row)
                    seed.execute(
                        "INSERT INTO seed_strategy_memberships VALUES (?,?,?,?,?)",
                        (
                            data["core_setup_uid"], data["strategy_code"],
                            data["strategy_setup_uid"], data["member_from_observation_uid"],
                            canonical_json(data),
                        ),
                    )
                for row in anchors:
                    data = dict(row)
                    seed.execute(
                        "INSERT INTO seed_event_anchors VALUES (?,?,?)",
                        (data["core_setup_uid"], data["anchor_type"], canonical_json(data)),
                    )
                row_counts["pivot_revisions"] = len(pivots)
                row_counts["strategy_memberships"] = len(memberships)
                row_counts["event_anchors"] = len(anchors)
                producer = source.execute(
                    "SELECT run_id FROM phase2a_runs WHERE state_lineage=? AND state_commit_status='COMMITTED' "
                    "ORDER BY expected_market_date DESC,run_id DESC LIMIT 1", (lineage,),
                ).fetchone()
                producer_run_id = producer[0] if producer else None

            metadata = {
                "seed_schema_version": SEED_SCHEMA_VERSION,
                "operational_schema_version": SCHEMA_VERSION,
                "operational_schema_sha256": SCHEMA_SHA256,
                "state_lineage": lineage,
                "producer_run_id": producer_run_id,
            }
            seed.executemany(
                "INSERT INTO seed_metadata VALUES (?,?)",
                [(key, canonical_json(value)) for key, value in sorted(metadata.items())],
            )
            seed.commit()
            seed.execute("VACUUM")
            seed.close()

            seed_sha = _sha256_file(seed_path)
            manifest = {
                **metadata,
                "sqlite_sha256": seed_sha,
                "row_counts": row_counts,
            }
            (temp_root / "seed-manifest.json").write_text(
                canonical_json(manifest) + "\n", encoding="utf-8"
            )
            (temp_root / "seed.sha256").write_text(seed_sha + "  seed.sqlite\n", encoding="utf-8")
            self.verify_seed(temp_root)
            os.replace(temp_root, output)
            return manifest
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise

    @staticmethod
    def verify_seed(seed_dir: str | Path) -> dict[str, Any]:
        root = Path(seed_dir)
        required = (root / "seed.sqlite", root / "seed-manifest.json", root / "seed.sha256")
        if not all(path.is_file() for path in required):
            raise Phase2ASeedError("seed bundle is incomplete")
        try:
            manifest = json.loads(required[1].read_text(encoding="utf-8"))
            sidecar = required[2].read_text(encoding="utf-8").split()[0]
        except (OSError, ValueError, IndexError, json.JSONDecodeError) as exc:
            raise Phase2ASeedError("seed metadata is invalid") from exc
        actual = _sha256_file(required[0])
        if actual != sidecar or actual != manifest.get("sqlite_sha256"):
            raise Phase2ASeedError("seed hash mismatch")
        if manifest.get("seed_schema_version") != SEED_SCHEMA_VERSION:
            raise Phase2ASeedError("seed schema version mismatch")
        if manifest.get("operational_schema_sha256") != SCHEMA_SHA256:
            raise Phase2ASeedError("operational schema hash mismatch")
        conn = sqlite3.connect(required[0])
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise Phase2ASeedError("seed SQLite integrity check failed")
            counts = {
                "identity_ledger": conn.execute("SELECT count(*) FROM seed_identity_ledger").fetchone()[0],
                "strategy_identity_ledger": conn.execute(
                    "SELECT count(*) FROM seed_strategy_identity_ledger"
                ).fetchone()[0],
                "current_states": conn.execute("SELECT count(*) FROM seed_current_states").fetchone()[0],
                "pivot_revisions": conn.execute("SELECT count(*) FROM seed_pivot_revisions").fetchone()[0],
                "strategy_memberships": conn.execute("SELECT count(*) FROM seed_strategy_memberships").fetchone()[0],
                "event_anchors": conn.execute("SELECT count(*) FROM seed_event_anchors").fetchone()[0],
            }
        finally:
            conn.close()
        if counts != manifest.get("row_counts"):
            raise Phase2ASeedError("seed row count mismatch")
        return manifest

    @classmethod
    def restore_seed(
        cls, seed_dir: str | Path, target_path: str | Path, *, journal_mode: str = "WAL"
    ) -> "Phase2AStore":
        manifest = cls.verify_seed(seed_dir)
        target = Path(target_path)
        if target.exists() and target.stat().st_size:
            raise Phase2ASeedError("restore target must not already contain a database")
        store = cls(target, journal_mode=journal_mode)
        store.migrate()
        seed = sqlite3.connect(Path(seed_dir) / "seed.sqlite")
        seed.row_factory = sqlite3.Row
        try:
            with store.connect() as conn:
                if conn.execute("SELECT count(*) FROM phase2a_identity_ledger").fetchone()[0]:
                    raise Phase2ASeedError("restore target ledger is not empty")
                for row in seed.execute("SELECT * FROM seed_identity_ledger ORDER BY core_setup_uid"):
                    _insert(conn, "phase2a_identity_ledger", TABLE_COLUMNS["phase2a_identity_ledger"], dict(row))
                for row in seed.execute(
                    "SELECT * FROM seed_strategy_identity_ledger ORDER BY strategy_setup_uid"
                ):
                    _insert(
                        conn, "phase2a_strategy_identity_ledger",
                        TABLE_COLUMNS["phase2a_strategy_identity_ledger"], dict(row),
                    )
                for row in seed.execute("SELECT state_json FROM seed_current_states ORDER BY core_setup_uid"):
                    data = json.loads(row[0])
                    _insert(conn, "phase2a_current_states", tuple(data.keys()), data)
                for row in seed.execute("SELECT pivot_json FROM seed_pivot_revisions ORDER BY core_setup_uid,revision_no"):
                    data = json.loads(row[0])
                    _insert(conn, "phase2a_pivot_revisions", TABLE_COLUMNS["phase2a_pivot_revisions"], data)
                for row in seed.execute("SELECT membership_json FROM seed_strategy_memberships"):
                    data = json.loads(row[0])
                    _insert(conn, "phase2a_strategy_memberships", TABLE_COLUMNS["phase2a_strategy_memberships"], data)
                for row in seed.execute("SELECT anchor_json FROM seed_event_anchors"):
                    data = json.loads(row[0])
                    _insert(conn, "phase2a_event_anchors", TABLE_COLUMNS["phase2a_event_anchors"], data)
                conn.execute(
                    "INSERT INTO phase2a_seed_manifests VALUES (?,?,?,?,?,?,?)",
                    (
                        manifest["sqlite_sha256"], manifest["seed_schema_version"],
                        manifest.get("producer_run_id"), manifest["state_lineage"],
                        _utc_now(), canonical_json(manifest["row_counts"]), _utc_now(),
                    ),
                )
        finally:
            seed.close()
        return store

    def state_hash(self, *, lineage: str = "live-sm1") -> str:
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM phase2a_current_states WHERE state_lineage=? ORDER BY core_setup_uid",
                (lineage,),
            )]
        return hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
