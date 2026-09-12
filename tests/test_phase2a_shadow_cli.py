from __future__ import annotations

import json

import pytest

from scripts.run_state_machine_shadow import ROOT, _ensure_private_path, main
from tests.test_phase2a_storage import core_inputs


def test_shadow_cli_dry_run_and_explicit_cutover(tmp_path, capsys):
    snapshot, details, scope = core_inputs()
    snapshot_path = tmp_path / "snapshot.json"
    scope_path = tmp_path / "scope.json"
    details_dir = tmp_path / "details"
    details_dir.mkdir()
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    scope_path.write_text(json.dumps({"members": scope}), encoding="utf-8")
    for code, detail in details.items():
        (details_dir / f"{code}.json").write_text(json.dumps(detail), encoding="utf-8")

    common = [
        "--snapshot", str(snapshot_path), "--details-dir", str(details_dir),
        "--scope-manifest", str(scope_path), "--expected-date", "2026-01-05",
        "--market-session-index", "100", "--identity-epoch", "cutover-2026-01-05",
    ]
    assert main(common + ["--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["write_performed"] is False
    assert not (tmp_path / "shadow.db").exists()

    args = common + [
        "--db", str(tmp_path / "shadow.db"),
        "--output-dir", str(tmp_path / "artifacts"), "--migrate",
        "--initialize-cutover", "--cutover-manifest-sha256", "b" * 64,
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "COMMITTED"
    assert (tmp_path / "artifacts/latest-state.json").is_file()


def test_shadow_cli_refuses_product_and_public_paths():
    with pytest.raises(ValueError, match="product database"):
        _ensure_private_path(ROOT / "data/momentum.db", database=True)
    with pytest.raises(ValueError, match="under public"):
        _ensure_private_path(ROOT / "public/phase2a-shadow")


def test_shadow_cli_rejects_unimplemented_version(capsys):
    with pytest.raises(SystemExit, match="2"):
        main([
            "--snapshot", "unused.json", "--details-dir", "unused",
            "--scope-manifest", "unused.json", "--expected-date", "2026-01-05",
            "--market-session-index", "100", "--identity-epoch", "cutover",
            "--state-machine-version", "sm2", "--dry-run",
        ])
    assert "unsupported state-machine version" in capsys.readouterr().err
