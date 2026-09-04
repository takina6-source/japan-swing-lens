from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any


DEFAULT_COOLDOWN_DAYS = {
    "SUCCESS": 7,
    "TRANSIENT_FAILURE": 1,
    "PERMANENT_FAILURE": 30,
    "CONFIGURATION_REQUIRED": 30,
    "INSUFFICIENT_DATA": 7,
}


def classify_attempt_outcome(reason_code: str | None) -> str:
    reason = str(reason_code or "UNKNOWN_ERROR").upper()
    if reason == "SUCCESS":
        return "SUCCESS"
    if reason.endswith("_NOT_CONFIGURED") or reason.endswith("_AUTH_ERROR"):
        return "CONFIGURATION_REQUIRED"
    if reason.endswith("_PARSE_ERROR") or reason.endswith("_NOT_AVAILABLE"):
        return "PERMANENT_FAILURE"
    if (reason.endswith("_NO_DATA") or "INSUFFICIENT" in reason
            or reason == "INSUFFICIENT_TOTAL_YEARS"):
        return "INSUFFICIENT_DATA"
    return "TRANSIENT_FAILURE"


def next_eligible_at(attempted_at: str, outcome: str,
                     cooldown_days: dict[str, int] | None = None) -> str:
    cooldowns = {**DEFAULT_COOLDOWN_DAYS, **(cooldown_days or {})}
    start = _date(attempted_at) or date.today()
    return (start + timedelta(days=int(cooldowns.get(outcome, 1)))).isoformat()


def attempt_record(code: str, data_kind: str, source: str, reason_code: str,
                   attempted_at: str | None = None,
                   cooldown_days: dict[str, int] | None = None) -> dict[str, Any]:
    attempted = attempted_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    outcome = classify_attempt_outcome(reason_code)
    return {
        "code": str(code), "data_kind": str(data_kind).upper(),
        "source": str(source).upper(), "last_attempt_at": attempted,
        "outcome": outcome, "reason_code": str(reason_code).upper(),
        "next_eligible_at": next_eligible_at(attempted, outcome, cooldown_days),
    }


def build_fair_update_queue(
        codes: list[str], attempts_by_code: dict[str, list[dict]],
        availability: dict[str, int], momentum: dict[str, float], limit: int,
        sources_by_code: dict[str, list[str]], today: str | None = None,
        ) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Order refreshes by fairness; momentum only breaks otherwise equal ties."""
    current = _date(today) or date.today()
    eligible, metadata = [], {}
    for code in codes:
        sources = [str(value).upper() for value in sources_by_code.get(code, [])]
        records = [row for row in attempts_by_code.get(code, [])
                   if not sources or str(row.get("source") or "").upper() in sources]
        by_source = {str(row.get("source") or "").upper(): row for row in records}
        never_sources = [source for source in sources if source not in by_source]
        due_sources = [source for source in sources if source not in by_source or
                       (_date(by_source[source].get("next_eligible_at")) or date.min) <= current]
        last_attempt = max((str(row.get("last_attempt_at") or "") for row in records),
                           default="") or None
        future_dates = sorted(str(row.get("next_eligible_at"))[:10] for row in records
                              if _date(row.get("next_eligible_at")) and
                              _date(row.get("next_eligible_at")) > current)
        next_date = current.isoformat() if due_sources else (future_dates[0] if future_dates else None)
        never = bool(never_sources) or not records
        if due_sources:
            queue_reason = "NEVER_ATTEMPTED" if never else (
                "OLDEST_ATTEMPT" if last_attempt else "DATA_INSUFFICIENT")
            eligible.append((code, never, last_attempt, int(availability.get(code, 0)),
                             float(momentum.get(code, 0))))
            state = "ELIGIBLE"
        else:
            queue_reason, state = "RETRY_COOLDOWN", "COOLDOWN"
        metadata[code] = {
            "update_state": state, "queue_reason": queue_reason,
            "last_attempt_at": last_attempt, "next_eligible_at": next_date,
            "eligible_sources": due_sources, "never_attempted_sources": never_sources,
            "next_update_rank": None,
        }
    eligible.sort(key=lambda item: (
        0 if item[1] else 1,
        item[2] or "",
        item[3],
        -item[4],
        item[0],
    ))
    ordered = [item[0] for item in eligible]
    for rank, code in enumerate(ordered, 1):
        metadata[code]["update_state"] = "SELECTED" if rank <= limit else "QUEUED_UPDATE_LIMIT"
        metadata[code]["next_update_rank"] = None if rank <= limit else rank
        if rank > limit:
            metadata[code]["queue_reason"] = "UPDATE_LIMIT"
    return ordered, metadata


def _date(value: Any) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
