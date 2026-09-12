#!/usr/bin/env python3
"""Explicit offline runner for the Phase 2A shadow state machine."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from engine.state_machine.adapter import adapt_core_artifacts
from engine.state_machine.artifacts import write_shadow_artifacts
from engine.state_machine.config import STATE_MACHINE_VERSION, THRESHOLD_VERSION
from engine.state_machine.service import execute_shadow_run
from engine.state_machine.storage import Phase2ASeedError, Phase2AStore


ROOT = Path(__file__).parents[1].resolve()
PRODUCT_DATABASES = {(ROOT / "data/momentum.db").resolve(), (ROOT / "momentum.db").resolve()}
PUBLIC_ROOT = (ROOT / "public").resolve()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_private_path(path: Path, *, database: bool = False) -> Path:
    resolved = path.expanduser().resolve()
    if database and resolved in PRODUCT_DATABASES:
        raise ValueError("the shadow runner refuses to open a product database")
    if resolved == PUBLIC_ROOT or resolved.is_relative_to(PUBLIC_ROOT):
        raise ValueError("Phase 2A shadow output must not be written under public/")
    return resolved


def _load_details(root: Path, codes: list[str]) -> dict[str, dict]:
    details: dict[str, dict] = {}
    for code in codes:
        path = root / f"{code}.json"
        if path.is_file():
            details[code] = _json(path)
    return details


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--details-dir", required=True, type=Path)
    parser.add_argument("--scope-manifest", required=True, type=Path)
    parser.add_argument("--expected-date", required=True)
    parser.add_argument("--market-session-index", required=True, type=int)
    parser.add_argument("--state-lineage", default="live-sm1")
    parser.add_argument("--state-machine-version", default=STATE_MACHINE_VERSION)
    parser.add_argument("--threshold-version", default=THRESHOLD_VERSION)
    parser.add_argument("--identity-epoch", required=True)
    parser.add_argument("--db", required=False, type=Path)
    parser.add_argument("--output-dir", required=False, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--seed-output", type=Path)
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--initialize-cutover", action="store_true")
    parser.add_argument("--cutover-manifest-sha256")
    parser.add_argument("--dry-run", "--validate-only", action="store_true")
    args = parser.parse_args(argv)

    if args.state_machine_version != STATE_MACHINE_VERSION:
        parser.error(
            f"unsupported state-machine version: {args.state_machine_version}; "
            f"expected {STATE_MACHINE_VERSION}"
        )
    if args.threshold_version != THRESHOLD_VERSION:
        parser.error(
            f"unsupported threshold version: {args.threshold_version}; "
            f"expected {THRESHOLD_VERSION}"
        )

    snapshot = _json(args.snapshot)
    scope_payload = _json(args.scope_manifest)
    scope_members = scope_payload.get("members") if isinstance(scope_payload, dict) else scope_payload
    if not isinstance(scope_members, list):
        raise ValueError("scope manifest must be a list or an object with members")
    codes = [str(row["code"]) for row in scope_members]
    details = _load_details(args.details_dir, codes)
    config_hash = (
        hashlib.sha256(args.config.read_bytes()).hexdigest() if args.config else "UNKNOWN"
    )
    adapted = adapt_core_artifacts(
        snapshot=snapshot, details_by_code=details, scope_members=scope_members,
        expected_market_date=args.expected_date,
        market_session_index=args.market_session_index,
        state_lineage=args.state_lineage, config_sha256=config_hash,
    )
    if args.dry_run:
        print(json.dumps({
            "status": "VALID", "write_performed": False, "run_id": adapted.run_id,
            "scope_count": len(adapted.scope_members),
            "current_observations": len(adapted.observations),
            "state_machine_version": STATE_MACHINE_VERSION,
            "threshold_version": THRESHOLD_VERSION,
            "input_sha256": adapted.input_sha256,
        }, ensure_ascii=False, sort_keys=True))
        return 0

    if args.db is None or args.output_dir is None:
        parser.error("--db and --output-dir are required unless --dry-run is used")
    database = _ensure_private_path(args.db, database=True)
    output = _ensure_private_path(args.output_dir)
    seed_output = _ensure_private_path(args.seed_output) if args.seed_output else None

    verified_seed = None
    if args.seed:
        seed = _ensure_private_path(args.seed)
        verified_seed = Phase2AStore.verify_seed(seed)
        if database.exists() and database.stat().st_size:
            raise Phase2ASeedError("--seed restore requires a new empty --db path")
        store = Phase2AStore.restore_seed(seed, database)
    else:
        store = Phase2AStore(database)
        if args.migrate:
            store.migrate(applied_at=str(snapshot.get("generated_at") or args.expected_date))
    if not store.schema_verified():
        raise Phase2ASeedError("schema is not verified; use --migrate or restore --seed")

    timestamp = str(snapshot.get("generated_at") or "")
    if not timestamp:
        raise ValueError("snapshot.generated_at is required for deterministic shadow output")
    result, bundle = execute_shadow_run(
        store, adapted, identity_epoch=args.identity_epoch,
        started_at=timestamp, finished_at=timestamp,
        initialize_cutover=args.initialize_cutover,
        cutover_manifest_sha256=args.cutover_manifest_sha256,
        verified_seed_manifest=verified_seed,
    )
    artifacts = write_shadow_artifacts(store, adapted.run_id, output)
    seed_manifest = store.export_seed(seed_output) if seed_output else None
    print(json.dumps({
        "status": result, "run_id": adapted.run_id,
        "processing_status": bundle.run["processing_status"],
        "coverage_status": bundle.run["coverage_status"],
        "artifact_files": sorted(artifacts), "seed": seed_manifest,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 2A shadow run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
