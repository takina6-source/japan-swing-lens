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
from engine.controls import update_controls
from engine.data.demo import make_demo_history
from engine.data.jpx import select_scope
from engine.data.service import DataService
from engine.data.yahoo import YahooProvider
from engine.database import Database
from engine.experimental import (analyze_experimental_universe, export_experimental,
                                 seed_experimental, update_experimental_tracking)
from engine.models import SetupState
from engine.validation import export_validation, seed_validation
from engine.annual_eps import annual_eps_profile, diagnostic_row
from engine.quarterly_fundamentals import quarterly_diagnostic, quarterly_profile

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


def universe_diagnostics(diagnostic_codes: set[str], scope_codes: set[str],
                         raw_codes: set[str], prepared_codes: set[str],
                         ranked_codes: set[str]) -> dict:
    excluded = {
        "outside_current_scope": len(diagnostic_codes - scope_codes),
        "insufficient_price_history": len((diagnostic_codes & scope_codes) - raw_codes),
        "indicator_preparation_failed": len((diagnostic_codes & raw_codes) - prepared_codes),
        "analysis_or_metadata_error": len((diagnostic_codes & prepared_codes) - ranked_codes),
    }
    return {
        "diagnostic_total": len(diagnostic_codes),
        "ranked_total": len(ranked_codes),
        "excluded_total": max(0, len(diagnostic_codes) - len(ranked_codes)),
        "excluded_from_ranking": excluded,
        "definitions": {
            "diagnostic_total": "Annual EPS診断レコードがある銘柄数",
            "ranked_total": "価格履歴と指標計算を通過し、現在ランキングされた銘柄数",
        },
    }


def cached_scan(db: Database, scope: str):
    master = select_scope(db.load_securities(), scope)
    names = {row["code"]: row["name"] for row in master}
    meta = {row["code"]: row for row in master}
    yahoo = YahooProvider()
    frames = db.load_prices_many(list(names), yahoo.name)
    frames = {code: frame for code, frame in frames.items() if len(frame) >= 200}
    funds = db.load_fundamentals(list(frames))
    annual = db.load_annual_eps(list(frames))
    quarterly = db.load_quarterly_fundamentals(list(frames))
    annual_cfg = load_config()["free_data"]["annual_eps"]
    profiles = {code: annual_eps_profile(
        annual.get(code, []), int(annual_cfg["minimum_years"]),
        int(annual_cfg["preferred_years"]), float(annual_cfg["conflict_pct"]))
        for code in frames}
    cfg = load_config()
    quarterly_profiles = {code: quarterly_profile(
        quarterly.get(code, []), cfg, str(frames[code].index[-1].date())) for code in frames}
    diagnosed_quarterly = {row["code"] for row in db.load_quarterly_diagnostics(list(frames))}
    for code, profile in quarterly_profiles.items():
        if code not in diagnosed_quarterly:
            db.save_quarterly_diagnostic(quarterly_diagnostic(code, profile, []), cfg["logic_version"])
    diagnosed = {row["code"] for row in db.load_fundamental_diagnostics(list(frames))}
    for code, profile in profiles.items():
        if code not in diagnosed:
            db.save_fundamental_diagnostic(
                diagnostic_row(code, profile, profile["years_available"], []),
                cfg["logic_version"])
    fundamentals = {code: {
        "eps_growth": funds.get(code, {}).get("eps_growth"),
        "sales_growth": funds.get(code, {}).get("sales_growth"),
        "operating_profit_growth": funds.get(code, {}).get("operating_profit_growth"),
        "fundamental_source": funds.get(code, {}).get("source", "N/A"),
        "fundamental_date": funds.get(code, {}).get("filing_date"),
        "annual_eps": profiles[code]["records"],
        "annual_eps_source": profiles[code]["source_summary"],
        "annual_eps_profile": profiles[code],
        "quarterly_earnings": quarterly_profiles[code],
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
    seed_experimental(db, os.getenv("EXPERIMENTAL_SEED_URL"))
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
    update_controls(db, analyses, prepared, benchmark, meta, cfg)
    experimental = analyze_experimental_universe(
        analyses, prepared, fundamentals, meta, benchmark, cfg)
    update_experimental_tracking(db, experimental, analyses, prepared, benchmark, meta, cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    detail_dir = OUT / "details"
    detail_dir.mkdir(exist_ok=True)
    candidates = []
    annual_diagnostics = {row["code"]: row for row in
                          db.load_fundamental_diagnostics(list(prepared))}
    quarterly_diagnostic_map = {row["code"]: row for row in
                                db.load_quarterly_diagnostics(list(prepared))}
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
            "trading_value_20d": finite(item.metrics.get("trading_value_20d")),
            "trading_value": finite(item.metrics.get("trading_value")),
            "trading_value_ratio": finite(item.metrics.get("trading_value_ratio")),
            "liquidity_level": item.metrics.get("liquidity_level", "N/A"),
            "liquid": bool(item.metrics.get("liquid")),
            "price": finite(item.metrics.get("price")), "pivot": finite(pivot),
            "pivot_type": primary.pivot_type if primary else "N/A",
            "pivot_basis": primary.pivot_basis if primary else "N/A",
            "trade_plan": plan,
            "methods": {name: result.state.value for name, result in item.strategies.items()},
            "summary": summary_text(item),
            "experimental": experimental[item.code].to_dict(),
            "annual_earnings": clean(fundamentals[item.code].get("annual_eps_profile", {})),
            "data_update": {
                "annual_eps": queue_public_metadata(annual_diagnostics.get(item.code, {})),
                "quarterly_fundamentals": queue_public_metadata(
                    quarterly_diagnostic_map.get(item.code, {})),
            },
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
        "liquidity_good_threshold": cfg["liquidity"]["levels"]["good"],
        "scope": scope,
        "universe_count": len(analyses),
        "breakout_count": sum(item.state == SetupState.BREAKOUT for item in analyses),
        "watch_count": sum(item.state == SetupState.BREAKOUT_WATCH for item in analyses),
        "errors": len(errors),
        "candidates": candidates,
    }
    quarterly_diagnostics = db.load_quarterly_diagnostics(list(prepared))
    (OUT / "quarterly_diagnostics.json").write_text(
        json.dumps(clean(quarterly_diagnostics), ensure_ascii=False, separators=(",", ":"),
                   allow_nan=False), encoding="utf-8")
    csv_rows = []
    for row in quarterly_diagnostics:
        flat = {key: value for key, value in row.items()
                if key not in ("reason_codes_json", "attempted_sources_json", "details_json")}
        flat["reason_codes"] = "|".join(row.get("reason_codes", []))
        flat["attempted_sources"] = "|".join(row.get("attempted_sources", []))
        csv_rows.append(flat)
    pd.DataFrame(csv_rows).to_csv(OUT / "quarterly_diagnostics.csv", index=False,
                                  encoding="utf-8-sig")
    coverage_distribution = {str(level): 0 for level in (0, 25, 50, 75, 100)}
    source_distribution: dict[str, int] = {}
    earnings_states: dict[str, int] = {}
    for code in prepared:
        profile = fundamentals[code].get("quarterly_earnings") or {}
        level = str(int(profile.get("coverage") or 0))
        coverage_distribution[level] = coverage_distribution.get(level, 0) + 1
        source = str(profile.get("source") or "N/A")
        source_distribution[source] = source_distribution.get(source, 0) + 1
        state = experimental[code].results["EARNINGS"].state
        earnings_states[state] = earnings_states.get(state, 0) + 1
    snapshot["quarterly_fundamentals"] = {
        "records_available_stocks": sum((fundamentals[code].get("quarterly_earnings") or {}).get(
            "quarters_available", 0) > 0 for code in prepared),
        "indicator_coverage_distribution": coverage_distribution,
        "coverage_definition": "最新YoY・加速の4指標（EPS、売上、営業利益、EPS加速）の取得率",
        "source_distribution": source_distribution,
        "earnings_states": earnings_states,
        "diagnostics_files": ["data/quarterly_diagnostics.json",
                              "data/quarterly_diagnostics.csv"],
    }
    validation = export_validation(db, ROOT / "public" / "dashboard" / "validation", cfg)
    diagnostic_codes = {row["code"] for row in db.load_fundamental_diagnostics()}
    scope_codes = {row["code"] for row in select_scope(db.load_securities(), scope)}
    raw_codes, prepared_codes = set(raw), set(prepared)
    ranked_codes = {item.code for item in analyses}
    universe = universe_diagnostics(diagnostic_codes, scope_codes, raw_codes,
                                    prepared_codes, ranked_codes)
    validation["universe"] = universe
    (ROOT / "public" / "dashboard" / "validation" / "index.json").write_text(
        json.dumps(validation, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        encoding="utf-8")
    snapshot["validation"] = validation
    snapshot["data_quality"] = {
        "schema_version": "2.0",
        "annual_eps_coverage": validation["annual_eps_coverage"],
        "universe": universe,
        "fidelity_definitions": {
            "STRICT": "公式開示の標準項目で必要年数を完全取得",
            "PRACTICAL": "公式補完・Yahoo等を含む実用可能データ",
            "PROXY": "公表日等に制約がある参考データ。取得日以降のみ使用",
            "N/A": "判定に必要なデータが不足",
        },
    }
    experiment = export_experimental(
        db, ROOT / "public" / "dashboard" / "experimental", cfg)
    snapshot["experimental_validation"] = experiment
    snapshot["experimental_version"] = cfg["experimental_version"]
    snapshot["experiment_start_date"] = cfg["experiment_start_date"]
    (OUT / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"exported {len(candidates)} stocks as of {snapshot['as_of']}")


def queue_public_metadata(row: dict) -> dict:
    details = row.get("details") or {}
    return clean({
        "update_state": details.get("update_state", "UNKNOWN"),
        "queue_reason": details.get("queue_reason"),
        "next_update_rank": details.get("next_update_rank"),
        "last_attempt_at": details.get("last_attempt_at"),
        "next_eligible_at": details.get("next_eligible_at"),
        "eligible_sources": details.get("eligible_sources", []),
        "source_attempts": details.get("source_attempts", {}),
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="JPX/Yahoo/EDINETを更新してから出力")
    parser.add_argument("--scope", default="主要500+Growth")
    args = parser.parse_args()
    export(args.refresh, args.scope)
