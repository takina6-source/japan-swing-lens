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
    yahoo_fixable = [row for row in unresolved
                     if "YAHOO" not in row.get("attempted_sources", [])]
    still_unresolved = [row for row in unresolved
                        if "YAHOO" in row.get("attempted_sources", [])]
    print("集計:", {
        "total_na": total_na,
        "edinet_fixable": sum("EDINET" in str(row.get("reason_codes")) for row in unresolved),
        "jquants_fixable": sum("JQUANTS" in str(row.get("reason_codes")) for row in unresolved),
        "yahoo_fixable": len(yahoo_fixable),
        "still_unresolved": len(still_unresolved),
    })


if __name__ == "__main__":
    main()
