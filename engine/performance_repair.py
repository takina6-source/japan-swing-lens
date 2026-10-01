"""Revalue observations on one current adjusted OHLC basis; never edit signal identity."""
from __future__ import annotations

import math
from collections import Counter

import pandas as pd

from .validation_engine import path_metrics, price_on_or_before


STATUS_COLUMNS = {
    "corporate_action_flag": "INTEGER",
    "corporate_action_type": "TEXT",
    "split_ratio": "REAL",
    "corporate_action_date": "TEXT",
    "price_adjustment_status": "TEXT",
    "performance_status": "TEXT",
    "anomaly_reason": "TEXT",
    "raw_entry_price": "REAL",
    "raw_forward_price": "REAL",
    "adjusted_entry_price": "REAL",
    "adjusted_forward_price": "REAL",
    "raw_return": "REAL",
    "corrected_return": "REAL",
}


def _positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def _split_between(actions, start, end):
    for date, ratio in sorted(actions or [], key=lambda item: str(item[0])):
        day = str(pd.Timestamp(date).date())
        value = _positive(ratio)
        if str(start) < day <= str(end) and value and value != 1:
            return day, value
    return None, None


def revalue_observation(row, anchor, frame, benchmark=None, actions=(),
                        *, anchor_column="close"):
    """Return new metric/diagnostic fields from current adjusted prices.

    `row` is the old observation.  Its stored close/return are diagnostic inputs only.
    A missing canonical anchor never falls back to an old snapshot price.
    """
    start, end = str(anchor["date"]), str(row["date"])
    raw_entry = _positive(row.get("raw_entry_price")) or _positive(anchor.get("price"))
    raw_forward = _positive(row.get("raw_forward_price")) or _positive(row.get("close"))
    raw_return = (raw_forward / raw_entry - 1) * 100 if raw_entry and raw_forward else None
    action_date, split_ratio = _split_between(actions, start, end)
    base = {
        "corporate_action_flag": int(bool(action_date)),
        "corporate_action_type": ("stock_split" if split_ratio > 1 else "reverse_split")
                                 if split_ratio else None,
        "split_ratio": split_ratio, "corporate_action_date": action_date,
        "price_adjustment_status": "UNVERIFIED",
        "raw_entry_price": raw_entry, "raw_forward_price": raw_forward,
        "raw_return": raw_return, "adjusted_entry_price": None,
        "adjusted_forward_price": None, "corrected_return": None,
        "anomaly_reason": None,
    }
    if frame is None or frame.empty:
        return {**base, "return_abs": None, "benchmark_relative_return": None,
                "mfe": None, "mae": None,
                "performance_status": "PRICE_DATA_INCONSISTENT",
                "anomaly_reason": "ADJUSTED_HISTORY_UNAVAILABLE"}
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    entry = anchor.get("adjusted_price") or price_on_or_before(frame, start_ts,
                                                                anchor_column)
    path = frame.loc[(frame.index >= start_ts) & (frame.index <= end_ts)]
    if (not _positive(entry) or path.empty or
            str(path.index[0].date()) != start or str(path.index[-1].date()) != end):
        return {**base, "return_abs": None, "benchmark_relative_return": None,
                "mfe": None, "mae": None,
                "performance_status": "PRICE_DATA_INCONSISTENT",
                "anomaly_reason": "ADJUSTED_DATE_MISSING"}
    metrics = path_metrics(path, entry, benchmark, start_ts, end_ts,
                           anchor_column)
    corrected = metrics["return_abs"]
    day_moves = path.close.pct_change().abs().dropna()
    # A split-like jump remaining in an adjusted path means the cached series is mixed.
    extreme_day = bool((day_moves > .50).any())
    status = "OK"
    reason = None
    if extreme_day or abs(corrected) > 500:
        status, reason = "ANOMALY_EXCLUDED", (
            "ADJUSTED_DAILY_JUMP_GT_50_PCT" if extreme_day else
            "ADJUSTED_RETURN_GT_500_PCT")
    elif action_date or (raw_return is not None and abs(raw_return - corrected) > 5
                         and raw_entry and abs(raw_entry / entry - 1) > .10):
        status = "CORPORATE_ACTION_ADJUSTED"
        if not action_date:
            base["corporate_action_flag"] = 1
            base["corporate_action_type"] = "suspected_corporate_action"
            ratio = raw_entry / entry
            for candidate in (2, 3, 5, 10, .5, 1 / 3, .2, .1):
                if abs(ratio / candidate - 1) < .08:
                    base["split_ratio"] = candidate
                    break
    return {**base, **metrics,
            "price_adjustment_status": "INCONSISTENT" if extreme_day else "ADJUSTED",
            "performance_status": status, "anomaly_reason": reason,
            "adjusted_entry_price": entry, "adjusted_forward_price": metrics["close"],
            "corrected_return": corrected,
            "return_abs": None if reason else corrected,
            "benchmark_relative_return": None if reason else metrics["benchmark_relative_return"],
            "mfe": None if reason else metrics["mfe"],
            "mae": None if reason else metrics["mae"]}


def repair_table(db, table, anchors, frames, benchmark, actions=None):
    """Update only observation rows. Anchors and selection memberships remain immutable."""
    actions = actions or {}
    with db.connect() as con:
        existing = {item[1] for item in con.execute(f"PRAGMA table_info({table})")}
        for column, declaration in STATUS_COLUMNS.items():
            if column not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        con.row_factory = __import__("sqlite3").Row
        rows = [dict(row) for row in con.execute(f"SELECT rowid,* FROM {table} ORDER BY rowid")]
        examples = []
        counts = Counter()
        for row in rows:
            anchor = anchors.get(row["rowid"])
            if anchor is None:
                continue
            code = anchor["code"]
            repaired = revalue_observation(row, anchor, frames.get(code), benchmark,
                                           actions.get(code, ()),
                                           anchor_column=anchor.get("column", "close"))
            if (repaired["performance_status"] == "PRICE_DATA_INCONSISTENT"
                    and abs(float(row.get("return_abs") or 0)) <= 50):
                continue
            counts["checked_observations"] += 1
            counts[repaired["performance_status"]] += 1
            updates = {key: value for key, value in repaired.items()
                       if key in STATUS_COLUMNS or key in
                       {"close", "return_abs", "benchmark_relative_return", "mfe", "mae"}}
            columns = list(updates)
            con.execute(f"UPDATE {table} SET " + ",".join(f"{col}=?" for col in columns)
                        + " WHERE rowid=?", [*(updates[col] for col in columns), row["rowid"]])
            if repaired["performance_status"] != "OK" and len(examples) < 100:
                examples.append({"code": code, "signal_date": anchor["date"],
                                 "horizon": row.get("session_offset"), **{
                                     key: repaired.get(key) for key in (
                                         "raw_entry_price", "raw_forward_price",
                                         "adjusted_entry_price", "adjusted_forward_price",
                                         "raw_return", "corrected_return", "split_ratio")},
                                 "action_type": repaired["corporate_action_type"],
                                 "performance_status": repaired["performance_status"],
                                 "anomaly_reason": repaired["anomaly_reason"]})
    return {"checked_observations": counts["checked_observations"],
            "corporate_action_adjusted": counts["CORPORATE_ACTION_ADJUSTED"],
            "anomaly_excluded": counts["ANOMALY_EXCLUDED"],
            "price_inconsistent": counts["PRICE_DATA_INCONSISTENT"],
            "examples": examples}


def repair_validation(db, frames, benchmark, actions=None):
    signals, _ = db.validation_rows()
    controls, _ = db.control_validation_rows()
    snapshots, _, experimental_controls, _ = db.experimental_rows()
    specs = [
        ("signal_history", "signal_id", {r["signal_id"]: r for r in signals},
         "code", "signal_date", "close"),
        ("control_history", "control_group_id", {
            (r["control_group_id"], r["control_code"]): r for r in controls},
         "control_code", "signal_date", "initial_close"),
        ("experimental_history", "experimental_signal_id", {
            r["experimental_signal_id"]: r for r in snapshots},
         "code", "signal_date", "close"),
        ("experimental_control_history", "control_group_id", {
            (r["control_group_id"], r["control_code"]): r for r in experimental_controls},
         "control_code", "signal_date", "initial_close"),
    ]
    totals = Counter()
    examples = []
    for table, key, members, code_key, date_key, price_key in specs:
        with db.connect() as con:
            con.row_factory = __import__("sqlite3").Row
            records = [dict(r) for r in con.execute(f"SELECT rowid,* FROM {table}")]
        anchors = {}
        for row in records:
            identity = ((row[key], row["control_code"]) if "control" in table else row[key])
            member = members.get(identity)
            if member:
                anchors[row["rowid"]] = {"code": member[code_key],
                                          "date": member[date_key],
                                          "price": member[price_key]}
        result = repair_table(db, table, anchors, frames, benchmark, actions)
        totals.update({k: result[k] for k in (
            "checked_observations", "corporate_action_adjusted",
            "anomaly_excluded", "price_inconsistent")})
        examples.extend(result["examples"])
    return {**totals, "examples": examples[:100]}


def action_candidates(db, frames):
    """Find frozen anchors whose basis differs materially from today's cache."""
    signals, _ = db.validation_rows()
    controls, _ = db.control_validation_rows()
    experimental, _, experimental_controls, _ = db.experimental_rows()
    members = [(r["code"], r["signal_date"], r["close"]) for r in signals]
    members += [(r["control_code"], r["signal_date"], r["initial_close"])
                for r in controls]
    members += [(r["code"], r["signal_date"], r["close"]) for r in experimental]
    members += [(r["control_code"], r["signal_date"], r["initial_close"])
                for r in experimental_controls]
    candidates = set()
    for code, frame in frames.items():
        if frame is not None and not frame.empty and bool(
                (frame.close.tail(40).pct_change().abs() > .50).any()):
            candidates.add(code)
    for code, date, old_price in members:
        frame = frames.get(code)
        current = price_on_or_before(frame, pd.Timestamp(date))
        old = _positive(old_price)
        if old and current and abs(old / current - 1) > .10:
            candidates.add(code)
    return candidates


def repair_research(db, frames, benchmark, actions=None):
    from .research.storage import ResearchStore

    store = ResearchStore(db)
    subjects = {r["validation_subject_id"]: r
                for r in store.rows("validation_subjects")}
    events = {r["research_event_id"]: r for r in store.rows("research_events")}
    controls = {(r["control_group_id"], r["control_code"]): r
                for r in store.rows("research_control_members")}
    results = []
    for table, members in (("research_subject_history", subjects),
                           ("research_control_history", controls)):
        with db.connect() as con:
            con.row_factory = __import__("sqlite3").Row
            records = [dict(r) for r in con.execute(f"SELECT rowid,* FROM {table}")]
        anchors = {}
        for row in records:
            key = ((row["control_group_id"], row["control_code"])
                   if "control" in table else row["validation_subject_id"])
            member = members.get(key)
            if member:
                basis = str(member.get("price_basis") or "CLOSE")
                anchors[row["rowid"]] = {
                    "code": member.get("control_code") or member.get("code"),
                    "date": member["anchor_date"], "price": member["anchor_price"],
                    "column": basis.lower() if basis in {"OPEN", "CLOSE"} else "close",
                }
                if basis == "PIVOT_TRIGGER":
                    # The trigger is a frozen entry rule, so scale its price by the
                    # contemporaneous close factor; never substitute the close itself.
                    event = events.get(member.get("source_event_id"), {})
                    old_close = _positive(event.get("close"))
                    frame = frames.get(member["code"])
                    new_close = price_on_or_before(frame, pd.Timestamp(member["anchor_date"]))
                    if old_close and new_close:
                        anchors[row["rowid"]]["adjusted_price"] = (
                            float(member["anchor_price"]) * new_close / old_close)
        results.append(repair_table(db, table, anchors, frames, benchmark, actions))
    counts = Counter()
    examples = []
    for result in results:
        counts.update({key: result[key] for key in (
            "checked_observations", "corporate_action_adjusted",
            "anomaly_excluded", "price_inconsistent")})
        examples.extend(result["examples"])
    return {**counts, "examples": examples[:100]}
