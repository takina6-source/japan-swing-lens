from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .database import Database
from .liquidity import liquidity_level

STRATEGIES = ("Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas", "Connors")
TREND_STRATEGIES = STRATEGIES[:-1]
SLUG = {"Minervini": "minervini", "Qullamaggie": "qullamaggie",
        "CAN SLIM": "can_slim", "Weinstein": "weinstein",
        "Darvas": "darvas", "Connors": "connors"}


def seed_validation(db: Database, base_url: str | None) -> bool:
    if not base_url:
        return False
    try:
        response = requests.get(base_url.rstrip("/") + "/state.json", timeout=30)
        if response.status_code != 200:
            return False
        payload = response.json()
        db.import_validation_rows(payload.get("signals", []), payload.get("history", []),
                                  payload.get("controls", []),
                                  payload.get("control_history", []))
        return True
    except Exception:
        return False


def export_validation(db: Database, output: Path, cfg: dict) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    signals_raw, history_raw = db.validation_rows()
    controls_raw, control_history_raw = db.control_validation_rows()
    signals = [_signal_row(row, cfg) for row in signals_raw]
    history = [_history_row(row) for row in history_raw]
    controls = [_control_row(row, cfg) for row in controls_raw]
    control_performance = _control_performance_rows(
        controls, control_history_raw, cfg["tracking"]["horizons"])
    performance = _performance_rows(
        signals, history, control_performance, cfg["tracking"]["horizons"])
    summary_rows = _summary_rows(performance, cfg)
    diagnostic_raw = db.load_fundamental_diagnostics()
    names = {row["code"]: row.get("name", "") for row in db.load_securities()}
    diagnostics = []
    for row in diagnostic_raw:
        details = row.get("details") or {}
        diagnostics.append({
            "code": row["code"], "name": names.get(row["code"], ""),
            "status": row.get("status"), "fidelity": row.get("fidelity"),
            "years_available": row.get("years_available"),
            "initial_years": row.get("initial_years"),
            "source_summary": row.get("source_summary"),
            "fallback_used": row.get("fallback_used"),
            "reason_code": row.get("reason_code"),
            "reason_codes": ",".join(row.get("reason_codes", [])),
            "attempted_sources": ",".join(row.get("attempted_sources", [])),
            "update_state": details.get("update_state", "UNKNOWN"),
            "next_update_rank": details.get("next_update_rank"),
            "source_attempts": details.get("source_attempts", {}),
            "selected_years": details.get("selected_years", []),
            "diagnosed_at": row.get("diagnosed_at"),
            "logic_version": row.get("logic_version"),
        })
    diagnostics_csv = [{**row,
                        "source_attempts": json.dumps(row["source_attempts"], ensure_ascii=False,
                                                      separators=(",", ":")),
                        "selected_years": json.dumps(row["selected_years"], ensure_ascii=False,
                                                     separators=(",", ":"))}
                       for row in diagnostics]
    _write_csv(output / "fundamental_diagnostics.csv", diagnostics_csv)
    _write_json(output / "fundamental_diagnostics.json", diagnostics)
    for name, rows in (("signals", signals), ("signal_history", history),
                       ("performance", performance), ("controls", controls),
                       ("control_performance", control_performance)):
        _write_csv(output / f"{name}.csv", rows)
        _write_json(output / f"{name}.json", rows)
    _write_csv(output / "summary.csv", summary_rows)
    generated = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    _write_json(output / "summary.json", {
        "generated_at": generated,
        "schema_version": cfg["export_schema_version"],
        "rows": summary_rows,
        "notes": [
            "Versionの異なるSignalは同じ集計行へ混在させない",
            "INSUFFICIENT_SAMPLEは有効性を断定できない",
            "Validation結果による閾値の自動最適化は行わない",
        ],
    })
    _write_json(output / "state.json", {
        "signals": signals_raw, "history": history_raw,
        "controls": controls_raw, "control_history": control_history_raw,
    })
    dates = [row["signal_date"] for row in signals if row.get("signal_date")]
    observation_dates = [row["date"] for row in history if row.get("date")]
    available = [
        "signals.csv", "signal_history.csv", "performance.csv",
        "signals.json", "signal_history.json", "performance.json",
        "controls.csv", "controls.json", "control_performance.csv",
        "control_performance.json", "summary.csv", "summary.json",
        "fundamental_diagnostics.csv", "fundamental_diagnostics.json",
    ]
    coverage = annual_eps_coverage_summary(diagnostic_raw, cfg)
    index = {
        "generated_at": generated,
        "schema_version": cfg["export_schema_version"],
        "logic_version": cfg["logic_version"],
        "strategy_version": cfg["strategy_version"],
        "threshold_version": cfg["threshold_version"],
        "signal_count": len(signals),
        "history_count": len(history),
        "performance_count": len(performance),
        "control_count": len(controls),
        "control_group_count": len({row["control_group_id"] for row in controls}),
        "control_performance_count": len(control_performance),
        "summary_count": len(summary_rows),
        "fundamental_diagnostics_count": len(diagnostics),
        "diagnostic_schema_version": "2.0",
        "annual_eps_coverage": coverage,
        "observation_start": min(dates + observation_dates) if dates or observation_dates else None,
        "observation_end": max(dates + observation_dates) if dates or observation_dates else None,
        "available_files": available,
        "notes": [
            "return/mfe/mae/excess returnの単位はpercent",
            "signal_idとcontrol_group_idでSignal・Controlを結合可能",
            "Randomは固定seed、MatchedはSignal日時点の非Signal銘柄から固定",
            "Market baselineは1306 ETF代理系列を継続使用",
            "既存のSignal SnapshotとControl membershipは後日上書きしない",
        ],
    }
    _write_json(output / "index.json", index)
    return index


def annual_eps_coverage_summary(rows: list[dict], cfg: dict) -> dict:
    minimum = int(cfg["free_data"]["annual_eps"]["minimum_years"])
    preferred = int(cfg["free_data"]["annual_eps"]["preferred_years"])
    years = [int(row.get("years_available") or 0) for row in rows]
    source_stock_counts: Counter[str] = Counter()
    source_attempt_status: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for part in str(row.get("source_summary") or "").split(" + "):
            if part and part != "N/A":
                source_stock_counts[part.split()[0]] += 1
        details = row.get("details") or {}
        statuses = dict(details.get("source_attempts") or {})
        attempted = set(row.get("attempted_sources") or [])
        for source in ("EDINET", "JQUANTS", "YAHOO"):
            statuses.setdefault(source, "UNKNOWN_LEGACY" if source in attempted else "NOT_ATTEMPTED")
        for source, status in statuses.items():
            source_attempt_status[str(source)][str(status)] += 1
    status_breakdown = Counter(str(row.get("status") or "UNKNOWN") for row in rows)
    fidelity_breakdown = Counter(str(row.get("fidelity") or "N/A") for row in rows)
    unresolved = [row for row, count in zip(rows, years) if count < minimum]
    def statuses_for(row):
        details = row.get("details") or {}
        values = dict(details.get("source_attempts") or {})
        attempted = set(row.get("attempted_sources") or [])
        for source in ("EDINET", "JQUANTS", "YAHOO"):
            values.setdefault(source, "UNKNOWN_LEGACY" if source in attempted else "NOT_ATTEMPTED")
        return values
    retry_candidates = [row for row in unresolved
                        if (row.get("details") or {}).get("update_state")
                        == "QUEUED_UPDATE_LIMIT"
                        or "NOT_ATTEMPTED" in statuses_for(row).values()]
    attempted_unresolved = [row for row in unresolved
                            if any(status not in {"NOT_ATTEMPTED", "JQUANTS_NOT_CONFIGURED"}
                                   for status in statuses_for(row).values())]
    return {
        "total": len(rows),
        "complete_4y": sum(value >= preferred for value in years),
        "usable_3y_plus": sum(value >= minimum for value in years),
        "partial_3y": sum(minimum <= value < preferred for value in years),
        "insufficient_under_3y": sum(value < minimum for value in years),
        "status_breakdown": dict(status_breakdown),
        "source_stock_counts": dict(source_stock_counts),
        "fidelity_breakdown": dict(fidelity_breakdown),
        "source_attempt_status": {key: dict(value)
                                  for key, value in source_attempt_status.items()},
        "source_retry_candidates": len(retry_candidates),
        "source_attempt_eligible": len(unresolved),
        "unresolved_after_attempts": len(attempted_unresolved),
        "fallback_used": sum(bool(row.get("fallback_used")) for row in rows),
        "initial_usable_3y_plus": sum(int(row.get("initial_years") or 0) >= minimum
                                      for row in rows),
    }


def _signal_row(row: dict, cfg: dict) -> dict:
    out = {key: value for key, value in row.items()
           if key not in ("strategy_states_json", "strategy_pivots_json",
                          "trade_plan_json", "experimental_states_json", "created_at")}
    states = _loads(row.get("strategy_states_json"))
    pivots = _loads(row.get("strategy_pivots_json"))
    plan = _loads(row.get("trade_plan_json"))
    experimental = _loads(row.get("experimental_states_json"))
    for strategy in STRATEGIES:
        slug = SLUG[strategy]
        pivot = pivots.get(strategy, {})
        out[f"{slug}_state"] = states.get(strategy)
        out[f"{slug}_pivot"] = pivot.get("price")
        out[f"{slug}_pivot_type"] = pivot.get("type")
        out[f"{slug}_pivot_basis"] = pivot.get("basis")
        out[f"{slug}_pivot_fidelity"] = pivot.get("fidelity")
    for key in ("entry_low", "entry_high", "stop", "target_1r", "target_2r"):
        out[key] = plan.get(key)
    out["strategy_combination"] = "+".join(
        strategy for strategy in TREND_STRATEGIES if states.get(strategy) == "BREAKOUT") or "NONE"
    out["momentum_bucket"] = _momentum_bucket(row.get("momentum_percentile"))
    trading_value_20d = row.get("trading_value_20d")
    if trading_value_20d is None:
        trading_value_20d = row.get("trading_value")
    out["trading_value_20d"] = trading_value_20d
    out["liquidity_level"] = (
        row.get("liquidity_level") or liquidity_level(trading_value_20d, cfg))
    out["liquid"] = (row.get("liquid") if row.get("liquid") is not None
                     else int(float(trading_value_20d or 0) >=
                              float(cfg["liquidity"]["minimum_trading_value_yen"])))
    out["export_schema_version"] = cfg["export_schema_version"]
    out["experimental_turtle_state"] = experimental.get("TURTLE")
    out["experimental_earnings_state"] = experimental.get("EARNINGS")
    out["experimental_sector_state"] = experimental.get("SECTOR_RS")
    return out


def _history_row(row: dict) -> dict:
    out = {key: value for key, value in row.items()
           if key not in ("strategy_states_json", "created_at")}
    states = _loads(row.get("strategy_states_json"))
    for strategy in STRATEGIES:
        out[f"{SLUG[strategy]}_state"] = states.get(strategy)
    return out


def _control_row(row: dict, cfg: dict) -> dict:
    out = {key: value for key, value in row.items() if key != "created_at"}
    out["export_schema_version"] = cfg["export_schema_version"]
    return out


def _control_performance_rows(controls: list[dict], history: list[dict], horizons) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in history:
        grouped[(row["control_group_id"], row["control_code"])].append(row)
    rows = []
    for member in controls:
        key = (member["control_group_id"], member["control_code"])
        observations = {int(row["session_offset"]): row for row in grouped.get(key, [])}
        out = {column: member.get(column) for column in (
            "control_group_id", "signal_id", "signal_date", "control_code", "control_name",
            "control_type", "control_rank", "match_score", "initial_close",
            "selection_version", "app_version", "strategy_version",
            "threshold_version", "schema_version", "export_schema_version")}
        for horizon in horizons:
            obs = observations.get(int(horizon))
            suffix = f"{horizon}d"
            out[f"return_{suffix}_pct"] = obs.get("return_abs") if obs else None
            out[f"benchmark_relative_{suffix}_pct"] = (
                obs.get("benchmark_relative_return") if obs else None)
        latest = max(observations.values(), key=lambda row: int(row["session_offset"]), default={})
        out["latest_session_offset"] = latest.get("session_offset")
        out["mfe_to_date_pct"] = latest.get("mfe")
        out["mae_to_date_pct"] = latest.get("mae")
        rows.append(out)
    return rows


def _performance_rows(signals: list[dict], history: list[dict],
                      control_performance: list[dict], horizons) -> list[dict]:
    grouped_history: dict[str, list[dict]] = defaultdict(list)
    for row in history:
        grouped_history[row["signal_id"]].append(row)
    controls: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in control_performance:
        controls[(row["signal_id"], row["control_type"])].append(row)
    rows = []
    for signal in signals:
        out = {
            "signal_id": signal["signal_id"], "signal_date": signal["signal_date"],
            "ticker": signal["code"], "stock_name": signal["stock_name"],
            "initial_consensus_state": signal["consensus_state"],
            "initial_breakout_count": signal["breakout_count"],
            "initial_aligned_count": signal["aligned_count"],
            "strategy_combination": signal.get("strategy_combination"),
            "initial_coverage": signal["coverage"],
            "initial_confidence": signal["confidence"],
            "pivot_fidelity": signal["pivot_fidelity"],
            "momentum_percentile": signal["momentum_percentile"],
            "momentum_bucket": signal.get("momentum_bucket"),
            "trading_value": signal.get("trading_value"),
            "initial_trading_value_20d": signal.get("trading_value_20d"),
            "initial_liquidity_level": signal.get("liquidity_level"),
            "initial_trading_value": signal.get("current_trading_value"),
            "initial_trading_value_ratio": signal.get("trading_value_ratio"),
            "liquidity_level": signal.get("liquidity_level"),
            "market_regime": signal["market_regime"],
            "app_version": signal.get("app_version"),
            "strategy_version": signal.get("strategy_version"),
            "threshold_version": signal.get("threshold_version"),
            "schema_version": signal.get("schema_version"),
            "experimental_version": signal.get("experimental_version"),
            "experimental_alignment": signal.get("experimental_alignment"),
            "experimental_combination": signal.get("experimental_combination"),
            "export_schema_version": signal.get("export_schema_version"),
        }
        observations = {int(row["session_offset"]): row
                        for row in grouped_history.get(signal["signal_id"], [])}
        for horizon in horizons:
            obs = observations.get(int(horizon))
            suffix = f"{horizon}d"
            signal_return = obs.get("return_abs") if obs else None
            market_excess = obs.get("benchmark_relative_return") if obs else None
            out[f"return_{suffix}_pct"] = signal_return
            out[f"market_return_{suffix}_pct"] = (
                signal_return - market_excess
                if signal_return is not None and market_excess is not None else None)
            out[f"benchmark_relative_{suffix}_pct"] = market_excess
            out[f"excess_vs_market_{suffix}_pct"] = market_excess
            out[f"consensus_state_{suffix}"] = obs.get("consensus_state") if obs else None
            out[f"breakout_count_{suffix}"] = obs.get("breakout_count") if obs else None
            out[f"liquidity_level_{suffix}"] = obs.get("liquidity_level") if obs else None
            out[f"trading_value_{suffix}"] = obs.get("trading_value") if obs else None
            out[f"trading_value_ratio_{suffix}"] = (
                obs.get("trading_value_ratio") if obs else None)
            for control_type in ("RANDOM", "MATCHED"):
                prefix = control_type.lower()
                values = [row.get(f"return_{suffix}_pct")
                          for row in controls.get((signal["signal_id"], control_type), [])]
                values = [float(value) for value in values if value is not None]
                baseline_mean = statistics.fmean(values) if values else None
                baseline_median = statistics.median(values) if values else None
                out[f"{prefix}_sample_count_{suffix}"] = len(values)
                out[f"{prefix}_return_mean_{suffix}_pct"] = baseline_mean
                out[f"{prefix}_return_median_{suffix}_pct"] = baseline_median
                out[f"excess_vs_{prefix}_{suffix}_pct"] = (
                    signal_return - baseline_mean
                    if signal_return is not None and baseline_mean is not None else None)
                out[f"excess_vs_{prefix}_median_{suffix}_pct"] = (
                    signal_return - baseline_median
                    if signal_return is not None and baseline_median is not None else None)
        latest = max(observations.values(), key=lambda row: int(row["session_offset"]), default={})
        out["mfe_to_date_pct"] = latest.get("mfe")
        out["mae_to_date_pct"] = latest.get("mae")
        out["failed_breakout"] = latest.get("failed_breakout")
        out["hit_1r"] = latest.get("hit_1r")
        out["hit_2r"] = latest.get("hit_2r")
        out["hit_stop"] = latest.get("hit_stop")
        rows.append(out)
    return rows


def _summary_rows(performance: list[dict], cfg: dict) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    combination_min = int(cfg["summary"]["minimum_combination_sample"])
    for row in performance:
        version = (row.get("app_version"), row.get("strategy_version"),
                   row.get("threshold_version"), row.get("schema_version"))
        dimensions = [
            ("ALL", "ALL"),
            ("CONSENSUS_STATE", row.get("initial_consensus_state")),
            ("BREAKOUT_COUNT", str(row.get("initial_breakout_count"))),
            ("STRATEGY_COMBINATION", row.get("strategy_combination")),
            ("MOMENTUM_BUCKET", row.get("momentum_bucket")),
            ("LIQUIDITY", row.get("liquidity_level")),
            ("PIVOT_FIDELITY", row.get("pivot_fidelity")),
            ("MARKET_REGIME", row.get("market_regime")),
            ("EXPERIMENTAL_ALIGNMENT", row.get("experimental_alignment")),
            ("EXPERIMENTAL_COMBINATION", row.get("experimental_combination")),
        ]
        for horizon in cfg["tracking"]["horizons"]:
            if row.get(f"return_{horizon}d_pct") is None:
                continue
            for dimension, value in dimensions:
                if value not in (None, ""):
                    groups[(*version, dimension, str(value), int(horizon))].append(row)
    rows = []
    for key, items in sorted(groups.items(), key=lambda item: tuple(str(value) for value in item[0])):
        app_version, strategy_version, threshold_version, schema_version, dimension, value, horizon = key
        if dimension == "STRATEGY_COMBINATION" and len(items) < combination_min:
            continue
        returns = _values(items, f"return_{horizon}d_pct")
        excess_market = _values(items, f"excess_vs_market_{horizon}d_pct")
        excess_random = _values(items, f"excess_vs_random_{horizon}d_pct")
        excess_matched = _values(items, f"excess_vs_matched_{horizon}d_pct")
        mfe = _values(items, "mfe_to_date_pct")
        mae = _values(items, "mae_to_date_pct")
        stddev = statistics.stdev(returns) if len(returns) >= 2 else None
        standard_error = stddev / math.sqrt(len(returns)) if stddev is not None else None
        rows.append({
            "app_version": app_version,
            "strategy_version": strategy_version,
            "threshold_version": threshold_version,
            "schema_version": schema_version,
            "dimension": dimension,
            "group_value": value,
            "horizon_days": horizon,
            "sample_count": len(returns),
            "sample_strength": _sample_strength(len(returns), cfg),
            "average_return_pct": _mean(returns),
            "median_return_pct": _median(returns),
            "positive_rate_pct": (sum(value > 0 for value in returns) / len(returns) * 100
                                  if returns else None),
            "average_excess_vs_market_pct": _mean(excess_market),
            "average_excess_vs_random_pct": _mean(excess_random),
            "average_excess_vs_matched_pct": _mean(excess_matched),
            "median_excess_vs_matched_pct": _median(excess_matched),
            "average_mfe_pct": _mean(mfe),
            "average_mae_pct": _mean(mae),
            "standard_deviation_pct": stddev,
            "standard_error_pct": standard_error,
            "confidence_interval_95_low_pct": (
                _mean(returns) - 1.96 * standard_error if standard_error is not None else None),
            "confidence_interval_95_high_pct": (
                _mean(returns) + 1.96 * standard_error if standard_error is not None else None),
        })
    return rows


def _momentum_bucket(value) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if value < 70:
        return "<70"
    if value < 80:
        return "70-79"
    if value < 90:
        return "80-89"
    if value < 95:
        return "90-94"
    return "95-100"


def _sample_strength(count: int, cfg: dict) -> str:
    thresholds = cfg["summary"]["sample_strength"]
    if count < int(thresholds["preliminary"]):
        return "INSUFFICIENT"
    if count < int(thresholds["moderate"]):
        return "PRELIMINARY"
    if count < int(thresholds["stronger"]):
        return "MODERATE"
    return "STRONGER_SAMPLE"


def _values(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows if row.get(key) is not None]


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _write_csv(path: Path, rows: list[dict]):
    fields = list(rows[0]) if rows else ["signal_id", "export_schema_version"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                               allow_nan=False, default=str), encoding="utf-8")


def _loads(value) -> dict:
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
