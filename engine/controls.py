from __future__ import annotations

import hashlib
import math
import random

import pandas as pd

from .models import SetupState


ELIGIBLE_STATES = (SetupState.BREAKOUT, SetupState.BREAKOUT_WATCH)


def update_controls(db, analyses: list, frames: dict[str, pd.DataFrame],
                    benchmark: pd.DataFrame, security_meta: dict[str, dict], cfg: dict):
    """Freeze same-day controls, then track every existing member without reselection."""
    by_code = {item.code: item for item in analyses}
    for signal in analyses:
        if signal.state not in ELIGIBLE_STATES or not signal.setup_id:
            continue
        signal_id = f"{signal.setup_id}:{cfg['strategy_version']}"
        snapshot = db.signal_snapshot(signal_id)
        if not snapshot or snapshot["signal_date"] != signal.as_of:
            # Historical snapshots cannot be matched safely without their original universe.
            continue
        if not db.load_control_members(signal_id=signal_id):
            members = select_control_members(signal, signal_id, analyses, frames,
                                              security_meta, cfg, snapshot)
            db.save_control_members(members)
    track_control_members(db, by_code, frames, benchmark, cfg)


def select_control_members(signal, signal_id: str, analyses: list,
                           frames: dict[str, pd.DataFrame],
                           security_meta: dict[str, dict], cfg: dict,
                           signal_snapshot: dict | None = None) -> list[dict]:
    candidates = [item for item in analyses
                  if item.code != signal.code and item.as_of == signal.as_of
                  and _has_same_day_price(frames.get(item.code), signal.as_of)]
    random_codes = deterministic_random_codes(
        signal_id, [item.code for item in candidates],
        int(cfg["controls"]["random_count"]), cfg["controls"]["selection_version"])
    by_code = {item.code: item for item in candidates}
    matched_candidates = [item for item in candidates if item.state not in ELIGIBLE_STATES]
    scored = [(matching_distance(signal, item, security_meta, cfg), item.code)
              for item in matched_candidates]
    matched_codes = [code for _, code in sorted(scored, key=lambda row: (row[0], row[1]))
                     [:int(cfg["controls"]["matched_count"])]]
    rows = []
    for control_type, codes in (("RANDOM", random_codes), ("MATCHED", matched_codes)):
        group_id = control_group_id(signal_id, control_type,
                                    cfg["controls"]["selection_version"])
        for rank, code in enumerate(codes, start=1):
            item = by_code[code]
            score = matching_distance(signal, item, security_meta, cfg) if control_type == "MATCHED" else None
            rows.append(_member_row(group_id, signal_id, signal, item, rank, score,
                                    frames, security_meta, cfg, signal_snapshot))
    return rows


def deterministic_random_codes(signal_id: str, candidate_codes: list[str], count: int,
                               selection_version: str) -> list[str]:
    codes = sorted(set(candidate_codes))
    seed_bytes = hashlib.sha256(
        f"{signal_id}|{selection_version}|RANDOM".encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(seed_bytes, "big"))
    return rng.sample(codes, min(count, len(codes)))


def control_group_id(signal_id: str, control_type: str, selection_version: str) -> str:
    raw = f"{signal_id}|{control_type}|{selection_version}"
    return f"ctrl-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]}"


def matching_distance(signal, control, security_meta: dict[str, dict], cfg: dict) -> float:
    weights = cfg["controls"]["matching"]
    signal_liquidity = _positive(signal.metrics.get("trading_value_20d"))
    control_liquidity = _positive(control.metrics.get("trading_value_20d"))
    liquidity = abs(math.log10(control_liquidity / signal_liquidity)) / float(
        weights["liquidity_log10_scale"])
    signal_momentum = _number(signal.metrics.get("momentum_percentile"))
    control_momentum = _number(control.metrics.get("momentum_percentile"))
    momentum = abs(control_momentum - signal_momentum) / float(weights["momentum_scale"])
    signal_price = _positive(signal.metrics.get("price"))
    control_price = _positive(control.metrics.get("price"))
    price = abs(math.log10(control_price / signal_price)) / float(weights["price_log10_scale"])
    signal_meta = security_meta.get(signal.code, {})
    control_meta = security_meta.get(control.code, {})
    market = float(_market_segment(signal_meta.get("market")) !=
                   _market_segment(control_meta.get("market")))
    size_class = float(_size_class(signal_meta.get("size_class")) !=
                       _size_class(control_meta.get("size_class")))
    return (float(weights["liquidity_weight"]) * liquidity +
            float(weights["momentum_weight"]) * momentum +
            float(weights["market_weight"]) * market +
            float(weights["size_class_weight"]) * size_class +
            float(weights["price_weight"]) * price)


def track_control_members(db, analyses_by_code: dict, frames: dict[str, pd.DataFrame],
                          benchmark: pd.DataFrame, cfg: dict):
    rows = []
    for member in db.load_control_members():
        frame = frames.get(member["control_code"])
        current = analyses_by_code.get(member["control_code"])
        if frame is None or frame.empty or current is None:
            continue
        start = pd.Timestamp(member["signal_date"])
        end = pd.Timestamp(current.as_of)
        path = frame.loc[(frame.index >= start) & (frame.index <= end)]
        if path.empty:
            continue
        offset = max(0, len(path) - 1)
        if offset > int(cfg["tracking"]["max_sessions"]):
            continue
        initial = float(member["initial_close"])
        close = float(path.close.iloc[-1])
        absolute = (close / initial - 1) * 100
        benchmark_start = _price_on_or_before(benchmark, start)
        benchmark_now = _price_on_or_before(benchmark, end)
        relative = absolute - ((benchmark_now / benchmark_start - 1) * 100) \
            if benchmark_start and benchmark_now else None
        rows.append({
            "control_group_id": member["control_group_id"],
            "signal_id": member["signal_id"],
            "control_code": member["control_code"],
            "control_type": member["control_type"],
            "date": current.as_of,
            "session_offset": offset,
            "close": close,
            "return_abs": absolute,
            "benchmark_relative_return": relative,
            "mfe": (float(path.high.max()) / initial - 1) * 100,
            "mae": (float(path.low.min()) / initial - 1) * 100,
        })
    db.save_control_history(rows)


def _member_row(group_id: str, signal_id: str, signal, control, rank: int,
                score: float | None, frames, security_meta, cfg,
                signal_snapshot: dict | None) -> dict:
    signal_meta = security_meta.get(signal.code, {})
    control_meta = security_meta.get(control.code, {})
    return {
        "control_group_id": group_id,
        "signal_id": signal_id,
        "signal_date": signal.as_of,
        "control_code": control.code,
        "control_name": control.name,
        "control_type": "MATCHED" if score is not None else "RANDOM",
        "control_rank": rank,
        "match_score": score,
        "matched_at": signal.as_of,
        "initial_close": float(frames[control.code].close.iloc[-1]),
        "signal_momentum_percentile": _nullable(signal.metrics.get("momentum_percentile")),
        "control_momentum_percentile": _nullable(control.metrics.get("momentum_percentile")),
        "signal_trading_value": _nullable(signal.metrics.get("trading_value_20d")),
        "control_trading_value": _nullable(control.metrics.get("trading_value_20d")),
        "signal_market": signal_meta.get("market"),
        "control_market": control_meta.get("market"),
        "signal_size_class": signal_meta.get("size_class"),
        "control_size_class": control_meta.get("size_class"),
        "signal_price": _nullable(signal.metrics.get("price")),
        "control_price": _nullable(control.metrics.get("price")),
        "selection_version": cfg["controls"]["selection_version"],
        "app_version": (signal_snapshot or {}).get("app_version", cfg["logic_version"]),
        "strategy_version": (signal_snapshot or {}).get("strategy_version", cfg["strategy_version"]),
        "threshold_version": (signal_snapshot or {}).get("threshold_version", cfg["threshold_version"]),
        "schema_version": (signal_snapshot or {}).get("schema_version", cfg["schema_version"]),
    }


def _has_same_day_price(frame: pd.DataFrame | None, date: str) -> bool:
    return bool(frame is not None and not frame.empty and str(frame.index[-1].date()) == date)


def _market_segment(value) -> str:
    text = str(value or "")
    for segment in ("プライム", "スタンダード", "グロース"):
        if segment in text:
            return segment
    return text


def _size_class(value) -> str:
    text = str(value or "")
    if "Core30" in text:
        return "Core30"
    if "Large70" in text:
        return "Large70"
    if "Mid400" in text:
        return "Mid400"
    return text


def _positive(value) -> float:
    number = _number(value)
    return max(number, 1e-9)


def _number(value) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _nullable(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _price_on_or_before(frame: pd.DataFrame, date: pd.Timestamp) -> float | None:
    rows = frame.loc[frame.index <= date]
    return float(rows.close.iloc[-1]) if not rows.empty else None
