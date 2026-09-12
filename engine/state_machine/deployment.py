"""Fail-closed deployment contracts for Phase 2A release assets."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import STATE_MACHINE_VERSION, THRESHOLD_VERSION
from .storage import Phase2ASeedError, Phase2AStore


RELEASE_TAG = "phase2a-state-v1"
LINEAGE = "live-sm1"
ASSET_RE = re.compile(
    r"^phase2a-seed-(?P<date>\d{4}-\d{2}-\d{2})-(?P<run>[a-z0-9_-]+)\.tar\.gz$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
EXPECTED_ARCHIVE_MEMBERS = {"seed.sqlite", "seed-manifest.json", "seed.sha256"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_release_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "asset_name", "asset_sha256", "expected_market_date", "market_session_index",
        "run_id", "state_lineage", "state_machine_version", "threshold_version",
        "seed_manifest", "producer_commit_sha", "input_sha256",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise Phase2ASeedError(f"release manifest missing fields: {missing}")
    match = ASSET_RE.fullmatch(str(payload["asset_name"]))
    if not match or match.group("date") != payload["expected_market_date"]:
        raise Phase2ASeedError("release asset name/date is invalid")
    if match.group("run") != str(payload["run_id"]).replace(":", "-"):
        raise Phase2ASeedError("release asset name/run id is invalid")
    if not SHA256_RE.fullmatch(str(payload["asset_sha256"])):
        raise Phase2ASeedError("release asset SHA-256 is invalid")
    if not SHA256_RE.fullmatch(str(payload["input_sha256"])):
        raise Phase2ASeedError("release input SHA-256 is invalid")
    if payload["state_lineage"] != LINEAGE:
        raise Phase2ASeedError("release lineage mismatch")
    if payload["state_machine_version"] != STATE_MACHINE_VERSION:
        raise Phase2ASeedError("release state-machine version mismatch")
    if payload["threshold_version"] != THRESHOLD_VERSION:
        raise Phase2ASeedError("release threshold version mismatch")
    if not isinstance(payload["market_session_index"], int) or payload["market_session_index"] < 0:
        raise Phase2ASeedError("release market-session index is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(payload["producer_commit_sha"])):
        raise Phase2ASeedError("producer commit SHA is invalid")
    return dict(payload)


def select_latest_manifest(
    manifests: Iterable[Mapping[str, Any]], *, before_or_on: str
) -> dict[str, Any]:
    valid = [validate_release_manifest(item) for item in manifests]
    eligible = [item for item in valid if item["expected_market_date"] <= before_or_on]
    if not eligible:
        raise Phase2ASeedError("no eligible verified Phase 2A seed manifest")
    return max(eligible, key=lambda item: (item["expected_market_date"], item["market_session_index"]))


def next_market_session_index(previous: Mapping[str, Any], market_dates: list[str], expected: str) -> int:
    prior = str(previous["expected_market_date"])
    dates = sorted(set(str(item) for item in market_dates))
    if expected == prior:
        return int(previous["market_session_index"])
    if expected not in dates or expected < prior:
        raise Phase2ASeedError("market-date sequence does not advance from the selected seed")
    advances = sum(prior < item <= expected for item in dates)
    if advances < 1:
        raise Phase2ASeedError("market-date sequence cannot establish continuity")
    return int(previous["market_session_index"]) + advances


def safe_extract_seed(archive: Path, target: Path) -> dict[str, Any]:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise Phase2ASeedError("seed archive exceeds the size limit")
    if target.exists() and any(target.iterdir()):
        raise Phase2ASeedError("seed extraction target must be empty")
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        names = {member.name for member in members}
        if names != EXPECTED_ARCHIVE_MEMBERS:
            raise Phase2ASeedError("seed archive contains unexpected files")
        for member in members:
            if not member.isfile() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                raise Phase2ASeedError("unsafe seed archive member")
        bundle.extractall(target, filter="data")
    return Phase2AStore.verify_seed(target)


def pack_seed(seed_dir: Path, archive: Path) -> str:
    Phase2AStore.verify_seed(seed_dir)
    if archive.exists():
        raise Phase2ASeedError("immutable seed archive already exists")
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
        for name in sorted(EXPECTED_ARCHIVE_MEMBERS):
            bundle.add(seed_dir / name, arcname=name, recursive=False)
    return sha256_file(archive)
