from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .database import Database
from .liquidity import liquidity_level

STRATEGIES = ("Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas", "Connors")
SLUG = {"Minervini": "minervini", "Qullamaggie": "qullamaggie",
        "CAN SLIM": "can_slim", "Weinstein": "weinstein",
        "Darvas": "darvas", "Connors": "connors"}


def seed_validation(db: Database, base_url: str | None) -> bool:
    if not base_url:
        return False
    try:
        response = requests.get(base_url.rstrip("/") + "/state.json", timeout=20)
        if response.status_code != 200:
            return False
        payload = response.json()
        db.import_validation_rows(payload.get("signals", []), payload.get("history", []))
        return True
    except Exception:
        return False


def export_validation(db: Database, output: Path, cfg: dict) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    signals_raw, history_raw = db.validation_rows()
    signals = [_signal_row(row, cfg) for row in signals_raw]
    history = [_history_row(row) for row in history_raw]
    performance = _performance_rows(signals, history, cfg["tracking"]["horizons"])
    _write_csv(output / "signals.csv", signals)
    _write_csv(output / "signal_history.csv", history)
    _write_csv(output / "performance.csv", performance)
    _write_json(output / "signals.json", signals)
    _write_json(output / "signal_history.json", history)
    _write_json(output / "performance.json", performance)
    _write_json(output / "state.json", {"signals": signals_raw, "history": history_raw})
    dates = [row["signal_date"] for row in signals if row.get("signal_date")]
    history_dates = [row["date"] for row in history if row.get("date")]
    generated = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    index = {
        "generated_at": generated,
        "schema_version": cfg["export_schema_version"],
        "logic_version": cfg["logic_version"],
        "strategy_version": cfg["strategy_version"],
        "threshold_version": cfg["threshold_version"],
        "signal_count": len(signals),
        "history_count": len(history),
        "performance_count": len(performance),
        "observation_start": min(dates + history_dates) if dates or history_dates else None,
        "observation_end": max(dates + history_dates) if dates or history_dates else None,
        "available_files": ["signals.csv", "signal_history.csv", "performance.csv",
                            "signals.json", "signal_history.json", "performance.json"],
        "notes": [
            "return/mfe/maeの単位はpercent",
            "signal_idでSnapshot・History・Performanceを結合可能",
            "Signal Snapshotは発生時点の判定を保持し後日上書きしない",
            "Liquidityは20日平均売買代金、trading_value_ratioは当日÷20日平均",
        ],
    }
    _write_json(output / "index.json", index)
    return index


def _signal_row(row: dict, cfg: dict) -> dict:
    out = {k: v for k, v in row.items()
           if k not in ("strategy_states_json", "strategy_pivots_json", "trade_plan_json", "created_at")}
    trading_value_20d = row.get("trading_value_20d")
    if trading_value_20d is None:
        trading_value_20d = row.get("trading_value")
    out["trading_value_20d"] = trading_value_20d
    out["liquidity_level"] = row.get("liquidity_level") or liquidity_level(trading_value_20d, cfg)
    out["liquid"] = (int(float(trading_value_20d) >=
                         float(cfg["liquidity"]["minimum_trading_value_yen"]))
                     if trading_value_20d is not None else row.get("liquid"))
    states = _loads(row.get("strategy_states_json"))
    pivots = _loads(row.get("strategy_pivots_json"))
    plan = _loads(row.get("trade_plan_json"))
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
    out["export_schema_version"] = row.get("schema_version")
    return out


def _history_row(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in ("strategy_states_json", "created_at")}
    states = _loads(row.get("strategy_states_json"))
    for strategy in STRATEGIES:
        out[f"{SLUG[strategy]}_state"] = states.get(strategy)
    return out


def _performance_rows(signals: list[dict], history: list[dict], horizons) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in history:
        grouped.setdefault(row["signal_id"], []).append(row)
    rows = []
    for signal in signals:
        out = {"signal_id": signal["signal_id"], "signal_date": signal["signal_date"],
               "ticker": signal["code"], "stock_name": signal["stock_name"],
               "initial_consensus_state": signal["consensus_state"],
               "initial_breakout_count": signal["breakout_count"],
               "initial_aligned_count": signal["aligned_count"],
               "initial_coverage": signal["coverage"], "initial_confidence": signal["confidence"],
               "pivot_fidelity": signal["pivot_fidelity"],
               "momentum_percentile": signal["momentum_percentile"],
               "market_regime": signal["market_regime"],
               "initial_trading_value_20d": signal.get("trading_value_20d"),
               "initial_liquidity_level": signal.get("liquidity_level"),
               "initial_trading_value": signal.get("current_trading_value"),
               "initial_trading_value_ratio": signal.get("trading_value_ratio")}
        observations = {int(r["session_offset"]): r for r in grouped.get(signal["signal_id"], [])}
        for horizon in horizons:
            obs = observations.get(int(horizon))
            suffix = f"{horizon}d"
            for key, source in ((f"return_{suffix}_pct", "return_abs"),
                                (f"benchmark_relative_{suffix}_pct", "benchmark_relative_return"),
                                (f"consensus_state_{suffix}", "consensus_state"),
                                (f"breakout_count_{suffix}", "breakout_count"),
                                (f"liquidity_level_{suffix}", "liquidity_level"),
                                (f"trading_value_{suffix}", "trading_value"),
                                (f"trading_value_ratio_{suffix}", "trading_value_ratio")):
                out[key] = obs.get(source) if obs else None
        latest = max(observations.values(), key=lambda r: int(r["session_offset"]), default={})
        out["mfe_to_date_pct"] = latest.get("mfe")
        out["mae_to_date_pct"] = latest.get("mae")
        out["failed_breakout"] = latest.get("failed_breakout")
        out["hit_1r"] = latest.get("hit_1r")
        out["hit_2r"] = latest.get("hit_2r")
        out["hit_stop"] = latest.get("hit_stop")
        rows.append(out)
    return rows


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
