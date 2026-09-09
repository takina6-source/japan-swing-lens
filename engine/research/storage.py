from __future__ import annotations

import json
from typing import Any

from ..database import Database


class ResearchStore:
    """Research-only persistence; immutable assets never update Core tables."""

    def __init__(self, db: Database):
        self.db = db
        self._init()

    def _init(self) -> None:
        with self.db.connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS research_hypotheses (
              hypothesis_version TEXT PRIMARY KEY, hypothesis_id TEXT,
              definition_hash TEXT NOT NULL, freeze_status TEXT NOT NULL,
              holdout_start TEXT NOT NULL, frozen_at TEXT NOT NULL,
              payload_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS research_families (
              family_id TEXT, family_version TEXT, definition_hash TEXT NOT NULL,
              family_holdout_start TEXT NOT NULL, frozen_at TEXT NOT NULL,
              payload_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(family_id,family_version));
            CREATE TABLE IF NOT EXISTS research_events (
              research_event_id TEXT PRIMARY KEY, code TEXT, setup_id TEXT,
              source_signal_id TEXT, event_type TEXT, event_date TEXT,
              aligned_count INTEGER, breakout_count INTEGER, pivot_price REAL,
              close REAL, open REAL, high REAL, low REAL, stop REAL,
              initial_risk REAL, event_state TEXT, price_basis TEXT,
              strategy_version TEXT, threshold_version TEXT, research_version TEXT,
              data_origin TEXT, evaluation_phase TEXT, payload_json TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS validation_subjects (
              validation_subject_id TEXT PRIMARY KEY, subject_type TEXT,
              code TEXT, stock_name TEXT, anchor_date TEXT, anchor_price REAL,
              price_basis TEXT, benchmark_anchor_price REAL,
              momentum_percentile REAL, trading_value REAL, trading_value_20d REAL,
              liquidity_level TEXT, market TEXT, size_class TEXT,
              strategy_version TEXT, threshold_version TEXT, schema_version TEXT,
              research_version TEXT, source_event_id TEXT, data_origin TEXT,
              evaluation_phase TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS research_control_members (
              control_group_id TEXT, validation_subject_id TEXT,
              control_code TEXT, control_name TEXT, control_type TEXT,
              control_rank INTEGER, match_score REAL, anchor_date TEXT,
              anchor_price REAL, price_basis TEXT, selection_version TEXT,
              feature_snapshot_json TEXT, feature_hash TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(control_group_id,control_code));
            CREATE TABLE IF NOT EXISTS research_control_history (
              control_group_id TEXT, validation_subject_id TEXT, control_code TEXT,
              date TEXT, session_offset INTEGER, close REAL, return_abs REAL,
              benchmark_relative_return REAL, mfe REAL, mae REAL,
              matured INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(control_group_id,control_code,date));
            CREATE TABLE IF NOT EXISTS research_subject_history (
              validation_subject_id TEXT, date TEXT, session_offset INTEGER,
              close REAL, return_abs REAL, benchmark_relative_return REAL,
              mfe REAL, mae REAL, failed_breakout INTEGER, hit_1r INTEGER,
              hit_2r INTEGER, hit_stop INTEGER, path_ambiguous INTEGER,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(validation_subject_id,date));
            CREATE TABLE IF NOT EXISTS research_checkpoint_evaluations (
              hypothesis_version TEXT, checkpoint_name TEXT,
              reached_at TEXT, evaluated_at TEXT, effective_n INTEGER,
              unique_stocks INTEGER, unique_dates INTEGER, result TEXT,
              payload_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(hypothesis_version,checkpoint_name));
            CREATE TABLE IF NOT EXISTS research_results (
              hypothesis_version TEXT PRIMARY KEY, test_execution_status TEXT,
              raw_primary_p REAL, effect_estimate REAL, ci_lower REAL, ci_upper REAL,
              direction_gate TEXT, effect_size_gate TEXT, ci_gate TEXT,
              opposite_effect_size_gate TEXT, opposite_ci_gate TEXT,
              multiplicity_status TEXT, holm_adjusted_p REAL,
              multiplicity_gate TEXT, final_decision TEXT,
              payload_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS research_family_results (
              family_id TEXT, family_version TEXT, family_status TEXT,
              closed_at TEXT, payload_json TEXT NOT NULL,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(family_id,family_version));
            CREATE TABLE IF NOT EXISTS research_telemetry (
              run_id TEXT PRIMARY KEY, run_date TEXT, payload_json TEXT NOT NULL,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS idx_research_events_code_date
              ON research_events(code,event_date);
            CREATE INDEX IF NOT EXISTS idx_research_subjects_phase
              ON validation_subjects(data_origin,evaluation_phase,anchor_date);
            CREATE INDEX IF NOT EXISTS idx_research_controls_subject
              ON research_control_members(validation_subject_id,selection_version);
            CREATE INDEX IF NOT EXISTS idx_research_history_subject_offset
              ON research_subject_history(validation_subject_id,session_offset);
            """)

    def register_hypotheses(self, rows: list[dict]) -> None:
        with self.db.connect() as con:
            for row in rows:
                existing = con.execute(
                    "SELECT definition_hash FROM research_hypotheses WHERE hypothesis_version=?",
                    (row["hypothesis_version"],)).fetchone()
                if existing and existing[0] != row["definition_hash"]:
                    raise ValueError(f"frozen hypothesis changed: {row['hypothesis_version']}")
                con.execute("""INSERT OR IGNORE INTO research_hypotheses
                (hypothesis_version,hypothesis_id,definition_hash,freeze_status,holdout_start,
                 frozen_at,payload_json) VALUES(?,?,?,?,?,?,?)""",
                            (row["hypothesis_version"], row["hypothesis_id"],
                             row["definition_hash"], row["freeze_status"], row["holdout_start"],
                             row["frozen_at"], _json(row)))

    def register_family(self, row: dict) -> None:
        digest = _hash(row)
        with self.db.connect() as con:
            existing = con.execute("""SELECT definition_hash FROM research_families
                WHERE family_id=? AND family_version=?""",
                                   (row["family_id"], row["family_version"])).fetchone()
            if existing and existing[0] != digest:
                raise ValueError("frozen family definition changed")
            con.execute("""INSERT OR IGNORE INTO research_families
            (family_id,family_version,definition_hash,family_holdout_start,frozen_at,payload_json)
            VALUES(?,?,?,?,?,?)""", (row["family_id"], row["family_version"], digest,
                                      row["family_holdout_start"], row["frozen_at"], _json(row)))

    def save_events(self, rows: list[dict]) -> int:
        before = self.count("research_events")
        _insert_dict_rows(self.db, "research_events", rows, replace=False,
                          json_column="payload_json")
        return self.count("research_events") - before

    def save_subjects(self, rows: list[dict]) -> int:
        before = self.count("validation_subjects")
        _insert_dict_rows(self.db, "validation_subjects", rows, replace=False)
        return self.count("validation_subjects") - before

    def save_control_members(self, rows: list[dict]) -> int:
        before = self.count("research_control_members")
        _insert_dict_rows(self.db, "research_control_members", rows, replace=False)
        return self.count("research_control_members") - before

    def save_control_history(self, rows: list[dict]) -> None:
        _insert_dict_rows(self.db, "research_control_history", rows, replace=True)

    def save_subject_history(self, rows: list[dict]) -> None:
        _insert_dict_rows(self.db, "research_subject_history", rows, replace=True)

    def rows(self, table: str, order_by: str = "") -> list[dict]:
        allowed = {
            "research_hypotheses", "research_families", "research_events",
            "validation_subjects", "research_control_members", "research_control_history",
            "research_subject_history", "research_checkpoint_evaluations",
            "research_results", "research_family_results", "research_telemetry",
        }
        if table not in allowed:
            raise ValueError(f"unsupported research table: {table}")
        with self.db.connect() as con:
            con.row_factory = __import__("sqlite3").Row
            result = [dict(row) for row in con.execute(
                f"SELECT * FROM {table}" + (f" ORDER BY {order_by}" if order_by else ""))]
        for row in result:
            for key in ("payload_json", "feature_snapshot_json"):
                if key in row:
                    try:
                        row[key.removesuffix("_json")] = json.loads(row.get(key) or "{}")
                    except json.JSONDecodeError:
                        row[key.removesuffix("_json")] = {}
        return result

    def control_members_for(self, subject_id: str, selection_version: str) -> list[dict]:
        with self.db.connect() as con:
            con.row_factory = __import__("sqlite3").Row
            return [dict(row) for row in con.execute("""SELECT * FROM research_control_members
                WHERE validation_subject_id=? AND selection_version=?
                ORDER BY control_type,control_rank,control_code""",
                                                        (subject_id, selection_version))]

    def checkpoint(self, hypothesis_version: str, checkpoint_name: str) -> dict | None:
        with self.db.connect() as con:
            con.row_factory = __import__("sqlite3").Row
            row = con.execute("""SELECT * FROM research_checkpoint_evaluations
                WHERE hypothesis_version=? AND checkpoint_name=?""",
                              (hypothesis_version, checkpoint_name)).fetchone()
        return dict(row) if row else None

    def save_checkpoint_once(self, row: dict) -> bool:
        before = self.checkpoint(row["hypothesis_version"], row["checkpoint_name"])
        _insert_dict_rows(self.db, "research_checkpoint_evaluations",
                          [{**row, "payload_json": _json(row)}], replace=False)
        return before is None

    def save_result_once(self, row: dict) -> bool:
        with self.db.connect() as con:
            exists = con.execute("SELECT 1 FROM research_results WHERE hypothesis_version=?",
                                 (row["hypothesis_version"],)).fetchone()
        _insert_dict_rows(self.db, "research_results",
                          [{**row, "payload_json": _json(row)}], replace=False)
        return not bool(exists)

    def save_family_result(self, row: dict) -> None:
        _insert_dict_rows(self.db, "research_family_results",
                          [{**row, "payload_json": _json(row)}], replace=True)

    def save_telemetry(self, row: dict) -> None:
        _insert_dict_rows(self.db, "research_telemetry",
                          [{**row, "payload_json": _json(row)}], replace=True)

    def compact_matured_history(self, horizons: tuple[int, ...] = (1, 5, 10, 20)) -> int:
        keep = ",".join("?" for _ in horizons)
        with self.db.connect() as con:
            groups = [row[0] for row in con.execute("""SELECT control_group_id
                FROM research_control_history GROUP BY control_group_id
                HAVING MAX(session_offset)>=20""")]
            removed = 0
            for group_id in groups:
                cursor = con.execute(f"""DELETE FROM research_control_history
                    WHERE control_group_id=? AND session_offset NOT IN ({keep})""",
                                     (group_id, *horizons))
                removed += cursor.rowcount
                con.execute("UPDATE research_control_history SET matured=1 WHERE control_group_id=?",
                            (group_id,))
        return removed

    def count(self, table: str) -> int:
        if table not in {"research_events", "validation_subjects", "research_control_members"}:
            raise ValueError(table)
        with self.db.connect() as con:
            return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def import_state(self, payload: dict[str, Any]) -> None:
        self.register_hypotheses([row["payload"] for row in payload.get("hypotheses", [])])
        for item in payload.get("families", []):
            self.register_family(item["payload"])
        for table in ("research_events", "validation_subjects", "research_control_members",
                      "research_control_history", "research_subject_history",
                      "research_checkpoint_evaluations", "research_results",
                      "research_family_results", "research_telemetry"):
            rows = payload.get(table, [])
            _insert_dict_rows(self.db, table, rows,
                              replace=table in {"research_control_history",
                                                "research_subject_history",
                                                "research_family_results",
                                                "research_telemetry"})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    import hashlib
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _insert_dict_rows(db: Database, table: str, rows: list[dict], *, replace: bool,
                      json_column: str | None = None) -> None:
    if not rows:
        return
    with db.connect() as con:
        allowed = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    prepared = []
    for original in rows:
        row = dict(original)
        if json_column and json_column not in row:
            row[json_column] = _json(original)
        prepared.append(row)
    columns = [key for key in prepared[0] if key in allowed and key != "created_at"]
    marks = ",".join("?" for _ in columns)
    operation = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    with db.connect() as con:
        con.executemany(f"{operation} INTO {table} ({','.join(columns)}) VALUES({marks})",
                        [[row.get(key) for key in columns] for row in prepared])
