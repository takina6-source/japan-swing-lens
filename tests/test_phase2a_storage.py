from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from engine.state_machine.adapter import adapt_core_artifacts
from engine.state_machine.artifacts import write_shadow_artifacts
from engine.state_machine.service import build_run_bundle, execute_shadow_run
from engine.state_machine.models import PermanentExitEvidence, PermanentExitType
from engine.state_machine.storage import (
    Phase2AInputConflict,
    Phase2ASeedError,
    Phase2AStorageError,
    Phase2AStore,
)


def core_inputs(price=980.0, generated_at="2026-01-05T16:00:00+09:00"):
    snapshot = {
        "generated_at": generated_at,
        "scope": "test-scope",
        "logic_version": "test-v1",
    }
    detail = {
        "code": "5901", "as_of": "2026-01-05", "state": "SETUP FORMING",
        "price": price, "aligned_strategy_count": 2, "breakout_strategy_count": 0,
        "methods": {
            "Minervini": "SETUP FORMING", "Qullamaggie": "SETUP FORMING",
            "CAN SLIM": "NOT QUALIFIED", "Weinstein": "NOT QUALIFIED",
            "Darvas": "NOT QUALIFIED", "Connors": "PULLBACK",
        },
        "strategies": {
            "Minervini": {
                "state": "SETUP FORMING", "pivot": 1000.0,
                "pivot_type": "VCP", "pivot_basis": "Structure",
                "pivot_fidelity": "PRACTICAL", "pivot_formed_date": "2026-01-02",
                "setup_id": "legacy-min",
            },
            "Qullamaggie": {
                "state": "SETUP FORMING", "pivot": 1000.0,
                "pivot_type": "20-day High", "pivot_basis": "Lookback Proxy",
                "pivot_fidelity": "PROXY", "pivot_formed_date": "2026-01-02",
                "setup_id": "legacy-q",
            },
        },
        "chart": [
            {"date": "2026-01-02", "close": 975.0},
            {"date": "2026-01-05", "close": price},
        ],
    }
    scope = (
        {"code": "5901", "in_scope": True, "price_history_count": 260},
        {"code": "8303", "in_scope": True, "price_history_count": 184},
    )
    return snapshot, {"5901": detail}, scope


def adapted(price=980.0):
    snapshot, details, scope = core_inputs(price)
    return adapt_core_artifacts(
        snapshot=snapshot, details_by_code=details, scope_members=scope,
        expected_market_date="2026-01-05", market_session_index=100,
        config_sha256="a" * 64,
    )


def kwargs():
    return {
        "identity_epoch": "cutover-2026-01-05",
        "started_at": "2026-01-05T16:01:00+09:00",
        "finished_at": "2026-01-05T16:02:00+09:00",
        "initialize_cutover": True,
        "cutover_manifest_sha256": "b" * 64,
    }


def adapt_day(date, session, state, price, previous, method_state):
    snapshot, details, scope = core_inputs(price, f"{date}T16:00:00+09:00")
    detail = details["5901"]
    detail["as_of"] = date
    detail["state"] = state
    detail["methods"]["Minervini"] = method_state
    detail["methods"]["Qullamaggie"] = method_state
    detail["aligned_strategy_count"] = int(method_state in {
        "SETUP FORMING", "BREAKOUT WATCH", "BREAKOUT"
    }) * 2
    detail["breakout_strategy_count"] = int(method_state == "BREAKOUT") * 2
    for strategy in detail["strategies"].values():
        strategy["state"] = method_state
        strategy["pivot_formed_date"] = "2026-01-02"
    detail["chart"] = [
        {"date": "2026-01-02", "close": previous}, {"date": date, "close": price}
    ]
    return adapt_core_artifacts(
        snapshot=snapshot, details_by_code=details, scope_members=scope,
        expected_market_date=date, market_session_index=session,
        config_sha256="a" * 64,
    )


def test_adapter_preserves_full_scope_and_excludes_connors():
    run = adapted()
    assert len(run.scope_members) == 2
    assert len(run.observations) == 1
    assert run.observations[0].aligned_trend_strategy_count == 2
    statuses = {row["code"]: row["observation_status"] for row in run.scope_members}
    assert statuses == {"5901": "CURRENT", "8303": "INSUFFICIENT_PRICE_HISTORY"}


def test_adapter_preserves_965_member_quality_distribution():
    snapshot, template_details, _ = core_inputs()
    template = template_details["5901"]
    scope = []
    details = {}
    for offset in range(965):
        code = f"{1000 + offset:04d}"
        if offset < 960:
            scope.append({"code": code, "in_scope": True, "price_history_count": 260})
            detail = json.loads(json.dumps(template))
            detail["code"] = code
            details[code] = detail
        elif offset < 964:
            scope.append({"code": code, "in_scope": True, "price_history_count": 260})
            detail = json.loads(json.dumps(template))
            detail["code"] = code
            detail["as_of"] = "2026-01-02"
            details[code] = detail
        else:
            scope.append({"code": code, "in_scope": True, "price_history_count": 184})
    run = adapt_core_artifacts(
        snapshot=snapshot, details_by_code=details, scope_members=scope,
        expected_market_date="2026-01-05", market_session_index=100,
    )
    counts = {}
    for row in run.scope_members:
        counts[row["observation_status"]] = counts.get(row["observation_status"], 0) + 1
    assert len(run.scope_members) == 965
    assert len(run.observations) == 960
    assert counts == {
        "CURRENT": 960,
        "STALE_MARKET_DATE": 4,
        "INSUFFICIENT_PRICE_HISTORY": 1,
    }


def test_run_status_coverage_and_publish_eligibility_are_independent(tmp_path):
    complete = Phase2AStore(tmp_path / "complete.db")
    complete.migrate()
    complete_bundle = build_run_bundle(complete, adapted(), **kwargs())
    assert (
        complete_bundle.run["processing_status"],
        complete_bundle.run["coverage_status"],
        complete_bundle.run["publish_eligibility"],
    ) == ("COMPLETE", "DEGRADED", "ELIGIBLE")

    snapshot, details, scope = core_inputs()
    snapshot["structured_errors"] = [{"stage": "fetch", "message": "unmapped"}]
    partial_run = adapt_core_artifacts(
        snapshot=snapshot, details_by_code=details, scope_members=scope,
        expected_market_date="2026-01-05", market_session_index=100,
    )
    partial = Phase2AStore(tmp_path / "partial.db")
    partial.migrate()
    partial_bundle = build_run_bundle(partial, partial_run, **kwargs())
    assert (
        partial_bundle.run["processing_status"],
        partial_bundle.run["coverage_status"],
        partial_bundle.run["publish_eligibility"],
    ) == ("PARTIAL", "UNKNOWN", "ELIGIBLE_DEGRADED")
    assert "UPSTREAM_STRUCTURED_ERRORS_PRESENT" in json.loads(
        partial_bundle.run["reason_codes_json"]
    )


def test_atomic_commit_idempotency_seed_and_restore(tmp_path):
    store = Phase2AStore(tmp_path / "shadow.db")
    store.migrate(applied_at="2026-01-05T16:00:00+09:00")
    bundle = build_run_bundle(store, adapted(), **kwargs())
    assert store.commit_run(bundle) == "COMMITTED"
    counts = store.table_counts()
    assert counts["phase2a_runs"] == 1
    assert counts["phase2a_scope_members"] == 2
    assert counts["phase2a_identity_ledger"] == 1
    assert counts["phase2a_strategy_identity_ledger"] == 2
    assert counts["phase2a_events"] == 1
    assert counts["phase2a_current_states"] == 1
    assert store.commit_run(bundle) == "NO_OP"
    assert store.integrity() == {"integrity_check": "ok", "foreign_key_errors": []}

    artifacts = write_shadow_artifacts(store, bundle.run["run_id"], tmp_path / "artifacts")
    assert set(artifacts) == {
        "run-summary.json", "latest-state.json", "recent-events.json", "rejections.json"
    }
    assert artifacts["latest-state.json"]["setups"][0]["distribution_eligible"] == 1
    assert not any("chart" in json.dumps(payload) for payload in artifacts.values())

    state_hash = store.state_hash()
    manifest = store.export_seed(tmp_path / "seed")
    assert manifest["row_counts"]["identity_ledger"] == 1
    restored = Phase2AStore.restore_seed(tmp_path / "seed", tmp_path / "restored.db")
    assert restored.integrity() == {"integrity_check": "ok", "foreign_key_errors": []}
    assert restored.state_hash() == state_hash


def test_same_seed_input_and_versions_build_identical_hashes(tmp_path):
    first_store = Phase2AStore(tmp_path / "deterministic-a.db")
    second_store = Phase2AStore(tmp_path / "deterministic-b.db")
    first_store.migrate(applied_at="2026-01-05T00:00:00+00:00")
    second_store.migrate(applied_at="2026-01-05T00:00:00+00:00")
    first = build_run_bundle(first_store, adapted(), **kwargs())
    second = build_run_bundle(second_store, adapted(), **kwargs())
    assert first.run == second.run
    assert first.ledger_rows == second.ledger_rows
    assert first.events == second.events
    assert first.current_states == second.current_states


def test_identity_short_uid_collision_with_different_full_hash_stops(tmp_path):
    store = Phase2AStore(tmp_path / "collision.db")
    store.migrate()
    bundle = build_run_bundle(store, adapted(), **kwargs())
    store.commit_run(bundle)
    collision = dict(bundle.ledger_rows[0])
    collision["mint_request_sha256"] = "f" * 64
    collision["canonical_mint_request"] = '{"different":true}'
    with store.connect() as conn, pytest.raises(Phase2AStorageError, match="collision"):
        store._insert_ledger_idempotent(conn, collision)


def test_seed_corruption_fails_closed(tmp_path):
    store = Phase2AStore(tmp_path / "shadow.db")
    store.migrate()
    store.commit_run(build_run_bundle(store, adapted(), **kwargs()))
    store.export_seed(tmp_path / "seed")
    sidecar = tmp_path / "seed" / "seed.sha256"
    sidecar.write_text("0" * 64 + "  seed.sqlite\n", encoding="utf-8")
    with pytest.raises(Phase2ASeedError, match="hash mismatch"):
        Phase2AStore.verify_seed(tmp_path / "seed")
    assert store.table_counts()["phase2a_identity_ledger"] == 1


@pytest.mark.parametrize(
    "failure_kind,match",
    [
        ("missing", "incomplete"),
        ("truncated", "hash mismatch"),
        ("seed_schema", "seed schema version mismatch"),
        ("operational_schema", "operational schema hash mismatch"),
        ("row_count", "seed row count mismatch"),
    ],
)
def test_seed_failure_modes_all_stop_before_restore(tmp_path, failure_kind, match):
    store = Phase2AStore(tmp_path / "source.db")
    store.migrate()
    store.commit_run(build_run_bundle(store, adapted(), **kwargs()))
    seed_dir = tmp_path / f"seed-{failure_kind}"
    store.export_seed(seed_dir)
    manifest_path = seed_dir / "seed-manifest.json"
    if failure_kind == "missing":
        (seed_dir / "seed.sha256").unlink()
    elif failure_kind == "truncated":
        seed_path = seed_dir / "seed.sqlite"
        seed_path.write_bytes(seed_path.read_bytes()[:128])
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if failure_kind == "seed_schema":
            manifest["seed_schema_version"] = "phase2a-seed-v999"
        elif failure_kind == "operational_schema":
            manifest["operational_schema_sha256"] = "0" * 64
        else:
            manifest["row_counts"]["current_states"] += 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(Phase2ASeedError, match=match):
        Phase2AStore.restore_seed(seed_dir, tmp_path / "must-not-exist.db")
    assert not (tmp_path / "must-not-exist.db").exists()


def test_failure_injection_rolls_back_and_retry_commits_once(tmp_path):
    store = Phase2AStore(tmp_path / "shadow.db")
    store.migrate()
    bundle = build_run_bundle(store, adapted(), **kwargs())
    with pytest.raises(Phase2AStorageError, match="EVENT_INSERT"):
        store.commit_run(bundle, fail_after_stage="EVENT_INSERT")
    counts = store.table_counts()
    assert all(value == 0 for value in counts.values())
    assert store.commit_run(bundle) == "COMMITTED"
    assert store.commit_run(bundle) == "NO_OP"
    counts = store.table_counts()
    assert counts["phase2a_identity_ledger"] == 1
    assert counts["phase2a_events"] == 1


@pytest.mark.parametrize(
    "stage",
    [
        "RUN_INSERT", "SCOPE_INSERT", "OBSERVATION_INSERT", "IDENTITY_INSERT",
        "PIVOT_MEMBERSHIP_INSERT", "EVENT_INSERT", "STATE_UPDATE", "INVARIANT_CHECK",
    ],
)
def test_every_commit_stage_failure_rolls_back_without_orphans(tmp_path, stage):
    store = Phase2AStore(tmp_path / f"failure-{stage}.db")
    store.migrate()
    bundle = build_run_bundle(store, adapted(), **kwargs())
    with pytest.raises(Phase2AStorageError, match=stage):
        store.commit_run(bundle, fail_after_stage=stage)
    assert all(value == 0 for value in store.table_counts().values())


def test_migration_is_idempotent_and_schema_hash_mismatch_stops(tmp_path):
    store = Phase2AStore(tmp_path / "migration.db")
    store.migrate(applied_at="2026-01-05T00:00:00+00:00")
    store.migrate(applied_at="2026-01-06T00:00:00+00:00")
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM phase2a_schema_migrations").fetchone()[0] == 1
        conn.execute("UPDATE phase2a_schema_migrations SET schema_sha256='tampered'")
    with pytest.raises(Phase2AStorageError, match="different hash"):
        store.migrate()


def test_missing_seed_cannot_mint_without_explicit_cutover(tmp_path):
    store = Phase2AStore(tmp_path / "shadow.db")
    store.migrate()
    options = kwargs()
    options["initialize_cutover"] = False
    options["cutover_manifest_sha256"] = None
    with pytest.raises(Phase2ASeedError, match="verified seed or explicit cutover"):
        build_run_bundle(store, adapted(), **options)
    assert store.table_counts()["phase2a_identity_ledger"] == 0


def test_same_day_different_input_is_quarantined_by_unique_run_date(tmp_path):
    store = Phase2AStore(tmp_path / "shadow.db")
    store.migrate()
    first = build_run_bundle(store, adapted(980), **kwargs())
    store.commit_run(first)
    snapshot, details, scope = core_inputs(981)
    changed = adapt_core_artifacts(
        snapshot=snapshot, details_by_code=details, scope_members=scope,
        expected_market_date="2026-01-05", market_session_index=100,
        config_sha256="a" * 64,
    )
    # Build from a fresh cutover store so only commit-time same-day protection is tested.
    fresh = Phase2AStore(tmp_path / "fresh.db")
    fresh.migrate()
    changed_bundle = build_run_bundle(fresh, changed, **kwargs())
    with pytest.raises(Phase2AInputConflict, match="same-day input conflict"):
        store.commit_run(changed_bundle)


def test_full_failure_retry_rebreakout_cycle(tmp_path):
    store = Phase2AStore(tmp_path / "cycle.db")
    store.migrate()
    days = [
        ("2026-01-05", 100, "SETUP FORMING", 980, 975, "SETUP FORMING"),
        ("2026-01-06", 101, "BREAKOUT WATCH", 990, 980, "BREAKOUT WATCH"),
        ("2026-01-07", 102, "BREAKOUT", 1010, 990, "BREAKOUT"),
        ("2026-01-08", 103, "FAILED", 969, 1010, "FAILED"),
        ("2026-01-09", 104, "SETUP FORMING", 980, 969, "SETUP FORMING"),
        ("2026-01-12", 105, "BREAKOUT", 1015, 980, "BREAKOUT"),
    ]
    for index, args in enumerate(days):
        adapted_run = adapt_day(*args)
        options = {
            "identity_epoch": "cutover-2026-01-05",
            "started_at": adapted_run.generated_at,
            "finished_at": adapted_run.generated_at,
            "initialize_cutover": index == 0,
            "cutover_manifest_sha256": "b" * 64 if index == 0 else None,
        }
        assert store.commit_run(build_run_bundle(store, adapted_run, **options)) == "COMMITTED"
    with store.connect() as conn:
        events = [row[0] for row in conn.execute(
            "SELECT event_type FROM phase2a_events ORDER BY effective_date,event_uid"
        )]
        state = conn.execute(
            "SELECT current_phase,breakout_count,failure_cycle_no,tracking_pivot_revision_no "
            "FROM phase2a_current_states"
        ).fetchone()
        frozen = conn.execute(
            "SELECT frozen_after_breakout FROM phase2a_pivot_revisions"
        ).fetchone()[0]
    assert events == [
        "SETUP_MINTED", "WATCH_ENTERED", "BREAKOUT_CONFIRMED", "FAILED_CONFIRMED",
        "RETRY_WATCH_ENTERED", "REBREAKOUT_CONFIRMED",
    ]
    assert tuple(state) == ("POST_BREAKOUT", 2, 1, 1)
    assert frozen == 1


def test_permanent_exit_closes_but_dynamic_scope_removal_does_not(tmp_path):
    store = Phase2AStore(tmp_path / "closure.db")
    store.migrate()
    first = adapted()
    store.commit_run(build_run_bundle(store, first, **kwargs()))

    snapshot, _, scope = core_inputs()
    snapshot["generated_at"] = "2026-01-06T16:00:00+09:00"
    out_scope = [dict(row) for row in scope]
    out_scope[0].update(in_scope=False, observation_status="OUT_OF_SCOPE")
    second = adapt_core_artifacts(
        snapshot=snapshot, details_by_code={}, scope_members=out_scope,
        expected_market_date="2026-01-06", market_session_index=101,
        config_sha256="a" * 64,
    )
    options = {
        "identity_epoch": "cutover-2026-01-05", "started_at": second.generated_at,
        "finished_at": second.generated_at,
        "permanent_exit_evidence_by_code": {
            "5901": PermanentExitEvidence(
                PermanentExitType.DELISTED_CONFIRMED, "master:2026-01-06:5901", "2026-01-06"
            )
        },
    }
    store.commit_run(build_run_bundle(store, second, **options))
    with store.connect() as conn:
        phase = conn.execute("SELECT current_phase FROM phase2a_current_states").fetchone()[0]
        closed = conn.execute(
            "SELECT count(*) FROM phase2a_events WHERE event_type='SETUP_CLOSED'"
        ).fetchone()[0]
    assert phase == "CLOSED"
    assert closed == 1


def test_schema_rejects_closed_event_without_permanent_evidence(tmp_path):
    store = Phase2AStore(tmp_path / "closed-evidence.db")
    store.migrate()
    bundle = build_run_bundle(store, adapted(), **kwargs())
    event = dict(bundle.events[0])
    event["event_type"] = "SETUP_CLOSED"
    event["permanent_exit_evidence_ref"] = None
    broken = replace(bundle, events=(event,))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        store.commit_run(broken)
    assert all(value == 0 for value in store.table_counts().values())


def test_optimistic_conflict_rolls_back_entire_run(tmp_path):
    store = Phase2AStore(tmp_path / "optimistic.db")
    store.migrate()
    store.commit_run(build_run_bundle(store, adapted(), **kwargs()))
    second = adapt_day("2026-01-06", 101, "BREAKOUT WATCH", 990, 980, "BREAKOUT WATCH")
    options = {
        "identity_epoch": "cutover-2026-01-05", "started_at": second.generated_at,
        "finished_at": second.generated_at,
    }
    bundle = build_run_bundle(store, second, **options)
    broken_state = dict(bundle.current_states[0])
    broken_state["expected_state_version"] = 0
    broken = replace(bundle, current_states=(broken_state,))
    before = store.table_counts()
    with pytest.raises(Phase2AStorageError, match="state version conflict"):
        store.commit_run(broken)
    assert store.table_counts() == before


def test_pivot_lookahead_is_rejected_by_adapter():
    snapshot, details, scope = core_inputs()
    for strategy in details["5901"]["strategies"].values():
        strategy["pivot_formed_date"] = "2026-01-05"
    run = adapt_core_artifacts(
        snapshot=snapshot, details_by_code=details, scope_members=scope,
        expected_market_date="2026-01-05", market_session_index=100,
    )
    status = {row["code"]: row["observation_status"] for row in run.scope_members}
    assert status["5901"] == "INVALID_INPUT"
    assert run.observations == ()


def test_pivot_without_source_date_is_rejected_by_adapter():
    snapshot, details, scope = core_inputs()
    for strategy in details["5901"]["strategies"].values():
        strategy.pop("pivot_formed_date")
    run = adapt_core_artifacts(
        snapshot=snapshot, details_by_code=details, scope_members=scope,
        expected_market_date="2026-01-05", market_session_index=100,
    )
    status = {row["code"]: row["observation_status"] for row in run.scope_members}
    reasons = {row["code"]: row["reason_codes"] for row in run.scope_members}
    assert status["5901"] == "INVALID_INPUT"
    assert reasons["5901"] == ["PIVOT_REFERENCE_DATE_MISSING"]
    assert run.observations == ()


def test_execute_shadow_run_records_separate_failure_manifest(tmp_path, monkeypatch):
    store = Phase2AStore(tmp_path / "failed-run.db")
    store.migrate()

    def fail_commit(bundle):
        raise Phase2AStorageError("synthetic commit failure")

    monkeypatch.setattr(store, "commit_run", fail_commit)
    with pytest.raises(Phase2AStorageError, match="synthetic commit failure"):
        execute_shadow_run(store, adapted(), **kwargs())

    counts = store.table_counts()
    assert all(value == 0 for value in counts.values())
    with store.connect() as conn:
        failure = conn.execute(
            "SELECT stage,reason_codes_json,error_digest "
            "FROM phase2a_failed_run_manifests"
        ).fetchone()
    assert failure[0] == "ATOMIC_COMMIT"
    assert json.loads(failure[1]) == ["STATE_TRANSACTION_FAILED"]
    assert len(failure[2]) == 64


def test_replay_lineage_is_separate_and_never_merged(tmp_path):
    store = Phase2AStore(tmp_path / "replay.db")
    store.migrate()
    store.create_replay_lineage(
        source_lineage="live-sm1", replay_lineage="replay-audit-1",
        requested_at="2026-01-05T00:00:00Z", reason="OUT_OF_ORDER_OBSERVATION",
    )
    with store.connect() as conn:
        row = conn.execute("SELECT source_lineage,merged_to_live FROM phase2a_replay_lineages").fetchone()
    assert tuple(row) == ("live-sm1", 0)
    with pytest.raises(Phase2AStorageError, match="must be separate"):
        store.create_replay_lineage(
            source_lineage="live-sm1", replay_lineage="live-sm1",
            requested_at="2026-01-05T00:00:00Z", reason="bad",
        )
