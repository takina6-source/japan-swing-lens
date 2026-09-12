"""Create the private, same-run Core input contract consumed by Phase 2A."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ids import canonical_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_core_input_bundle(
    output: Path,
    *,
    snapshot_path: Path,
    details_dir: Path,
    scope_members: Sequence[Mapping[str, Any]],
    market_dates: Sequence[str],
    config_sha256: str,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError(f"Phase 2A input output already exists: {output}")
    output.mkdir(parents=True)
    shutil.copyfile(snapshot_path, output / "snapshot.json")
    shutil.copytree(details_dir, output / "details")
    (output / "scope.json").write_text(
        canonical_json({"members": list(scope_members)}) + "\n", encoding="utf-8"
    )
    dates = sorted(set(str(item) for item in market_dates))
    (output / "market-dates.json").write_text(canonical_json(dates) + "\n", encoding="utf-8")
    files = [output / "snapshot.json", output / "scope.json", output / "market-dates.json"]
    files.extend(sorted((output / "details").glob("*.json")))
    manifest = {
        "contract_version": "phase2a-core-input-v1",
        "config_sha256": config_sha256,
        "files": {str(path.relative_to(output)): _sha256(path) for path in files},
    }
    (output / "input-manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    return manifest
