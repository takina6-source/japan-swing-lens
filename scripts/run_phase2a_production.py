#!/usr/bin/env python3
"""Run one fail-closed Phase 2A production-shadow cycle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1].resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.state_machine.adapter import adapt_core_artifacts
from engine.state_machine.artifacts import write_shadow_artifacts
from engine.state_machine.config import (
    IDENTITY_DECISION_RULE_VERSION,
    IDENTITY_VERSION,
    STATE_MACHINE_VERSION,
    THRESHOLD_VERSION,
)
from engine.state_machine.deployment import (
    LINEAGE,
    canonical_json,
    next_market_session_index,
    pack_seed,
    validate_release_manifest,
)
from engine.state_machine.service import execute_shadow_run
from engine.state_machine.storage import Phase2ASeedError, Phase2AStore
from engine.state_machine.ids import hash_payload
from scripts.run_state_machine_shadow import _ensure_private_path


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_core_input(root: Path) -> dict:
    manifest = _json(root / "input-manifest.json")
    if manifest.get("contract_version") != "phase2a-core-input-v1":
        raise Phase2ASeedError("Core input contract version mismatch")
    for name, expected in manifest.get("files", {}).items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise Phase2ASeedError(f"Core input hash mismatch: {name}")
    return manifest


def _details(root: Path, codes: list[str]) -> dict[str, dict]:
    return {
        code: _json(root / "details" / f"{code}.json")
        for code in codes if (root / "details" / f"{code}.json").is_file()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("initialize", "continue"), required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed-output", type=Path, required=True)
    parser.add_argument("--release-output", type=Path, required=True)
    parser.add_argument("--identity-epoch", required=True)
    parser.add_argument("--producer-commit-sha", required=True)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--previous-release-manifest", type=Path)
    parser.add_argument("--cutover-manifest", type=Path)
    parser.add_argument("--cutover-manifest-sha256")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)

    input_root = _ensure_private_path(args.input_dir)
    database = _ensure_private_path(args.db, database=True)
    output = _ensure_private_path(args.output_dir)
    seed_output = _ensure_private_path(args.seed_output)
    release_output = _ensure_private_path(args.release_output)
    input_manifest = _verify_core_input(input_root)
    snapshot = _json(input_root / "snapshot.json")
    scope_payload = _json(input_root / "scope.json")
    scope = scope_payload["members"]
    expected = str(snapshot["as_of"])
    market_dates = _json(input_root / "market-dates.json")

    initialize = args.mode == "initialize"
    verified_seed = None
    cutover_sha = None
    if initialize:
        if args.seed or args.previous_release_manifest:
            raise Phase2ASeedError("initialize must start without a prior seed")
        if not args.cutover_manifest or not args.cutover_manifest_sha256:
            raise Phase2ASeedError("initialize requires the approved cutover manifest and SHA-256")
        actual = hashlib.sha256(args.cutover_manifest.read_bytes()).hexdigest()
        if actual != args.cutover_manifest_sha256:
            raise Phase2ASeedError("cutover manifest SHA-256 mismatch")
        cutover = _json(args.cutover_manifest)
        if cutover.get("manifest_version") != "phase2a-cutover-v1":
            raise Phase2ASeedError("cutover manifest version mismatch")
        if cutover.get("expected_market_date") != expected:
            raise Phase2ASeedError("cutover market date does not match the Core input")
        if cutover.get("scope_name") != snapshot.get("scope"):
            raise Phase2ASeedError("cutover scope name does not match the Core input")
        if cutover.get("scope_member_count") != len(scope):
            raise Phase2ASeedError("cutover scope count does not match the Core input")
        scope_hash = hash_payload([
            {"code": str(row["code"]), "in_scope": bool(row.get("in_scope", True))}
            for row in sorted(scope, key=lambda item: str(item["code"]))
        ])
        if cutover.get("scope_member_sha256") != scope_hash:
            raise Phase2ASeedError("cutover scope hash does not match the Core input")
        if cutover.get("core_config_sha256") != input_manifest["config_sha256"]:
            raise Phase2ASeedError("cutover Core config hash does not match the Core input")
        if cutover.get("state_machine_version") != STATE_MACHINE_VERSION:
            raise Phase2ASeedError("cutover state-machine version mismatch")
        if cutover.get("threshold_version") != THRESHOLD_VERSION:
            raise Phase2ASeedError("cutover threshold version mismatch")
        if cutover.get("identity_version") != IDENTITY_VERSION:
            raise Phase2ASeedError("cutover identity version mismatch")
        if cutover.get("decision_rule_version") != IDENTITY_DECISION_RULE_VERSION:
            raise Phase2ASeedError("cutover decision-rule version mismatch")
        if cutover.get("identity_epoch") != args.identity_epoch:
            raise Phase2ASeedError("cutover identity epoch mismatch")
        if cutover.get("automatic_closed_enabled") is not False:
            raise Phase2ASeedError("automatic CLOSED must remain disabled at cutover")
        market_index = int(cutover.get("market_session_index", -1))
        if market_index != 0:
            raise Phase2ASeedError("first cutover market-session index must be zero")
        cutover_sha = actual
    else:
        if not args.seed or not args.previous_release_manifest:
            raise Phase2ASeedError("continue requires a verified seed and its release manifest")
        previous = validate_release_manifest(_json(args.previous_release_manifest))
        verified_seed = Phase2AStore.verify_seed(args.seed)
        if previous["seed_manifest"] != verified_seed:
            raise Phase2ASeedError("release manifest does not describe the supplied seed")
        market_index = next_market_session_index(previous, market_dates, expected)

    codes = [str(row["code"]) for row in scope]
    adapted = adapt_core_artifacts(
        snapshot=snapshot, details_by_code=_details(input_root, codes),
        scope_members=scope, expected_market_date=expected,
        market_session_index=market_index, state_lineage=LINEAGE,
        config_sha256=input_manifest["config_sha256"],
    )
    if not initialize and expected == previous["expected_market_date"]:
        if adapted.input_sha256 != previous["input_sha256"]:
            raise Phase2ASeedError("same-market-date input conflicts with the promoted seed")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as handle:
                handle.write("promote=false\n")
                handle.write("status=NO_OP\n")
        print(canonical_json({"status": "NO_OP", "run_id": adapted.run_id, "promote": False}))
        return 0
    if initialize:
        store = Phase2AStore(database)
        store.migrate(applied_at=str(snapshot["generated_at"]))
    else:
        store = Phase2AStore.restore_seed(args.seed, database)
    result, bundle = execute_shadow_run(
        store, adapted, identity_epoch=args.identity_epoch,
        started_at=str(snapshot["generated_at"]), finished_at=str(snapshot["generated_at"]),
        initialize_cutover=initialize, cutover_manifest_sha256=cutover_sha,
        verified_seed_manifest=verified_seed,
    )
    if result not in {"COMMITTED", "NO_OP"}:
        raise Phase2ASeedError(f"unexpected state commit result: {result}")
    write_shadow_artifacts(store, adapted.run_id, output)
    seed_manifest = store.export_seed(seed_output)
    release_output.mkdir(parents=True, exist_ok=True)
    safe_run = adapted.run_id.replace(":", "-")
    asset_name = f"phase2a-seed-{expected}-{safe_run}.tar.gz"
    archive = release_output / asset_name
    archive_sha = pack_seed(seed_output, archive)
    release_manifest = {
        "manifest_version": "phase2a-release-asset-v1",
        "asset_name": asset_name,
        "asset_sha256": archive_sha,
        "input_sha256": adapted.input_sha256,
        "expected_market_date": expected,
        "market_session_index": market_index,
        "run_id": adapted.run_id,
        "state_lineage": LINEAGE,
        "state_machine_version": STATE_MACHINE_VERSION,
        "threshold_version": THRESHOLD_VERSION,
        "seed_manifest": seed_manifest,
        "producer_commit_sha": args.producer_commit_sha,
        "processing_status": bundle.run["processing_status"],
        "coverage_status": bundle.run["coverage_status"],
        "publish_eligibility": bundle.run["publish_eligibility"],
    }
    validate_release_manifest(release_manifest)
    manifest_path = release_output / f"{asset_name}.manifest.json"
    manifest_path.write_text(canonical_json(release_manifest) + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write("promote=true\n")
            handle.write(f"status={result}\n")
    print(canonical_json({
        "status": result, "run_id": adapted.run_id, "asset": asset_name,
        "asset_sha256": archive_sha, "release_manifest": manifest_path.name,
    }))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 2A production run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
