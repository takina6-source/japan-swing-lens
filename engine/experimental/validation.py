from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ..controls import control_group_id, deterministic_random_codes, matching_distance


def seed_experimental(db, base_url: str | None) -> bool:
    if not base_url:
        return False
    try:
        response = requests.get(base_url.rstrip("/") + "/state.json", timeout=30)
        if response.status_code != 200:
            return False
        value = response.json()
        db.import_experimental_rows(value.get("signals", []), value.get("history", []),
                                    value.get("controls", []), value.get("control_history", []))
        return True
    except Exception:
        return False


def update_experimental_tracking(db, experimental: dict, core_analyses: list,
                                 frames: dict[str, pd.DataFrame], benchmark: pd.DataFrame,
                                 security_meta: dict[str, dict], cfg: dict):
    core_by_code = {item.code: item for item in core_analyses}
    start_date = cfg["experiment_start_date"]
    snapshots = []
    current_ids = []
    for code, analysis in experimental.items():
        if analysis.as_of < start_date:
            continue
        core = core_by_code[code]
        core_signal = core.state.value in ("BREAKOUT", "BREAKOUT WATCH") and bool(core.setup_id)
        if core_signal:
            core_id = f"{core.setup_id}:{cfg['strategy_version']}"
            db.attach_core_experimental(core_id, core.as_of, analysis, cfg)
        for strategy, result in analysis.results.items():
            if not result.signal or not result.setup_id:
                continue
            signal_id = _signal_id(code, strategy, result.setup_id, cfg["experimental_version"])
            current_ids.append(signal_id)
            cross = _cross_signal(core.state.value if core_signal else None, analysis.combination)
            snapshots.append({
                "experimental_signal_id": signal_id, "signal_date": analysis.as_of,
                "code": code, "stock_name": analysis.name, "strategy": strategy,
                "initial_state": result.state, "close": float(frames[code].close.iloc[-1]),
                "benchmark_close": _price(benchmark, pd.Timestamp(analysis.as_of)),
                "experimental_alignment": analysis.alignment,
                "experimental_combination": analysis.combination,
                "core_signal": int(core_signal), "core_state": core.state.value if core_signal else None,
                "cross_signal": cross, "metrics_json": json.dumps(result.metrics, ensure_ascii=False),
                "coverage": result.coverage, "fidelity": result.fidelity,
                "setup_id": result.setup_id, "experiment_start_date": start_date,
                "experimental_version": cfg["experimental_version"],
                "schema_version": cfg["experimental_export_schema_version"],
            })
    db.save_experimental_snapshots(snapshots)
    _save_signal_history(db, experimental, frames, benchmark, cfg)
    _ensure_controls(db, current_ids, experimental, core_by_code, frames, security_meta, cfg)
    _track_controls(db, core_by_code, frames, benchmark, cfg)


def _save_signal_history(db, experimental, frames, benchmark, cfg):
    rows = []
    for code, analysis in experimental.items():
        frame = frames[code]
        for snapshot in db.experimental_snapshots(code):
            start = pd.Timestamp(snapshot["signal_date"])
            end = pd.Timestamp(analysis.as_of)
            path = frame.loc[(frame.index >= start) & (frame.index <= end)]
            if path.empty:
                continue
            offset = len(path) - 1
            if offset > int(cfg["tracking"]["max_sessions"]):
                continue
            initial, close = float(snapshot["close"]), float(path.close.iloc[-1])
            absolute = (close / initial - 1) * 100
            bench_start, bench_now = _price(benchmark, start), _price(benchmark, end)
            current = analysis.results.get(snapshot["strategy"])
            state = current.state if current else "N/A"
            rows.append({
                "experimental_signal_id": snapshot["experimental_signal_id"],
                "date": analysis.as_of, "session_offset": offset, "close": close,
                "return_abs": absolute,
                "benchmark_relative_return": absolute - ((bench_now / bench_start - 1) * 100)
                if bench_start and bench_now else None,
                "mfe": (float(path.high.max()) / initial - 1) * 100,
                "mae": (float(path.low.min()) / initial - 1) * 100,
                "state": state, "failed": int(state == "FAILED"),
            })
    db.save_experimental_history(rows)


def _ensure_controls(db, current_ids, experimental, core_by_code, frames, meta, cfg):
    positive_codes = {code for code, analysis in experimental.items() if analysis.alignment > 0}
    candidates = [item for item in core_by_code.values() if item.code not in positive_codes]
    selection = cfg["experimental"]["controls"]["selection_version"]
    for snapshot in db.experimental_snapshots():
        signal_id = snapshot["experimental_signal_id"]
        if signal_id not in current_ids or db.experimental_controls(signal_id):
            continue
        signal = core_by_code.get(snapshot["code"])
        if not signal or signal.as_of != snapshot["signal_date"]:
            continue
        random_codes = deterministic_random_codes(
            signal_id, [item.code for item in candidates],
            int(cfg["experimental"]["controls"]["random_count"]), selection)
        scored = sorted((matching_distance(signal, item, meta, cfg), item.code)
                        for item in candidates if item.code != signal.code)
        matched_codes = [code for _, code in scored[:int(cfg["experimental"]["controls"]["matched_count"])]]
        by_code = {item.code: item for item in candidates}
        rows = []
        for control_type, codes in (("RANDOM", random_codes), ("MATCHED", matched_codes)):
            group = control_group_id(signal_id, control_type, selection)
            for rank, code in enumerate(codes, 1):
                item = by_code[code]
                rows.append({
                    "control_group_id": group, "experimental_signal_id": signal_id,
                    "signal_date": snapshot["signal_date"], "control_code": code,
                    "control_name": item.name, "control_type": control_type,
                    "control_rank": rank,
                    "match_score": matching_distance(signal, item, meta, cfg)
                    if control_type == "MATCHED" else None,
                    "initial_close": float(frames[code].close.iloc[-1]),
                    "selection_version": selection,
                    "experimental_version": cfg["experimental_version"],
                })
        db.save_experimental_control_members(rows)


def _track_controls(db, core_by_code, frames, benchmark, cfg):
    rows = []
    for member in db.experimental_controls():
        frame, current = frames.get(member["control_code"]), core_by_code.get(member["control_code"])
        if frame is None or frame.empty or current is None:
            continue
        start, end = pd.Timestamp(member["signal_date"]), pd.Timestamp(current.as_of)
        path = frame.loc[(frame.index >= start) & (frame.index <= end)]
        if path.empty or len(path) - 1 > int(cfg["tracking"]["max_sessions"]):
            continue
        initial, close = float(member["initial_close"]), float(path.close.iloc[-1])
        absolute = (close / initial - 1) * 100
        bench_start, bench_now = _price(benchmark, start), _price(benchmark, end)
        rows.append({
            "control_group_id": member["control_group_id"],
            "experimental_signal_id": member["experimental_signal_id"],
            "control_code": member["control_code"], "control_type": member["control_type"],
            "date": current.as_of, "session_offset": len(path) - 1, "close": close,
            "return_abs": absolute,
            "benchmark_relative_return": absolute - ((bench_now / bench_start - 1) * 100)
            if bench_start and bench_now else None,
            "mfe": (float(path.high.max()) / initial - 1) * 100,
            "mae": (float(path.low.min()) / initial - 1) * 100,
        })
    db.save_experimental_control_history(rows)


def export_experimental(db, output: Path, cfg: dict) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    snapshots, history, controls, control_history = db.experimental_rows()
    signals = [_flatten_signal(row) for row in snapshots]
    control_performance = _control_performance(controls, control_history, cfg["tracking"]["horizons"])
    performance = _performance(signals, history, control_performance, cfg["tracking"]["horizons"])
    summary = _summary(performance, cfg)
    for name, rows in (("signals", signals), ("history", history), ("performance", performance),
                       ("controls", controls), ("control_performance", control_performance),
                       ("summary", summary)):
        _write_csv(output / f"{name}.csv", rows)
        _write_json(output / f"{name}.json", rows)
    _write_json(output / "state.json", {"signals": snapshots, "history": history,
                                         "controls": controls, "control_history": control_history})
    offsets = [int(row["session_offset"]) for row in history if row.get("session_offset") is not None]
    generated = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    index = {
        "generated_at": generated, "experimental_version": cfg["experimental_version"],
        "experiment_start_date": cfg["experiment_start_date"],
        "trading_sessions_elapsed": max(offsets, default=0),
        "schema_version": cfg["experimental_export_schema_version"],
        "signal_count": len(signals), "history_count": len(history),
        "control_count": len(controls), "control_group_count": len({row["control_group_id"] for row in controls}),
        "summary_count": len(summary),
        "available_files": ["signals.csv", "signals.json", "history.csv", "history.json",
                            "performance.csv", "performance.json", "controls.csv", "controls.json",
                            "control_performance.csv", "control_performance.json",
                            "summary.csv", "summary.json"],
        "notes": ["ExperimentalはCoreランキングに影響しない",
                  "SnapshotとControl membershipは後日上書きしない",
                  "現在UniverseによるSurvivorship Biasは除去できない"],
    }
    _write_json(output / "index.json", index)
    return index


def _flatten_signal(row):
    out = {key: value for key, value in row.items() if key not in ("metrics_json", "created_at")}
    out.update(json.loads(row.get("metrics_json") or "{}"))
    return out


def _control_performance(controls, history, horizons):
    grouped = defaultdict(dict)
    for row in history:
        grouped[(row["control_group_id"], row["control_code"])][int(row["session_offset"])] = row
    output = []
    for member in controls:
        out = {key: value for key, value in member.items() if key != "created_at"}
        obs = grouped[(member["control_group_id"], member["control_code"])]
        for horizon in horizons:
            row = obs.get(int(horizon))
            out[f"return_{horizon}d_pct"] = row.get("return_abs") if row else None
        latest = max(obs.values(), key=lambda row: row["session_offset"], default={})
        out["mfe_to_date_pct"], out["mae_to_date_pct"] = latest.get("mfe"), latest.get("mae")
        output.append(out)
    return output


def _performance(signals, history, controls, horizons):
    observations = defaultdict(dict)
    for row in history:
        observations[row["experimental_signal_id"]][int(row["session_offset"])] = row
    baselines = defaultdict(list)
    for row in controls:
        baselines[(row["experimental_signal_id"], row["control_type"])].append(row)
    output = []
    for signal in signals:
        out = dict(signal)
        obs = observations[signal["experimental_signal_id"]]
        for horizon in horizons:
            row, suffix = obs.get(int(horizon)), f"{horizon}d"
            value = row.get("return_abs") if row else None
            market_excess = row.get("benchmark_relative_return") if row else None
            out[f"return_{suffix}_pct"] = value
            out[f"excess_vs_market_{suffix}_pct"] = market_excess
            for kind in ("RANDOM", "MATCHED"):
                values = [item.get(f"return_{suffix}_pct") for item in baselines[(signal["experimental_signal_id"], kind)]]
                values = [float(item) for item in values if item is not None]
                mean = statistics.fmean(values) if values else None
                out[f"{kind.lower()}_sample_count_{suffix}"] = len(values)
                out[f"{kind.lower()}_return_mean_{suffix}_pct"] = mean
                out[f"excess_vs_{kind.lower()}_{suffix}_pct"] = value - mean if value is not None and mean is not None else None
        latest = max(obs.values(), key=lambda row: row["session_offset"], default={})
        out["mfe_to_date_pct"], out["mae_to_date_pct"] = latest.get("mfe"), latest.get("mae")
        out["failed"] = latest.get("failed")
        output.append(out)
    return output


def _summary(performance, cfg):
    groups = defaultdict(list)
    for row in performance:
        dimensions = [("STRATEGY_STATE", f"{row['strategy']}:{row['initial_state']}"),
                      ("STRATEGY", row["strategy"]),
                      ("EXPERIMENTAL_ALIGNMENT", str(row["experimental_alignment"])),
                      ("EXPERIMENTAL_COMBINATION", row["experimental_combination"]),
                      ("CORE_CROSS", row.get("cross_signal"))]
        for horizon in cfg["tracking"]["horizons"]:
            if row.get(f"return_{horizon}d_pct") is None:
                continue
            for dimension, value in dimensions:
                if value:
                    groups[(row["experimental_version"], dimension, value, int(horizon))].append(row)
    output = []
    for (version, dimension, value, horizon), items in sorted(groups.items()):
        returns = _values(items, f"return_{horizon}d_pct")
        market = _values(items, f"excess_vs_market_{horizon}d_pct")
        random = _values(items, f"excess_vs_random_{horizon}d_pct")
        matched = _values(items, f"excess_vs_matched_{horizon}d_pct")
        std = statistics.stdev(returns) if len(returns) >= 2 else None
        se = std / math.sqrt(len(returns)) if std is not None else None
        mean = statistics.fmean(returns) if returns else None
        output.append({
            "experimental_version": version, "dimension": dimension, "group_value": value,
            "horizon_days": horizon, "sample_count": len(returns),
            "sample_strength": _sample_strength(len(returns), cfg),
            "average_return_pct": mean, "median_return_pct": statistics.median(returns),
            "positive_rate_pct": sum(item > 0 for item in returns) / len(returns) * 100,
            "average_excess_vs_market_pct": _mean(market),
            "average_excess_vs_random_pct": _mean(random),
            "average_excess_vs_matched_pct": _mean(matched),
            "average_mfe_pct": _mean(_values(items, "mfe_to_date_pct")),
            "average_mae_pct": _mean(_values(items, "mae_to_date_pct")),
            "standard_deviation_pct": std, "standard_error_pct": se,
            "confidence_interval_95_low_pct": mean - 1.96 * se if se is not None else None,
            "confidence_interval_95_high_pct": mean + 1.96 * se if se is not None else None,
        })
    return output


def _signal_id(code, strategy, setup_id, version):
    value = f"{version}|{code}|{strategy}|{setup_id}"
    return f"exp-{hashlib.sha1(value.encode()).hexdigest()[:24]}"


def _cross_signal(core_state, combination):
    return f"CORE_{core_state}+{combination}" if core_state else f"EXPERIMENTAL_ONLY+{combination}"


def _price(frame, date):
    rows = frame.loc[frame.index <= date]
    return float(rows.close.iloc[-1]) if not rows.empty else None


def _values(rows, key):
    return [float(row[key]) for row in rows if row.get(key) is not None]


def _mean(values):
    return statistics.fmean(values) if values else None


def _sample_strength(count, cfg):
    levels = cfg["summary"]["sample_strength"]
    if count < int(levels["preliminary"]): return "INSUFFICIENT"
    if count < int(levels["moderate"]): return "PRELIMINARY"
    if count < int(levels["stronger"]): return "MODERATE"
    return "STRONGER_SAMPLE"


def _write_csv(path, rows):
    fields = list(rows[0]) if rows else ["experimental_signal_id", "experimental_version"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                               allow_nan=False, default=str), encoding="utf-8")
