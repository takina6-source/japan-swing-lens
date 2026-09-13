#!/usr/bin/env python3
"""Publish 30 compact daily snapshots and a code-keyed What Changed report."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.simple_changes import compact_snapshot, compare_snapshots, validate_compact


ROOT = Path(__file__).parents[1]
INDEX_SCHEMA = "simple-history-index-v1"


def _read_url(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if getattr(response, "status", 200) not in (None, 200):
            raise OSError("HTTP_ERROR")
        return json.loads(response.read().decode("utf-8"))


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".simple-", delete=False) as handle:
            tmp = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()


def _load_history(base_url: str, timeout: float) -> list[dict]:
    if not base_url:
        return []
    base = base_url.rstrip("/")
    index = _read_url(f"{base}/index.json", timeout)
    if index.get("schema_version") != INDEX_SCHEMA or not isinstance(index.get("entries"), list):
        raise ValueError("INVALID_HISTORY_INDEX")
    history = []
    for entry in index["entries"]:
        filename = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(filename, str) or filename != Path(filename).name or not filename.endswith(".json"):
            continue
        try:
            history.append(validate_compact(_read_url(f"{base}/{filename}", timeout)))
        except (OSError, ValueError, TypeError, urllib.error.URLError, json.JSONDecodeError):
            continue
    return history


def export(dashboard_root: Path, history_url: str, retention: int, timeout: float) -> dict:
    root = dashboard_root.resolve()
    snapshot = json.loads((root / "data" / "snapshot.json").read_text(encoding="utf-8"))
    current = compact_snapshot(snapshot)
    history_dir = root / "snapshot-history"
    history = []
    for path in history_dir.glob("snapshot-*.json"):
        try:
            history.append(validate_compact(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    try:
        history.extend(_load_history(history_url, timeout))
    except (OSError, ValueError, TypeError, urllib.error.URLError, json.JSONDecodeError):
        pass
    by_date = {item["as_of"]: item for item in history}
    earlier = [item for item in by_date.values() if item["as_of"] < current["as_of"]]
    previous = max(earlier, key=lambda item: item["as_of"], default=None)
    report = compare_snapshots(previous, current)
    by_date[current["as_of"]] = current
    kept = sorted(by_date.values(), key=lambda item: item["as_of"], reverse=True)[:retention]

    for item in kept:
        _atomic_json(history_dir / f'snapshot-{item["as_of"]}.json', item)
    kept_names = {f'snapshot-{item["as_of"]}.json' for item in kept}
    for path in history_dir.glob("snapshot-*.json"):
        if path.name not in kept_names:
            path.unlink()
    entries = [{"as_of": item["as_of"], "file": f'snapshot-{item["as_of"]}.json',
                "sha256": item["source_hash"]} for item in kept]
    _atomic_json(history_dir / "index.json", {
        "schema_version": INDEX_SCHEMA,
        "updated_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
        "retention": retention,
        "entries": entries,
    })
    _atomic_json(root / "briefing" / "what-changed.json", report)
    text = "\n".join(report["summary_lines"] + ["", report["disclaimer"]]) + "\n"
    (root / "briefing").mkdir(parents=True, exist_ok=True)
    (root / "briefing" / "what-changed.txt").write_text(text, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-root", type=Path, default=ROOT / "public/dashboard")
    parser.add_argument("--history-url", default="")
    parser.add_argument("--retention", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()
    if args.retention < 2 or args.retention > 366 or args.timeout <= 0:
        parser.error("retention must be 2..366 and timeout must be positive")
    try:
        report = export(args.dashboard_root, args.history_url, args.retention, args.timeout)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "UNAVAILABLE", "reason": type(exc).__name__}))
        return 1
    print(json.dumps({"status": report["status"], "current_date": report["current_date"],
                      "previous_date": report["previous_date"],
                      "state_changes": len(report["state_changes"]),
                      "rank_changes": len(report["rank_changes"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
