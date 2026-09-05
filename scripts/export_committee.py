#!/usr/bin/env python3
"""生成済みSwing Lens Web成果物をInvestment Committee形式へ変換する。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from engine.committee_export import export_committee
from engine.config import ROOT, load_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard-root", type=Path,
                        default=ROOT / "public" / "dashboard")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "public" / "dashboard" / "committee")
    parser.add_argument("--history-seed-url",
                        default=os.getenv("COMMITTEE_SEED_URL"))
    parser.add_argument("--history-limit", type=int, default=260)
    args = parser.parse_args()
    manifest = export_committee(
        args.dashboard_root, args.output,
        ROOT / "schemas" / "investment_committee_engine.schema.json",
        config=load_config(), history_seed_url=args.history_seed_url,
        history_limit=args.history_limit)
    print(f"exported {manifest['count']} Committee records "
          f"as of {manifest['evaluation_date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
