#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from engine.config import ROOT
from engine.database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="年次EPSの取得状況とN/A原因を表示")
    parser.add_argument("code", nargs="?", help="4桁の銘柄コード")
    parser.add_argument("--na-only", action="store_true", help="3期未満だけ表示")
    parser.add_argument("--json", action="store_true", help="JSONで表示")
    args = parser.parse_args()
    db = Database(ROOT / "data" / "momentum.db")
    rows = db.load_fundamental_diagnostics([args.code] if args.code else None)
    if args.na_only:
        rows = [row for row in rows if int(row.get("years_available") or 0) < 3]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return
    for row in rows:
        print(f"{row['code']} {row.get('status')} {row.get('years_available', 0)}期 "
              f"{row.get('fidelity')} {row.get('source_summary')} "
              f"原因={row.get('reason_code') or '-'}")
    total_na = sum(int(row.get("years_available") or 0) < 3 for row in rows)
    unresolved = [row for row in rows if int(row.get("years_available") or 0) < 3]
    retry_candidates = [row for row in unresolved
                        if (row.get("details") or {}).get("update_state")
                        == "QUEUED_UPDATE_LIMIT"]
    attempted_unresolved = [row for row in unresolved
                            if (row.get("details") or {}).get("source_attempts")]
    print("集計:", {
        "total_na": total_na,
        "source_retry_candidates": len(retry_candidates),
        "source_attempt_eligible": len(unresolved),
        "unresolved_after_attempts": len(attempted_unresolved),
    })


if __name__ == "__main__":
    main()
