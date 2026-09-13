from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from engine.state_machine.core_input import write_core_input_bundle
from engine.state_machine.deployment import (
    next_market_session_index,
    safe_extract_seed,
    select_latest_manifest,
)
from engine.state_machine.ids import hash_payload
from engine.state_machine.storage import Phase2ASeedError
from scripts.run_phase2a_production import main
from tests.test_phase2a_storage import core_inputs


ROOT = Path(__file__).parents[1]


def test_production_runner_supports_direct_script_execution() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run_phase2a_production.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--mode" in result.stdout


def _bundle(tmp_path: Path) -> tuple[Path, tuple[dict, ...], str]:
    snapshot, details, scope = core_inputs()
    snapshot["as_of"] = "2026-01-05"
    snapshot_path = tmp_path / "snapshot.json"
    detail_dir = tmp_path / "source-details"
    detail_dir.mkdir()
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    for code, detail in details.items():
        (detail_dir / f"{code}.json").write_text(json.dumps(detail), encoding="utf-8")
    output = tmp_path / "input"
    config_hash = "a" * 64
    write_core_input_bundle(
        output, snapshot_path=snapshot_path, details_dir=detail_dir,
        scope_members=scope, market_dates=["2026-01-02", "2026-01-05"],
        config_sha256=config_hash,
    )
    return output, scope, config_hash


def _cutover(tmp_path: Path, scope: tuple[dict, ...], config_hash: str) -> tuple[Path, str]:
    payload = {
        "manifest_version": "phase2a-cutover-v1",
        "implementation_commit_sha": "a" * 40,
        "core_config_sha256": config_hash,
        "scope_name": "test-scope",
        "scope_member_count": len(scope),
        "scope_member_sha256": hash_payload([
            {"code": row["code"], "in_scope": bool(row.get("in_scope", True))}
            for row in sorted(scope, key=lambda item: item["code"])
        ]),
        "expected_market_date": "2026-01-05",
        "market_session_index": 0,
        "state_machine_version": "sm1",
        "threshold_version": "smt1",
        "identity_version": "csu1",
        "decision_rule_version": "idr1",
        "identity_epoch": "phase2a-cutover-v1",
        "automatic_closed_enabled": False,
        "approved_at": "2026-01-05T17:00:00+09:00",
    }
    path = tmp_path / "cutover.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _initialize_args(tmp_path: Path) -> list[str]:
    input_dir, scope, config_hash = _bundle(tmp_path)
    cutover, digest = _cutover(tmp_path, scope, config_hash)
    return [
        "--mode", "initialize", "--input-dir", str(input_dir),
        "--db", str(tmp_path / "state.sqlite"),
        "--output-dir", str(tmp_path / "artifacts"),
        "--seed-output", str(tmp_path / "seed"),
        "--release-output", str(tmp_path / "release"),
        "--identity-epoch", "phase2a-cutover-v1",
        "--producer-commit-sha", "b" * 40,
        "--cutover-manifest", str(cutover), "--cutover-manifest-sha256", digest,
    ]


def test_initialize_writes_immutable_release_pair(tmp_path):
    assert main(_initialize_args(tmp_path)) == 0
    files = sorted(path.name for path in (tmp_path / "release").iterdir())
    assert len(files) == 2
    assert files[0].endswith(".tar.gz")
    assert files[1].endswith(".tar.gz.manifest.json")


def test_initialize_hash_mismatch_mints_nothing(tmp_path):
    args = _initialize_args(tmp_path)
    args[-1] = "0" * 64
    with pytest.raises(Phase2ASeedError, match="SHA-256 mismatch"):
        main(args)
    assert not (tmp_path / "state.sqlite").exists()


def test_same_market_date_keeps_session_index_for_noop():
    previous = {"expected_market_date": "2026-01-05", "market_session_index": 8}
    assert next_market_session_index(previous, ["2026-01-05"], "2026-01-05") == 8


def test_release_selection_is_latest_verified_not_mutable_pointer():
    def record(date: str, index: int):
        run_id = f"smrun1:{date.replace('-', '')}:abc"
        return {
            "asset_name": f"phase2a-seed-{date}-{run_id.replace(':', '-')}.tar.gz",
            "asset_sha256": "a" * 64, "expected_market_date": date,
            "input_sha256": "c" * 64,
            "market_session_index": index, "run_id": run_id,
            "state_lineage": "live-sm1", "state_machine_version": "sm1",
            "threshold_version": "smt1", "seed_manifest": {},
            "producer_commit_sha": "b" * 40,
        }
    selected = select_latest_manifest(
        [record("2026-01-05", 0), record("2026-01-06", 1)],
        before_or_on="2026-01-06",
    )
    assert selected["expected_market_date"] == "2026-01-06"


def test_same_day_same_input_is_noop_before_database_write(tmp_path, capsys):
    args = _initialize_args(tmp_path)
    assert main(args) == 0
    capsys.readouterr()
    release_manifest = next((tmp_path / "release").glob("*.manifest.json"))
    continue_args = [
        "--mode", "continue", "--input-dir", str(tmp_path / "input"),
        "--db", str(tmp_path / "retry.sqlite"),
        "--output-dir", str(tmp_path / "retry-artifacts"),
        "--seed-output", str(tmp_path / "retry-seed"),
        "--release-output", str(tmp_path / "retry-release"),
        "--identity-epoch", "phase2a-cutover-v1",
        "--producer-commit-sha", "b" * 40,
        "--seed", str(tmp_path / "seed"),
        "--previous-release-manifest", str(release_manifest),
    ]
    assert main(continue_args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NO_OP"
    assert not (tmp_path / "retry.sqlite").exists()


def test_safe_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("../seed.sqlite")
        info.size = 1
        bundle.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(Phase2ASeedError, match="unexpected files"):
        safe_extract_seed(archive, tmp_path / "out")


def test_reviewed_cutover_manifest_matches_schema_and_implementation_commit():
    manifest = json.loads((ROOT / "docs/phase2a-cutover-manifest.json").read_text())
    schema = json.loads((ROOT / "schemas/phase2a_cutover_manifest.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert manifest["implementation_commit_sha"] == "6712cfc320adaeca928f78b6618a318761122552"
    assert manifest["automatic_closed_enabled"] is False
