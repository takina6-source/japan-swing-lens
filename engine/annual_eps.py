from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any


SOURCE_PRIORITY = {
    "EDINET_STANDARD": 10,
    "EDINET_EXTENSION": 20,
    "EDINET_DERIVED": 30,
    "JQUANTS": 40,
    "YAHOO": 50,
}

REASON_CODES = {
    "EDINET_NOT_FOUND", "EDINET_DOCUMENT_NOT_FOUND", "EDINET_XBRL_NOT_AVAILABLE",
    "EDINET_EPS_TAG_NOT_FOUND", "EDINET_INSUFFICIENT_YEARS", "EDINET_PARSE_ERROR",
    "JQUANTS_NOT_CONFIGURED", "JQUANTS_AUTH_ERROR", "JQUANTS_API_ERROR",
    "JQUANTS_NO_DATA", "JQUANTS_PARSE_ERROR", "JQUANTS_NOT_AVAILABLE",
    "JQUANTS_INSUFFICIENT_HISTORY", "YAHOO_API_ERROR", "YAHOO_NO_DATA",
    "YAHOO_NOT_AVAILABLE", "YAHOO_PARSE_ERROR",
    "INSUFFICIENT_TOTAL_YEARS", "DATA_CONFLICT", "INVALID_EPS_VALUE", "UNKNOWN_ERROR",
    "UPDATE_LIMIT_NOT_ATTEMPTED",
}


class SourceFetchError(RuntimeError):
    """Provider boundary error carrying only a safe, non-secret reason code."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code if reason_code in REASON_CODES else "UNKNOWN_ERROR"


def source_family(source: str | None) -> str:
    text = str(source or "").upper()
    if "EDINET_STANDARD" in text:
        return "EDINET_STANDARD"
    if "EDINET_EXTENSION" in text:
        return "EDINET_EXTENSION"
    if "EDINET_DERIVED" in text:
        return "EDINET_DERIVED"
    if "EDINET" in text:
        return "EDINET_STANDARD"
    if "J-QUANTS" in text or "JQUANTS" in text:
        return "JQUANTS"
    if "YAHOO" in text:
        return "YAHOO"
    return "UNKNOWN"


def normalize_record(row: dict[str, Any], as_of: str | None = None) -> dict[str, Any] | None:
    try:
        year = str(row.get("fiscal_year") or row.get("year") or "")[:4]
        eps = float(row.get("eps"))
    except (TypeError, ValueError):
        return None
    period = str(row.get("period_type") or "FY").upper()
    if not (year.isdigit() and len(year) == 4 and math.isfinite(eps)):
        return None
    if any(token in period for token in ("Q1", "Q2", "Q3", "QUARTER", "INTERIM", "HY")):
        return None
    family = source_family(row.get("source"))
    if family == "UNKNOWN":
        return None
    result = dict(row)
    result.update({
        "fiscal_year": year,
        "eps": eps,
        "source_family": family,
        "priority": int(row.get("priority") or SOURCE_PRIORITY[family]),
        "period_type": "FY",
        "published_date": row.get("published_date") or row.get("filing_date"),
        "retrieved_at": row.get("retrieved_at") or row.get("updated_at"),
    })
    if as_of:
        cutoff = str(as_of)[:10]
        published = str(result.get("published_date") or "")[:10]
        # 公表日が分かるSourceは公表日で、Yahoo等は少なくとも年度でFutureを除外する。
        if published and published > cutoff:
            return None
        if not published and year > cutoff[:4]:
            return None
    return result


def merge_annual_eps(rows: list[dict[str, Any]], conflict_pct: float = 10.0,
                     as_of: str | None = None
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """年度単位で最優先ソースを採用し、値の不一致は診断として残す。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid = 0
    for row in rows:
        if as_of:
            cutoff = str(as_of)[:10]
            published = str(row.get("published_date") or row.get("filing_date") or "")[:10]
            year = str(row.get("fiscal_year") or row.get("year") or "")[:4]
            if (published and published > cutoff) or (not published and year > cutoff[:4]):
                continue
        normalized = normalize_record(row, as_of)
        if normalized is None:
            invalid += 1
        else:
            grouped[normalized["fiscal_year"]].append(normalized)
    selected, diagnostics = [], []
    if invalid:
        diagnostics.append({"reason_code": "INVALID_EPS_VALUE", "count": invalid})
    for year, candidates in sorted(grouped.items()):
        # 同じ優先度なら訂正開示を含む新しい公表値を採用する。
        candidates.sort(key=lambda x: str(x.get("published_date") or ""), reverse=True)
        candidates.sort(key=lambda x: x["priority"])
        chosen = candidates[0]
        selected.append(chosen)
        for other in candidates[1:]:
            denominator = max(abs(chosen["eps"]), abs(other["eps"]), 1e-9)
            difference = abs(chosen["eps"] - other["eps"]) / denominator * 100
            if difference > conflict_pct:
                diagnostics.append({
                    "reason_code": "DATA_CONFLICT", "fiscal_year": year,
                    "selected_source": chosen["source_family"],
                    "selected_eps": chosen["eps"], "other_source": other["source_family"],
                    "other_eps": other["eps"], "difference_pct": round(difference, 2),
                })
    return selected, diagnostics


def annual_eps_profile(rows: list[dict[str, Any]], minimum_years: int = 3,
                       preferred_years: int = 4, conflict_pct: float = 10.0,
                       attempted_reasons: list[str] | None = None,
                       as_of: str | None = None) -> dict[str, Any]:
    selected, conflicts = merge_annual_eps(rows, conflict_pct, as_of)
    families = [row["source_family"] for row in selected]
    years = len(selected)
    if years >= preferred_years:
        status = "COMPLETE"
    elif years >= minimum_years:
        status = "PARTIAL"
    elif years:
        status = "INSUFFICIENT"
    else:
        status = "FAILED"
    if status == "FAILED":
        fidelity = "N/A"
    elif conflicts:
        fidelity = "LOW_CONFIDENCE"
    elif families and all(x == "EDINET_STANDARD" for x in families) and years >= preferred_years:
        fidelity = "STRICT"
    elif any(x in {"YAHOO", "EDINET_DERIVED"} for x in families):
        fidelity = "PRACTICAL" if years >= minimum_years else "PARTIAL"
    else:
        fidelity = "PRACTICAL" if years >= minimum_years else "PARTIAL"
    reasons = [x for x in (attempted_reasons or []) if x in REASON_CODES]
    reasons.extend(x["reason_code"] for x in conflicts)
    if years < minimum_years:
        reasons.append("INSUFFICIENT_TOTAL_YEARS")
    primary_reason = ("DATA_CONFLICT" if conflicts else
                      (reasons[0] if reasons and years < minimum_years else None))
    counts = Counter(families)
    return {
        "records": selected,
        "status": status,
        "fidelity": fidelity,
        "years_available": years,
        "minimum_years": minimum_years,
        "preferred_years": preferred_years,
        "sources": dict(counts),
        "source_summary": " + ".join(f"{k} {v}期" for k, v in counts.items()) or "N/A",
        "fallback_used": len(counts) > 1 or bool(counts and next(iter(counts)) != "EDINET_STANDARD"),
        "reason_code": primary_reason,
        "reason_codes": list(dict.fromkeys(reasons)),
        "conflicts": conflicts,
    }


def diagnostic_row(code: str, profile: dict[str, Any], initial_years: int,
                   attempted: list[str] | None = None, *, update_state: str = "CURRENT",
                   next_update_rank: int | None = None,
                   source_attempts: dict[str, str] | None = None,
                   queue_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    queue_details = dict(queue_metadata or {})
    queue_details.setdefault("update_state", update_state)
    queue_details.setdefault("next_update_rank", next_update_rank)
    return {
        "code": code,
        "status": profile["status"],
        "fidelity": profile["fidelity"],
        "years_available": profile["years_available"],
        "initial_years": initial_years,
        "source_summary": profile["source_summary"],
        "fallback_used": int(profile["fallback_used"]),
        "reason_code": profile.get("reason_code"),
        "reason_codes": profile.get("reason_codes", []),
        "attempted_sources": attempted or [],
        "details": {
            "conflicts": profile.get("conflicts", []),
            **queue_details,
            "source_attempts": source_attempts or {},
            "selected_years": [{
                "fiscal_year": row.get("fiscal_year"),
                "source": row.get("source_family") or source_family(row.get("source")),
                "fidelity": row.get("fidelity") or "N/A",
                "retrieved_at": row.get("retrieved_at"),
            } for row in profile.get("records", [])],
        },
        "diagnosed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def resolve_with_fallback(
        rows: list[dict[str, Any]],
        fetchers: list[tuple[str, Callable[[], list[dict[str, Any]]]]],
        minimum_years: int = 3, preferred_years: int = 4,
        conflict_pct: float = 10.0, initial_reasons: list[str] | None = None,
        as_of: str | None = None) -> dict[str, Any]:
    """不足時だけ次Sourceを呼ぶ、ネットワーク非依存で試験可能な制御層。"""
    combined = list(rows)
    added: list[dict[str, Any]] = []
    attempts: list[str] = []
    reasons = list(initial_reasons or [])
    errors: list[str] = []
    source_attempts: dict[str, str] = {}
    for name, fetch in fetchers:
        profile = annual_eps_profile(combined, minimum_years, preferred_years,
                                     conflict_pct, reasons, as_of)
        if profile["years_available"] >= minimum_years:
            break
        attempts.append(name)
        try:
            fetched = fetch() or []
            valid = [item for item in fetched if normalize_record(item, as_of) is not None]
            combined.extend(valid)
            added.extend(valid)
            current = annual_eps_profile(combined, minimum_years, preferred_years,
                                         conflict_pct, reasons, as_of)
            if not fetched:
                reason = "JQUANTS_NO_DATA" if name == "JQUANTS" else "YAHOO_NO_DATA"
                reasons.append(reason)
                source_attempts[name] = reason
            elif not valid:
                reason = "JQUANTS_PARSE_ERROR" if name == "JQUANTS" else "YAHOO_PARSE_ERROR"
                reasons.append(reason)
                source_attempts[name] = reason
            elif current["years_available"] < minimum_years and name == "JQUANTS":
                reasons.append("JQUANTS_INSUFFICIENT_HISTORY")
                source_attempts[name] = "JQUANTS_INSUFFICIENT_HISTORY"
            elif current["years_available"] < minimum_years:
                source_attempts[name] = "INSUFFICIENT_TOTAL_YEARS"
            else:
                source_attempts[name] = "SUCCESS"
        except Exception as exc:
            reason = classify_source_error(name, exc)
            reasons.append(reason)
            source_attempts[name] = reason
            # Do not propagate response bodies, credentials, or provider exception text.
            errors.append(f"{name}: {reason}")
    profile = annual_eps_profile(combined, minimum_years, preferred_years,
                                 conflict_pct, reasons, as_of)
    return {"profile": profile, "added_records": added, "attempted_sources": attempts,
            "source_attempts": source_attempts,
            "reason_codes": list(dict.fromkeys(reasons)), "errors": errors}


def classify_source_error(name: str, exc: Exception) -> str:
    explicit = getattr(exc, "reason_code", None)
    if explicit in REASON_CODES:
        return explicit
    message = str(exc).lower()
    source = str(name).upper()
    if source == "JQUANTS":
        if any(token in message for token in ("auth", "401", "403", "api key", "unauthorized")):
            return "JQUANTS_AUTH_ERROR"
        if any(token in message for token in ("parse", "column", "schema", "format")):
            return "JQUANTS_PARSE_ERROR"
        return "JQUANTS_API_ERROR"
    if any(token in message for token in ("parse", "column", "schema", "format")):
        return "YAHOO_PARSE_ERROR"
    return "YAHOO_API_ERROR"


def update_queue_metadata(codes: list[str], limit: int) -> dict[str, dict[str, Any]]:
    """Describe capped refresh work without pretending queued stocks were attempted."""
    return {
        code: {
            "update_state": "SELECTED" if rank <= limit else "QUEUED_UPDATE_LIMIT",
            "next_update_rank": None if rank <= limit else rank,
        }
        for rank, code in enumerate(codes, 1)
    }
