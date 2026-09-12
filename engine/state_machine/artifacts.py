"""Compact private artifacts for Phase 2A shadow review."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .ids import canonical_json
from .storage import Phase2AStore


ROOT = Path(__file__).parents[2]
SCHEMA_DIR = ROOT / "schemas"
ARTIFACT_SCHEMAS = {
    "run-summary.json": SCHEMA_DIR / "phase2a_run_summary.schema.json",
    "latest-state.json": SCHEMA_DIR / "phase2a_latest_state.schema.json",
    "recent-events.json": SCHEMA_DIR / "phase2a_recent_events.schema.json",
    "rejections.json": SCHEMA_DIR / "phase2a_rejections.schema.json",
}


def _parse_json_fields(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    result = dict(row)
    for field in fields:
        value = result.get(field)
        if isinstance(value, str):
            result[field.removesuffix("_json")] = json.loads(value)
            del result[field]
    return result


def build_shadow_artifacts(
    store: Phase2AStore, run_id: str, *, recent_market_days: int = 30
) -> dict[str, dict[str, Any]]:
    with store.connect() as conn:
        run_row = conn.execute(
            "SELECT * FROM phase2a_runs WHERE run_id=? AND state_commit_status='COMMITTED'",
            (run_id,),
        ).fetchone()
        if run_row is None:
            raise ValueError("shadow artifacts require a committed run")
        run = _parse_json_fields(
            dict(run_row), ("versions_json", "reason_codes_json", "structured_errors_json")
        )
        states = [dict(row) for row in conn.execute(
            "SELECT * FROM phase2a_current_states WHERE state_lineage=? ORDER BY code,core_setup_uid",
            (run["state_lineage"],),
        )]
        dates = [row[0] for row in conn.execute(
            "SELECT DISTINCT expected_market_date FROM phase2a_runs "
            "WHERE state_lineage=? AND state_commit_status='COMMITTED' "
            "ORDER BY expected_market_date DESC LIMIT ?",
            (run["state_lineage"], recent_market_days),
        )]
        minimum_date = min(dates) if dates else run["expected_market_date"]
        events = [
            _parse_json_fields(dict(row), ("reason_codes_json",))
            for row in conn.execute(
                "SELECT * FROM phase2a_events WHERE effective_date>=? "
                "ORDER BY effective_date,event_uid", (minimum_date,),
            )
        ]
        rejections = [
            _parse_json_fields(dict(row), ("reason_codes_json",))
            for row in conn.execute(
                "SELECT * FROM phase2a_rejections WHERE run_id=? ORDER BY rejection_uid", (run_id,),
            )
        ]

    run_summary = {
        "schema_version": "phase2a-shadow-run-v1",
        "generated_at": run["finished_at"], "source_run_id": run_id,
        "state_lineage": run["state_lineage"],
        "processing_status": run["processing_status"],
        "coverage_status": run["coverage_status"],
        "publish_eligibility": run["publish_eligibility"],
        "expected_market_date": run["expected_market_date"],
        "market_session_index": run["market_session_index"],
        "scope": {
            "name": run["scope_name"], "count": run["scope_total"],
            "member_sha256": run["scope_member_sha256"],
        },
        "observation_counts": {
            "current": run["current_total"], "no_new_market": run["no_new_market_total"],
            "stale": run["stale_total"], "insufficient": run["insufficient_total"],
            "fetch_failed": run["fetch_failed_total"],
            "analysis_failed": run["analysis_failed_total"],
            "invalid_input": run["invalid_input_total"],
        },
        "identity_counts": {
            "link": run["identity_link_total"], "mint": run["identity_mint_total"],
            "ambiguous": run["identity_ambiguous_total"], "no_setup": run["no_setup_total"],
        },
        "transition_count": run["transition_total"],
        "rejected_count": run["rejected_total"],
        "versions": run["versions"], "reason_codes": run["reason_codes"],
        "structured_errors": run["structured_errors"],
        "input_sha256": run["input_sha256"],
    }
    latest_state = {
        "schema_version": "phase2a-shadow-state-v1",
        "generated_at": run["finished_at"], "source_run_id": run_id,
        "state_lineage": run["state_lineage"], "setups": states,
    }
    recent_events = {
        "schema_version": "phase2a-shadow-events-v1",
        "generated_at": run["finished_at"], "source_run_id": run_id,
        "state_lineage": run["state_lineage"],
        "market_day_window": recent_market_days, "events": events,
    }
    rejection_artifact = {
        "schema_version": "phase2a-shadow-rejections-v1",
        "generated_at": run["finished_at"], "source_run_id": run_id,
        "rejections": rejections,
    }
    return {
        "run-summary.json": run_summary,
        "latest-state.json": latest_state,
        "recent-events.json": recent_events,
        "rejections.json": rejection_artifact,
    }


def validate_shadow_artifacts(artifacts: dict[str, dict[str, Any]]) -> None:
    for name, schema_path in ARTIFACT_SCHEMAS.items():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(artifacts[name])


def write_shadow_artifacts(
    store: Phase2AStore, run_id: str, output_dir: str | Path,
) -> dict[str, dict[str, Any]]:
    artifacts = build_shadow_artifacts(store, run_id)
    validate_shadow_artifacts(artifacts)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for name, payload in artifacts.items():
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{name}.", dir=output)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(canonical_json(payload) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, output / name)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
    return artifacts
