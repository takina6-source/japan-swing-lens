#!/usr/bin/env python3
"""Convert existing Core artifacts without network, database or analysis calls."""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from engine.briefing import ROOT, export_brief


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-root", type=Path, default=ROOT / "public/dashboard")
    parser.add_argument("--output", type=Path, default=ROOT / "public/dashboard/briefing")
    parser.add_argument("--config", type=Path, default=ROOT / "config/thresholds.yaml")
    parser.add_argument("--limit", type=int, default=20, help="0 = all candidates")
    parser.add_argument("--same-run-config", action="store_true",
                        help="Caller attests Core export just succeeded in this job with this config")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("limit must be >= 0")
    started = time.perf_counter()
    try:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        config = {}
    try:
        result = export_brief(args.dashboard_root, args.output, config,
                              generated_at=datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
                              limit=args.limit, same_run_config=args.same_run_config)
    except Exception:
        print(json.dumps({"status": "UNAVAILABLE", "reason": "OUTPUT_FAILED",
                          "seconds": round(time.perf_counter() - started, 3)}))
        return 1
    print(json.dumps({"status": result["status"], "candidates": result["candidate_count"],
                      "output": result["output_count"],
                      "partial_stocks": sum(r["status"] == "PARTIAL" for r in result["stocks"]),
                      "issue_count": len(result["issues"]) + sum(len(r["issues"]) for r in result["stocks"]),
                      "reasons": sorted({i["reason"] for i in result["issues"]}),
                      "seconds": round(time.perf_counter() - started, 3)}))
    return 1 if result["status"] == "UNAVAILABLE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
