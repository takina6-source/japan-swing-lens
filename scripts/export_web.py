#!/usr/bin/env python3
"""Swing Lensの最新分析を、DB不要の静的Webデータへ書き出す。"""
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from engine.analyzer import analyze, prepare_universe, rank
from engine.config import ROOT, load_config
from engine.data.demo import make_demo_history
from engine.data.jpx import select_scope
from engine.data.service import DataService
from engine.data.yahoo import YahooProvider
from engine.database import Database
from engine.models import SetupState
from engine.validation import export_validation, seed_validation

OUT = ROOT / "public" / "dashboard" / "data"


def finite(value):
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def clean(value):
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def cached_scan(db: Database, scope: str):
    master = select_scope(db.load_securities(), scope)
    names = {row["code"]: row["name"] for row in master}
    meta = {row["code"]: row for row in master}
    yahoo = YahooProvider()
    frames = db.load_prices_many(list(names), yahoo.name)
    frames = {code: frame for code, frame in frames.items() if len(frame) >= 200}
    funds = db.load_fundamentals(list(frames))
    annual = db.load_annual_eps(list(frames))
    fundamentals = {code: {
        "eps_growth": funds.get(code, {}).get("eps_growth"),
        "sales_growth": funds.get(code, {}).get("sales_growth"),
        "fundamental_source": funds.get(code, {}).get("source", "N/A"),
        "fundamental_date": funds.get(code, {}).get("filing_date"),
        "annual_eps": annual.get(code, []),
        "annual_eps_source": (annual.get(code) or [{}])[-1].get("source", "N/A"),
    } for code in frames}
    sources = {code: yahoo.name for code in frames}
    return names, frames, fundamentals, sources, meta


def summary_text(item) -> str:
    strong = [name for name, result in item.strategies.items()
              if result.state in (SetupState.BREAKOUT, SetupState.BREAKOUT_WATCH, SetupState.PULLBACK)]
    cautions = []
    if not item.metrics.get("market_bullish"):
        cautions.append("市場環境が弱い")
    if not item.metrics.get("liquid"):
        cautions.append("流動性が基準未満")
    if item.strategies["Connors"].state == SetupState.EXTENDED:
        cautions.append("短期的に過熱")
    lead = f"{', '.join(strong[:3])}が形を検出" if strong else "セットアップを形成中"
    return f"{lead}。" + (f"注意点は{'・'.join(cautions)}です。" if cautions else "大きな機械判定上の警告はありません。")


def chart_rows(frame: pd.DataFrame) -> list[dict]:
    cols = [c for c in ("close", "ma20", "ma50", "ma200") if c in frame.columns]
    rows = []
    for date, row in frame.tail(180).iterrows():
        item = {"date": str(date.date())}
        item.update({col: finite(row[col]) for col in cols})
        rows.append(item)
    return rows


def export(refresh: bool, scope: str):
    cfg = load_config()
    db = Database(ROOT / "data" / "momentum.db")
    seed_validation(db, os.getenv("VALIDATION_SEED_URL"))
    service = DataService(db)
    meta = {row["code"]: row for row in db.load_securities()}
    if refresh:
        names, raw, fundamentals, sources, errors, _ = service.scan(
            "無料実用", scope=scope, edinet_key=os.getenv("EDINET_API_KEY") or None)
        meta = {row["code"]: row for row in db.load_securities()}
    else:
        names, raw, fundamentals, sources, meta = cached_scan(db, scope)
        errors = []
    if not raw:
        raise RuntimeError("公開用に分析できる保存株価がありません")
    try:
        benchmark = YahooProvider().history("TOPIX")
        benchmark_source = "1306 TOPIX連動ETF（Yahoo Finance）"
    except Exception:
        benchmark = make_demo_history("TOPIX")
        benchmark_source = "代替benchmark"
    prepared = prepare_universe(raw, benchmark)
    analyses = rank([
        analyze(code, names[code], frame, fundamentals[code], sources[code], benchmark, cfg,
                db.load_setup_registry(code))
        for code, frame in prepared.items()
    ])
    for item in analyses:
        db.save_analysis(item, cfg["logic_version"])
        db.save_signal_tracking(item, prepared[item.code], benchmark, cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    detail_dir = OUT / "details"
    detail_dir.mkdir(exist_ok=True)
    candidates = []
    for item in analyses:
        row_meta = meta.get(item.code, {})
        pivot_results = [result for name, result in item.strategies.items()
                         if name != "Connors" and result.pivot]
        primary = min(pivot_results, key=lambda result: abs(result.pivot - item.metrics["price"])) if pivot_results else None
        pivot = primary.pivot if primary else None
        plan = {k: finite(v) for k, v in asdict(item.trade_plan).items()} if item.trade_plan else {}
        candidate = {
            "code": item.code, "name": item.name,
            "market": row_meta.get("market", ""), "sector": row_meta.get("sector33", ""),
            "state": item.state.value, "confluence": item.confluence,
            "breakout_strategy_count": item.breakout_strategy_count,
            "aligned_strategy_count": item.aligned_strategy_count,
            "coverage": finite(item.coverage), "confidence": item.confidence,
            "pivot_fidelity": primary.pivot_fidelity.value if primary else item.pivot_fidelity.value,
            "consensus_pivot_fidelity": item.pivot_fidelity.value,
            "setup_id": item.setup_id,
            "momentum_percentile": finite(item.metrics.get("momentum_percentile")),
            "price": finite(item.metrics.get("price")), "pivot": finite(pivot),
            "pivot_type": primary.pivot_type if primary else "N/A",
            "pivot_basis": primary.pivot_basis if primary else "N/A",
            "trade_plan": plan,
            "methods": {name: result.state.value for name, result in item.strategies.items()},
            "summary": summary_text(item),
        }
        candidates.append(candidate)
        detail = {
            **candidate,
            "as_of": item.as_of,
            "source": item.source,
            "metrics": {key: clean(value) for key, value in item.metrics.items()
                        if key not in ("pivot_registry",)},
            "chart": chart_rows(prepared[item.code]),
            "strategies": {name: {
                "state": result.state.value,
                "summary": result.summary,
                "coverage": finite(result.coverage), "confidence": result.confidence,
                "pivot": finite(result.pivot), "stop": finite(result.stop),
                "pivot_type": result.pivot_type, "pivot_basis": result.pivot_basis,
                "pivot_fidelity": result.pivot_fidelity.value,
                "pivot_formed_date": result.pivot_formed_date,
                "setup_start_date": result.setup_start_date,
                "setup_id": result.setup_id, "setup_age": result.setup_age,
                "distance_to_pivot_pct": finite(result.distance_to_pivot_pct),
                "breakout_date": result.breakout_date, "breakout_age": result.breakout_age,
                "counts": result.counts,
                "conditions": [clean(condition.to_dict()) for condition in result.conditions],
            } for name, result in item.strategies.items()},
        }
        (detail_dir / f"{item.code}.json").write_text(
            json.dumps(detail, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    generated = datetime.now(ZoneInfo("Asia/Tokyo"))
    snapshot = {
        "generated_at": generated.isoformat(timespec="seconds"),
        "as_of": max(item.as_of for item in analyses),
        "market_regime": analyses[0].metrics["market_regime"],
        "benchmark_source": benchmark_source,
        "logic_version": cfg["logic_version"],
        "scope": scope,
        "universe_count": len(analyses),
        "breakout_count": sum(item.state == SetupState.BREAKOUT for item in analyses),
        "watch_count": sum(item.state == SetupState.BREAKOUT_WATCH for item in analyses),
        "errors": len(errors),
        "candidates": candidates,
    }
    validation = export_validation(db, ROOT / "public" / "dashboard" / "validation", cfg)
    snapshot["validation"] = validation
    (OUT / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"exported {len(candidates)} stocks as of {snapshot['as_of']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="JPX/Yahoo/EDINETを更新してから出力")
    parser.add_argument("--scope", default="主要500+Growth")
    args = parser.parse_args()
    export(args.refresh, args.scope)
