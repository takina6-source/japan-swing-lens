"""Small, code-keyed daily snapshots for the lightweight What Changed report.

This deliberately does not infer setup continuity.  It only compares the same
security code across two Core snapshots.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date


SNAPSHOT_SCHEMA = "simple-snapshot-v1"
REPORT_SCHEMA = "simple-what-changed-v1"
STATE_LABELS = {
    "BREAKOUT": "新規ブレイク",
    "BREAKOUT WATCH": "ブレイク直前",
    "SETUP FORMING": "形成中",
    "PULLBACK": "押し目",
    "EXTENDED": "過熱",
    "FAILED": "失敗",
    "NOT QUALIFIED": "対象外",
}


def _hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _market_date(value: object) -> str:
    text = str(value or "")
    date.fromisoformat(text)
    return text


def compact_snapshot(snapshot: dict) -> dict:
    """Reduce a Core snapshot to the four facts needed for daily comparison."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("candidates"), list):
        raise ValueError("INVALID_CORE_SNAPSHOT")
    as_of = _market_date(snapshot.get("as_of"))
    stocks, seen = [], set()
    for rank, row in enumerate(snapshot["candidates"], 1):
        if not isinstance(row, dict):
            raise ValueError("INVALID_CANDIDATE")
        code = str(row.get("code") or "")
        if not (len(code) == 4 and code.isascii() and code.isalnum()) or code in seen:
            raise ValueError("INVALID_OR_DUPLICATE_CODE")
        seen.add(code)
        stocks.append({
            "code": code,
            "name": str(row.get("name") or ""),
            "state": str(row.get("state") or "UNKNOWN"),
            "rank": rank,
        })
    payload = {
        "schema_version": SNAPSHOT_SCHEMA,
        "as_of": as_of,
        "captured_at": str(snapshot.get("generated_at") or "") or None,
        "scope": str(snapshot.get("scope") or ""),
        "stock_count": len(stocks),
        "stocks": stocks,
    }
    payload["source_hash"] = _hash(payload)
    return payload


def validate_compact(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("schema_version") != SNAPSHOT_SCHEMA:
        raise ValueError("INVALID_COMPACT_SCHEMA")
    _market_date(payload.get("as_of"))
    stocks = payload.get("stocks")
    if not isinstance(stocks, list) or payload.get("stock_count") != len(stocks):
        raise ValueError("INVALID_COMPACT_COUNT")
    expected = dict(payload)
    claimed = expected.pop("source_hash", None)
    if not isinstance(claimed, str) or claimed != _hash(expected):
        raise ValueError("INVALID_COMPACT_HASH")
    seen = set()
    for row in stocks:
        if not isinstance(row, dict) or set(row) != {"code", "name", "state", "rank"}:
            raise ValueError("INVALID_COMPACT_ROW")
        if row["code"] in seen or not isinstance(row["rank"], int) or row["rank"] < 1:
            raise ValueError("INVALID_COMPACT_ROW")
        seen.add(row["code"])
    return payload


def _base_report(current: dict, previous: dict | None, status: str, reason: str | None) -> dict:
    return {
        "schema_version": REPORT_SCHEMA,
        "status": status,
        "reason": reason,
        "current_date": current["as_of"],
        "previous_date": previous["as_of"] if previous else None,
        "checked_at": current.get("captured_at"),
        "previous_checked_at": previous.get("captured_at") if previous else None,
        "scope": current["scope"],
        "current_count": current["stock_count"],
        "previous_count": previous["stock_count"] if previous else 0,
        "state_changes": [],
        "rank_changes": [],
        "added_codes": [],
        "removed_codes": [],
        "summary_lines": [],
        "disclaimer": "銘柄コード単位の前回比較です。setupの同一性やPivotの継続性は判定していません。",
    }


def compare_snapshots(previous: dict | None, current: dict, *, rank_threshold: int = 10) -> dict:
    """Compare two distinct market dates, using only security code identity."""
    validate_compact(current)
    if previous is None:
        report = _base_report(current, None, "BASELINE_ONLY", "PREVIOUS_SNAPSHOT_MISSING")
        report["summary_lines"] = ["初回保存日です。次の市場日から前回比較を表示します。"]
        return report
    validate_compact(previous)
    if current["as_of"] == previous["as_of"]:
        report = _base_report(current, previous, "NO_NEW_MARKET_DATE", "SAME_MARKET_DATE")
        report["summary_lines"] = ["市場日が前回と同じため、新しい差分はありません。"]
        return report
    if current["as_of"] < previous["as_of"]:
        report = _base_report(current, previous, "UNAVAILABLE", "DATE_WENT_BACKWARDS")
        report["summary_lines"] = ["日付の前後関係を確認できないため、差分を表示しません。"]
        return report
    if current["scope"] != previous["scope"]:
        report = _base_report(current, previous, "UNAVAILABLE", "SCOPE_CHANGED")
        report["summary_lines"] = ["分析範囲が変わったため、前回との単純比較を停止しました。"]
        return report

    before = {row["code"]: row for row in previous["stocks"]}
    after = {row["code"]: row for row in current["stocks"]}
    common = sorted(before.keys() & after.keys())
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed_share = (len(added) + len(removed)) / max(len(before), 1)
    if changed_share > 0.05:
        report = _base_report(current, previous, "UNAVAILABLE", "UNIVERSE_CHANGED_TOO_MUCH")
        report["added_codes"], report["removed_codes"] = added, removed
        report["summary_lines"] = ["比較対象の増減が5%を超えたため、誤解防止のため差分を停止しました。"]
        return report

    report = _base_report(current, previous, "READY", None)
    report["added_codes"], report["removed_codes"] = added, removed
    for code in common:
        old, new = before[code], after[code]
        if old["state"] != new["state"]:
            report["state_changes"].append({
                "code": code, "name": new["name"],
                "from": old["state"], "to": new["state"],
            })
        delta = old["rank"] - new["rank"]
        if abs(delta) >= rank_threshold:
            report["rank_changes"].append({
                "code": code, "name": new["name"],
                "from": old["rank"], "to": new["rank"], "delta": delta,
            })
    report["rank_changes"].sort(key=lambda row: (-abs(row["delta"]), row["code"]))
    report["state_changes"].sort(key=lambda row: (row["to"], row["code"]))
    lines = [f'{row["code"]} {row["name"]}：{STATE_LABELS.get(row["from"], row["from"])}（{row["from"]}） → {STATE_LABELS.get(row["to"], row["to"])}（{row["to"]}）'
             for row in report["state_changes"][:20]]
    lines += [f'{row["code"]} {row["name"]}：順位 {row["from"]}位 → {row["to"]}位'
              for row in report["rank_changes"][:10]]
    if added or removed:
        lines.append(f"比較対象の増減：追加 {len(added)}件／対象外 {len(removed)}件")
    report["summary_lines"] = lines or ["前回から目立った状態・順位変化はありません。"]
    return report


def briefing_summary(report: dict) -> dict:
    """Return the intentionally small subset embedded in Morning Brief."""
    return {
        "schema_version": report["schema_version"],
        "status": report["status"],
        "reason": report["reason"],
        "current_date": report["current_date"],
        "previous_date": report["previous_date"],
        "checked_at": report["checked_at"],
        "state_change_count": len(report["state_changes"]),
        "rank_change_count": len(report["rank_changes"]),
        "added_count": len(report["added_codes"]),
        "removed_count": len(report["removed_codes"]),
        "summary_lines": report["summary_lines"],
        "disclaimer": report["disclaimer"],
    }
