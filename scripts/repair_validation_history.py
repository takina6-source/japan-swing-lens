#!/usr/bin/env python3
"""Repair saved forward observations without regenerating signals or controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.config import ROOT, load_config
from engine.data.yahoo import YahooProvider
from engine.database import Database
from engine.experimental.validation import export_experimental
from engine.performance_repair import (action_candidates, repair_research,
                                       repair_validation)
from engine.research.pipeline import export_research
from engine.validation import export_validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "momentum.db")
    parser.add_argument("--output-root", type=Path,
                        default=ROOT / "public" / "dashboard")
    args = parser.parse_args()
    cfg = load_config()
    db = Database(args.db)
    provider = YahooProvider()
    signals, history = db.validation_rows()
    controls, control_history = db.control_validation_rows()
    experimental, _, exp_controls, _ = db.experimental_rows()
    codes = {row["code"] for row in signals}
    codes.update(row["control_code"] for row in controls)
    codes.update(row["code"] for row in experimental)
    codes.update(row["control_code"] for row in exp_controls)
    frames = db.load_prices_many(sorted(codes), provider.name)
    suspect = action_candidates(db, frames)
    signal_code = {row["signal_id"]: row["code"] for row in signals}
    control_code = {(row["control_group_id"], row["control_code"]): row["control_code"]
                    for row in controls}
    for row in history:
        if abs(float(row.get("return_abs") or 0)) > 50:
            code = signal_code.get(row["signal_id"])
            if code:
                suspect.add(code)
    for row in control_history:
        if abs(float(row.get("return_abs") or 0)) > 50:
            code = control_code.get((row["control_group_id"], row["control_code"]))
            if code:
                suspect.add(code)
    fresh, errors = provider.histories(sorted(suspect), period="2y") if suspect else ({}, [])
    if fresh:
        db.save_prices_bulk(fresh, provider.name)
        frames.update(db.load_prices_many(sorted(fresh), provider.name))
    actions = {}
    for code in sorted(suspect):
        try:
            actions[code] = provider.stock_splits(code)
        except Exception as exc:
            errors.append(f"{code}: split actions: {exc}")
    benchmark = provider.history("TOPIX", period="2y")
    validation = repair_validation(db, frames, benchmark, actions)
    research = repair_research(db, frames, benchmark, actions)
    export_validation(db, args.output_root / "validation", cfg, validation)
    export_experimental(db, args.output_root / "experimental", cfg)
    export_research(db, args.output_root / "research", cfg,
                    {"corporate_action_diagnostics": research})
    print(json.dumps({"validation": validation, "research": research,
                      "refresh_errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
