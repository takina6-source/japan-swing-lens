"""Investment Committee向けの疎結合な静的JSON Adapter。

このモジュールは生成済みのWeb snapshot/detailだけを入力に使う。
分析・ランキング関数を呼ばないため、Committee連携が既存判定を変えない。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import urlopen
from zoneinfo import ZoneInfo


ENGINE = "japan_swing_lens"
SCHEMA_VERSION = "1.0"
DEFAULT_HISTORY_LIMIT = 260
_TICKER = re.compile(r"^[0-9A-Z]{4,6}$")


def normalize_ticker(value: Any) -> str:
    """JPXコードをCommittee共通キーへ正規化する（Yahooの.Tは除去）。"""
    ticker = str(value or "").strip().upper()
    if ticker.endswith(".T"):
        ticker = ticker[:-2]
    if not _TICKER.fullmatch(ticker):
        raise ValueError(f"unsupported Japanese ticker: {value!r}")
    return ticker


def detect_git_commit() -> str | None:
    value = os.getenv("GITHUB_SHA") or os.getenv("GIT_COMMIT")
    if value:
        return value.strip() or None
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
            text=True, timeout=3).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def export_committee(
    dashboard_root: Path,
    output_dir: Path,
    schema_path: Path,
    *,
    config: dict[str, Any] | None = None,
    history_seed_url: str | None = None,
    generated_at: str | None = None,
    git_commit: str | None = None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> dict[str, Any]:
    """既存Web成果物を読み、Committee成果物だけを置き換える。"""
    dashboard_root = Path(dashboard_root)
    output_dir = Path(output_dir)
    snapshot_path = dashboard_root / "data" / "snapshot.json"
    snapshot = _read_json(snapshot_path)
    candidates = snapshot.get("candidates") or []
    if not candidates:
        raise ValueError("snapshot has no candidates")

    evaluation_date = str(snapshot.get("as_of") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", evaluation_date):
        raise ValueError("snapshot.as_of must be YYYY-MM-DD")
    generated_at = generated_at or datetime.now(
        ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    engine_version = str(snapshot.get("logic_version") or "not_available")
    config = config or {}
    versions = {
        "engine_version": engine_version,
        "logic_version": snapshot.get("logic_version"),
        "config_version": config.get("threshold_version"),
        "strategy_version": config.get("strategy_version"),
        "git_commit": git_commit if git_commit is not None else detect_git_commit(),
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="committee-export-", dir=output_dir.parent))
    try:
        latest = staging / "latest"
        latest.mkdir(parents=True)
        documents: list[dict[str, Any]] = []
        ranking_rows: list[dict[str, Any]] = []
        for rank_number, candidate in enumerate(candidates, 1):
            ticker = normalize_ticker(candidate.get("code"))
            detail_path = dashboard_root / "data" / "details" / f"{ticker}.json"
            detail = _read_json(detail_path) if detail_path.exists() else candidate
            document = build_committee_document(
                detail, rank_number=rank_number, evaluation_date=evaluation_date,
                generated_at=generated_at, versions=versions)
            _write_json(latest / f"{ticker}.json", document)
            documents.append(document)
            ranking_rows.append({
                "rank": rank_number,
                "ticker": ticker,
                "company_name": document["company_name"],
                "evaluation_date": evaluation_date,
                "verdict": document["verdict"],
                "confidence": document["confidence"],
                "core_verdict": document["core"]["verdict"],
                "experimental_alignment": document["experimental"]["alignment"],
                "path": f"latest/{ticker}.json",
            })

        ranking = {
            "schema_version": SCHEMA_VERSION,
            "engine": ENGINE,
            **versions,
            "evaluation_date": evaluation_date,
            "generated_at": generated_at,
            "count": len(ranking_rows),
            "ranking": ranking_rows,
        }
        _write_json(staging / "ranking.json", ranking)

        history = _load_history(history_seed_url)
        compact = [{
            "rank": row["rank"], "ticker": row["ticker"],
            "verdict": row["verdict"], "core_verdict": row["core_verdict"],
            "experimental_alignment": row["experimental_alignment"],
        } for row in ranking_rows]
        snapshots = [row for row in history.get("snapshots", [])
                     if row.get("evaluation_date") != evaluation_date]
        snapshots.append({
            "evaluation_date": evaluation_date,
            "generated_at": generated_at,
            "engine_version": engine_version,
            "git_commit": versions["git_commit"],
            "count": len(compact),
            "ranking": compact,
        })
        snapshots = sorted(snapshots, key=lambda row: row["evaluation_date"])[-history_limit:]
        history_index = {
            "schema_version": SCHEMA_VERSION,
            "engine": ENGINE,
            "retention_trading_days": history_limit,
            "snapshots": snapshots,
        }
        _write_json(staging / "history" / "index.json", history_index)
        for history_row in snapshots:
            _write_json(
                staging / "history" / history_row["evaluation_date"] / "ranking.json",
                {key: value for key, value in history_row.items() if key != "ranking"}
                | {"schema_version": SCHEMA_VERSION, "engine": ENGINE,
                   "ranking": history_row["ranking"]})

        schema = _read_json(schema_path)
        _write_json(staging / "schema.json", schema)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "engine": ENGINE,
            **versions,
            "evaluation_date": evaluation_date,
            "generated_at": generated_at,
            "count": len(documents),
            "ticker_format": "JPX code string without .T",
            "schema_path": "schema.json",
            "ranking_path": "ranking.json",
            "history_index_path": "history/index.json",
            "files": [{"ticker": row["ticker"], "path": row["path"]}
                      for row in ranking_rows],
            "tickers": [row["ticker"] for row in ranking_rows],
        }
        _write_json(staging / "manifest.json", manifest)

        backup = output_dir.with_name(f"{output_dir.name}.previous")
        if backup.exists():
            shutil.rmtree(backup)
        if output_dir.exists():
            output_dir.rename(backup)
        staging.rename(output_dir)
        if backup.exists():
            shutil.rmtree(backup)
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def build_committee_document(
    detail: dict[str, Any], *, rank_number: int, evaluation_date: str,
    generated_at: str, versions: dict[str, Any],
) -> dict[str, Any]:
    ticker = normalize_ticker(detail.get("code"))
    methods = detail.get("methods") or {}
    strategies = detail.get("strategies") or {}
    core_signals = {
        _signal_key(name): {
            "status": status,
            "value": {
                "coverage": strategies.get(name, {}).get("coverage"),
                "confidence": strategies.get(name, {}).get("confidence"),
                "pivot": strategies.get(name, {}).get("pivot"),
            },
            "reason": strategies.get(name, {}).get("summary"),
        }
        for name, status in methods.items()
    }
    experimental_source = detail.get("experimental") or {}
    experimental_signals = {
        _signal_key(name): {
            "status": result.get("state"),
            "positive": result.get("positive"),
            "value": _select_metrics(result.get("metrics") or {}, {
                "breakout_price", "atr_pct", "eps_growth", "sales_growth",
                "operating_profit_growth", "eps_acceleration", "sector_rank",
                "sector_percentile", "stock_relative_strength"}),
            "reason": None,
            "coverage": result.get("coverage"),
            "fidelity": result.get("fidelity"),
        }
        for name, result in (experimental_source.get("results") or {}).items()
    }
    annual = detail.get("annual_earnings") or {}
    quarterly = (detail.get("metrics") or {}).get("quarterly_earnings") or {}
    source_status = {
        "price": {"status": "ok" if detail.get("price") is not None else "missing",
                  "source": detail.get("source")},
        "fundamentals": {
            "status": _annual_status(annual.get("status")),
            "source": annual.get("source_summary"),
            "fidelity": annual.get("fidelity"),
            "reason_code": annual.get("reason_code"),
        },
        "earnings": {
            "status": _coverage_status(quarterly.get("coverage")),
            "source": quarterly.get("source"),
            "fidelity": quarterly.get("fidelity"),
            "coverage": quarterly.get("coverage"),
            "reason_codes": quarterly.get("reason_codes") or quarterly.get("missing") or [],
        },
        "update_diagnostics": detail.get("data_update") or {},
    }
    metrics_source = detail.get("metrics") or {}
    metrics = _select_metrics(metrics_source, {
        "price", "momentum_1m", "momentum_3m", "momentum_6m", "momentum_12m",
        "momentum_percentile", "benchmark_rs_6m", "rsi2", "trading_value",
        "trading_value_20d", "trading_value_ratio", "liquidity_level",
        "eps_growth", "sales_growth", "operating_profit_growth", "market_regime",
    })
    metrics.update({
        "pivot": detail.get("pivot"),
        "pivot_type": detail.get("pivot_type"),
        "trade_plan": detail.get("trade_plan") or {},
        "annual_earnings": {
            "status": annual.get("status"), "years_available": annual.get("years_available"),
            "fidelity": annual.get("fidelity"), "source": annual.get("source_summary"),
        },
        "quarterly_earnings": _select_metrics(quarterly, {
            "eps_growth", "previous_eps_growth", "sales_growth",
            "operating_profit_growth", "eps_acceleration", "sales_acceleration",
            "coverage", "fidelity", "latest_period", "published_date",
            "eps_period_match_status", "eps_continuity_warning",
        }),
    })
    core = {
        "verdict": detail.get("state") or "not_available",
        "rank": rank_number,
        "consensus": {
            "label": detail.get("state") or "not_available",
            "confluence": detail.get("confluence"),
            "breakout_strategy_count": detail.get("breakout_strategy_count"),
            "aligned_strategy_count": detail.get("aligned_strategy_count"),
            "coverage": detail.get("coverage"),
            "confidence": detail.get("confidence"),
            "pivot_fidelity": detail.get("consensus_pivot_fidelity"),
        },
        "signals": core_signals,
    }
    experimental = {
        "alignment": experimental_source.get("alignment"),
        "combination": experimental_source.get("combination"),
        "affects_core_ranking": False,
        "signals": experimental_signals,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": ENGINE,
        **versions,
        "ticker": ticker,
        "provider_ticker": f"{ticker}.T",
        "company_name": detail.get("name") or "",
        "evaluation_date": evaluation_date,
        "generated_at": generated_at,
        "verdict": core["verdict"],
        "confidence": detail.get("confidence"),
        "risk_level": None,
        "core": core,
        "experimental": experimental,
        "signals": {
            "core": core_signals,
            "liquidity": {
                "status": detail.get("liquidity_level") or "not_available",
                "value": {"liquid": detail.get("liquid"),
                          "trading_value_20d": detail.get("trading_value_20d")},
                "reason": None,
            },
            "experimental": experimental_signals,
        },
        "metrics": metrics,
        "source_status": source_status,
        "validation_reference": {
            "join_keys": {"ticker": ticker, "evaluation_date": evaluation_date,
                          "engine": ENGINE, "engine_version": versions["engine_version"]},
            "core_history_path": "validation/signal_history.json",
            "experimental_history_path": "experimental/history.json",
        },
    }


def _signal_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _select_metrics(source: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {key: source.get(key) for key in sorted(allowed) if key in source}


def _annual_status(status: Any) -> str:
    return {"COMPLETE": "ok", "PARTIAL": "partial",
            "INSUFFICIENT": "missing", "FAILED": "missing"}.get(str(status), "missing")


def _coverage_status(value: Any) -> str:
    try:
        coverage = float(value)
    except (TypeError, ValueError):
        return "missing"
    return "ok" if coverage >= 100 else "partial" if coverage > 0 else "missing"


def _load_history(seed_url: str | None) -> dict[str, Any]:
    if not seed_url:
        return {"snapshots": []}
    try:
        url = urljoin(seed_url.rstrip("/") + "/", "history/index.json")
        with urlopen(url, timeout=10) as response:  # nosec B310: URL is operator supplied
            value = json.load(response)
        return value if value.get("engine") == ENGINE else {"snapshots": []}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"snapshots": []}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                               allow_nan=False), encoding="utf-8")
