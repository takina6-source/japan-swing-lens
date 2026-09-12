#!/usr/bin/env python3
"""Select and verify immutable assets from the single Phase 2A Release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.state_machine.deployment import (
    select_latest_manifest,
    sha256_file,
    safe_extract_seed,
    validate_release_manifest,
)
from engine.state_machine.storage import Phase2ASeedError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    select = actions.add_parser("select")
    select.add_argument("--manifests-dir", type=Path, required=True)
    select.add_argument("--before-or-on", required=True)
    select.add_argument("--github-output", type=Path)
    extract = actions.add_parser("extract")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--manifest", type=Path, required=True)
    extract.add_argument("--target", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.action == "select":
        records = []
        paths: dict[str, Path] = {}
        for path in sorted(args.manifests_dir.glob("*.manifest.json")):
            payload = validate_release_manifest(json.loads(path.read_text(encoding="utf-8")))
            records.append(payload)
            paths[payload["asset_name"]] = path
        selected = select_latest_manifest(records, before_or_on=args.before_or_on)
        result = {
            "asset_name": selected["asset_name"],
            "manifest_path": str(paths[selected["asset_name"]]),
        }
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as handle:
                for key, value in result.items():
                    handle.write(f"{key}={value}\n")
        print(json.dumps(result, sort_keys=True))
        return 0

    manifest = validate_release_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    if args.archive.name != manifest["asset_name"]:
        raise Phase2ASeedError("downloaded archive name does not match its manifest")
    if sha256_file(args.archive) != manifest["asset_sha256"]:
        raise Phase2ASeedError("downloaded archive hash mismatch")
    seed_manifest = safe_extract_seed(args.archive, args.target)
    if seed_manifest != manifest["seed_manifest"]:
        raise Phase2ASeedError("extracted seed metadata does not match release manifest")
    print(json.dumps({"status": "VERIFIED", "asset_name": args.archive.name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
