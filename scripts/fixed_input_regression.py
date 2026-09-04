from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from engine.analyzer import analyze, prepare_universe, rank
from engine.config import ROOT, load_config
from engine.controls import update_controls
from engine.database import Database
from engine.experimental.analyzer import analyze_experimental_universe
from engine.experimental.validation import (export_experimental,
                                             update_experimental_tracking)
from engine.validation import export_validation


FIXTURE = ROOT / "tests" / "fixtures" / "fixed_input"
MANIFEST = FIXTURE / "manifest.json"
AS_OF = "2026-08-31"
CODES = [f"{1000 + number}" for number in range(24)]
ARTIFACTS = (
    "core/signals.json", "core/signal_history.json", "core/performance.json",
    "core/controls.json", "core/control_performance.json",
    "experimental/signals.json", "experimental/history.json",
    "experimental/performance.json", "experimental/controls.json",
    "experimental/control_performance.json", "ranking.json",
)


def fixed_history(seed: int, benchmark: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=AS_OF, periods=330)
    drift = (0.00045 if benchmark else
             (-0.0008 if seed % 4 == 1 else 0.0005 + (seed % 7) * 0.00014))
    close = (900 + seed * 13) * np.exp(np.cumsum(rng.normal(drift, 0.009, len(dates))))
    if not benchmark:
        close[-25:-1] = np.linspace(close[-25], close[-25] * 1.035, 24)
        if seed % 3 == 0:
            close[-1] = max(close[-2] * 1.018, close[-21:-1].max() * 1.012)
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, len(dates)))
    high = np.maximum(open_, close) * 1.008
    low = np.minimum(open_, close) * 0.992
    volume = rng.integers(180_000, 900_000, len(dates)).astype(float)
    if not benchmark and seed % 3 == 0:
        volume[-1] = volume[-20:].mean() * 2.2
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": volume}, index=dates)


def fundamentals(code: str) -> dict:
    seed = int(code)
    annual = [{"fiscal_year": str(year), "eps": float(20 + (year - 2022) * 8 + seed % 5),
               "source": "EDINET_STANDARD", "fidelity": "STRICT", "period_type": "FY",
               "published_date": f"{year + 1}-05-15"}
              for year in range(2022, 2026)]
    quarterly = []
    for year in (2025, 2026):
        for quarter, month in enumerate((3, 6, 9, 12), 1):
            period = f"{year}-{month:02d}-{31 if month in (3, 12) else 30}"
            base = 10 + quarter + seed % 4
            factor = 1.35 if year == 2026 else 1.0
            quarterly.append({"period_end": period, "fiscal_year": str(year),
                              "fiscal_quarter": f"Q{quarter}", "basic_eps": base * factor,
                              "revenue": (100 + base) * factor,
                              "operating_profit": (15 + base) * factor,
                              "net_income": (10 + base) * factor,
                              "published_date": period, "source": "JQUANTS",
                              "fidelity": "STRICT", "period_type": "QUARTER",
                              "publication_date_known": 1,
                              "eps_period_match_status": "MATCHED"})
    return {"eps_growth": 30.0, "sales_growth": 24.0,
            "operating_profit_growth": 28.0, "annual_eps": annual,
            "annual_eps_source": "EDINET_STANDARD 4期",
            "annual_eps_profile": {"records": annual, "status": "COMPLETE",
                                   "fidelity": "STRICT", "years_available": 4,
                                   "source_summary": "EDINET_STANDARD 4期",
                                   "fallback_used": False},
            "quarterly_earnings": {}, "fundamental_source": "FIXED"}


def generate(output: Path) -> dict[str, str]:
    cfg = copy.deepcopy(load_config())
    cfg["experiment_start_date"] = "2026-01-01"
    raw = {code: fixed_history(int(code)) for code in CODES}
    benchmark = fixed_history(777, benchmark=True)
    prepared = prepare_universe(raw, benchmark)
    funds = {code: fundamentals(code) for code in CODES}
    # Use the production profile builder against fixed quarterly records.
    from engine.quarterly_fundamentals import quarterly_profile
    for code in CODES:
        fixed_quarters = [] if int(code) % 4 == 1 else _quarterly_rows(funds[code])
        funds[code]["quarterly_earnings"] = quarterly_profile(
            [{**row, "code": code} for row in fixed_quarters], cfg, AS_OF)
    meta = {code: {"code": code, "name": f"固定銘柄{code}", "market": "Prime",
                   "sector33": f"固定業種{int(code) % 4}", "size_class": "TOPIX Mid400"}
            for code in CODES}
    analyses = rank([analyze(code, meta[code]["name"], prepared[code], funds[code],
                             "FIXED", benchmark, cfg) for code in CODES])
    output.mkdir(parents=True, exist_ok=True)
    ranking = [{"rank": rank_no, "code": item.code, "state": item.state.value,
                "breakout_count": item.breakout_strategy_count,
                "confluence": item.confluence, "coverage": round(item.coverage, 8),
                "momentum_percentile": round(float(item.metrics["momentum_percentile"]), 8),
                "trade_plan": asdict(item.trade_plan) if item.trade_plan else None}
               for rank_no, item in enumerate(analyses, 1)]
    _write(output / "ranking.json", ranking)
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "fixed.db")
        db.save_securities(list(meta.values()))
        for item in analyses:
            db.save_analysis(item, cfg["logic_version"])
            db.save_signal_tracking(item, prepared[item.code], benchmark, cfg)
        update_controls(db, analyses, prepared, benchmark, meta, cfg)
        experimental = analyze_experimental_universe(
            analyses, prepared, funds, meta, benchmark, cfg)
        update_experimental_tracking(db, experimental, analyses, prepared, benchmark, meta, cfg)
        export_validation(db, output / "core", cfg)
        export_experimental(db, output / "experimental", cfg)
    return {name: _hash(output / name) for name in ARTIFACTS}


def _quarterly_rows(fund: dict) -> list[dict]:
    # Recreate fixed periods independently from generated profile output.
    seed = int(fund["annual_eps"][0]["eps"])
    rows = []
    for year in (2025, 2026):
        for quarter, month in enumerate((3, 6, 9, 12), 1):
            period = f"{year}-{month:02d}-{31 if month in (3, 12) else 30}"
            base = 10 + quarter + seed % 4
            factor = 1.35 if year == 2026 else 1.0
            rows.append({"period_end": period, "fiscal_year": str(year),
                         "fiscal_quarter": f"Q{quarter}", "basic_eps": base * factor,
                         "revenue": (100 + base) * factor,
                         "operating_profit": (15 + base) * factor,
                         "net_income": (10 + base) * factor, "published_date": period,
                         "source": "JQUANTS", "fidelity": "STRICT",
                         "period_type": "QUARTER", "publication_date_known": 1,
                         "eps_period_match_status": "MATCHED"})
    return rows


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False), encoding="utf-8")


def _hash(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    normalized = _normalize(value)
    if isinstance(normalized, list) and path.name != "ranking.json":
        normalized.sort(key=lambda item: json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _normalize(value):
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()
                if key not in {"generated_at", "created_at", "updated_at", "diagnosed_at"}}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, float):
        # Pandas/NumPy patch releases can differ below the economically meaningful
        # precision. Four decimals still catches a 0.01 percentage-point change.
        return round(value, 4)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        hashes = generate(Path(temp))
    if args.update:
        FIXTURE.mkdir(parents=True, exist_ok=True)
        _write(FIXTURE / "input.json", {"as_of": AS_OF, "codes": CODES,
                                         "price_seed": "ticker-code",
                                         "control_seed": "production-deterministic"})
        _write(MANIFEST, {"schema_version": "1.0", "artifacts": hashes,
                          "numeric_precision_decimals": 4,
                          "ignored_fields": ["generated_at", "created_at",
                                             "updated_at", "diagnosed_at"]})
        print(f"updated {MANIFEST}")
        return 0
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["artifacts"]
    changed = {name: {"expected": expected.get(name), "actual": digest}
               for name, digest in hashes.items() if expected.get(name) != digest}
    if changed:
        print(json.dumps(changed, ensure_ascii=False, indent=2))
        return 1
    print(f"fixed-input regression passed: {len(hashes)} artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
