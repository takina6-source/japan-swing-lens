from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

import pandas as pd


FIELDS = ("basic_eps", "revenue", "operating_profit", "net_income")


def normalize_quarterly_records(records: list[dict]) -> list[dict]:
    """Normalize quarterly/YTD records without mixing them into Annual EPS.

    Revenue/profit YTD values may be converted to stand-alone quarters when the
    preceding cumulative period exists. EPS is deliberately left as YTD because
    subtracting weighted-average per-share figures is not accounting-safe.
    """
    cleaned = [_clean_record(row) for row in records]
    cleaned = [row for row in cleaned if row]
    by_source_year: dict[tuple[str, str], list[dict]] = {}
    for row in cleaned:
        by_source_year.setdefault((row["source"], row["fiscal_year"]), []).append(row)
    derived: list[dict] = []
    for rows in by_source_year.values():
        ytd = {quarter_number(row["fiscal_quarter"]): row for row in rows
               if row["period_type"] in ("YTD", "FY")}
        for quarter, current in sorted(ytd.items()):
            if quarter == 1:
                standalone = dict(current)
                standalone["period_type"] = "QUARTER"
                standalone["is_derived"] = 1
                derived.append(standalone)
                continue
            previous = ytd.get(quarter - 1)
            if not previous:
                continue
            standalone = dict(current)
            standalone["period_type"] = "QUARTER"
            standalone["is_derived"] = 1
            standalone["basic_eps"] = None
            standalone["field_diagnostics"] = dict(current.get("field_diagnostics") or {})
            standalone["field_diagnostics"]["basic_eps"] = {
                "status": "MISSING", "item_name": None, "reason": "YTD_EPS_NOT_DERIVED"}
            for field in ("revenue", "operating_profit", "net_income"):
                a, b = _number(current.get(field)), _number(previous.get(field))
                standalone[field] = a - b if a is not None and b is not None else None
                standalone["field_diagnostics"][field] = {
                    "status": "AVAILABLE" if standalone[field] is not None else "MISSING",
                    "item_name": None,
                    "reason": "DERIVED_FROM_YTD" if standalone[field] is not None
                    else "PRIOR_YTD_VALUE_MISSING",
                }
            derived.append(standalone)
    unique: dict[tuple, dict] = {}
    for row in [*cleaned, *derived]:
        key = (row["source"], row["period_end"], row["fiscal_quarter"],
               row["period_type"], int(row.get("is_derived", 0)))
        unique[key] = row
    return sorted(unique.values(), key=lambda row: (row["period_end"], row["source"], row["period_type"]))


def quarterly_profile(records: list[dict], cfg: dict, as_of: str | None = None) -> dict[str, Any]:
    c = cfg["free_data"]["quarterly_fundamentals"]
    earnings_cfg = cfg["experimental"]["earnings"]
    as_of_date = str(as_of or date.today().isoformat())[:10]
    eligible, future = [], 0
    for row in normalize_quarterly_records(records):
        available = str(row.get("published_date") or row.get("filing_date") or
                        row.get("retrieved_at") or "")[:10]
        if available and available <= as_of_date and row["period_end"] <= as_of_date:
            eligible.append(row)
        else:
            future += 1
    priority = {str(name).upper(): i for i, name in enumerate(c["source_priority"])}
    selected: dict[tuple[str, str], dict] = {}
    for row in eligible:
        key = (row["period_end"], row["period_type"])
        family = source_family(row.get("source"))
        rank = priority.get(family, 999)
        old = selected.get(key)
        if old is None or rank < old[0]:
            selected[key] = (rank, row)
    rows = sorted((item[1] for item in selected.values()), key=lambda row: row["period_end"])
    # Choose one internally comparable series. A complete higher-priority official
    # series wins; an incomplete source may fall through to a complete fallback.
    candidates = []
    families = sorted({source_family(row.get("source")) for row in rows},
                      key=lambda family: priority.get(family, 999))
    for family in families:
        family_rows = [row for row in rows if source_family(row.get("source")) == family]
        quarters = [row for row in family_rows if row["period_type"] == "QUARTER"
                    and not row.get("is_derived")]
        ytd = [row for row in family_rows if row["period_type"] in ("YTD", "FY")]
        candidate = quarters if quarters else ytd
        if candidate:
            candidates.append((priority.get(family, 999), candidate))
    complete = [item for item in candidates if len(item[1]) >= int(c["minimum_quarters"])]
    if complete:
        series = min(complete, key=lambda item: item[0])[1]
    else:
        series = max(candidates, key=lambda item: (len(item[1]), -item[0]))[1] if candidates else []
    latest = series[-1] if series else None
    previous_period = series[-2] if len(series) >= 2 else None
    latest_yoy = _period_growth(latest, _year_ago(latest, series), earnings_cfg) if latest else {}
    previous_yoy = (_period_growth(previous_period, _year_ago(previous_period, series), earnings_cfg)
                    if previous_period else {})
    turnaround = bool(latest_yoy.get("turnaround_flag"))
    anomaly = bool(latest_yoy.get("anomaly_flag") or previous_yoy.get("anomaly_flag"))
    eps = latest_yoy.get("basic_eps")
    sales = latest_yoy.get("revenue")
    operating = latest_yoy.get("operating_profit")
    previous_eps = previous_yoy.get("basic_eps")
    previous_sales = previous_yoy.get("revenue")
    acceleration = eps - previous_eps if eps is not None and previous_eps is not None else None
    sales_acceleration = sales - previous_sales if sales is not None and previous_sales is not None else None
    available = sum(value is not None for value in (eps, sales, operating, acceleration))
    coverage = available / 4 * 100
    missing = []
    for value, label in ((eps, "EPS_NOT_FOUND"), (sales, "SALES_NOT_FOUND"),
                         (operating, "OPERATING_PROFIT_NOT_FOUND"),
                         (acceleration, "EPS_ACCELERATION_NOT_AVAILABLE")):
        if value is None:
            missing.append(label)
    reasons = list(dict.fromkeys(missing))
    if len(series) < int(c["minimum_quarters"]):
        reasons.append("INSUFFICIENT_QUARTERS")
    if future:
        reasons.append("FUTURE_PUBLICATION_EXCLUDED")
    if latest and not latest.get("publication_date_known"):
        reasons.append("PUBLICATION_DATE_UNKNOWN")
    stale = bool(latest and (pd.Timestamp(as_of_date) - pd.Timestamp(latest["period_end"])).days
                 > int(c.get("max_statement_age_days", 200)))
    if stale:
        reasons.append("STALE_QUARTERLY_DATA")
    sources = sorted({source_family(row.get("source")) for row in series})
    fidelity = _lowest_fidelity([row.get("fidelity") for row in series])
    field_diagnostics = dict((latest or {}).get("field_diagnostics") or {})
    for field in FIELDS:
        field_diagnostics.setdefault(field, {
            "status": "AVAILABLE" if latest and latest.get(field) is not None else "MISSING",
            "item_name": None,
            "reason": None if latest and latest.get(field) is not None else "VALUE_NOT_AVAILABLE",
        })
    return {
        "records": series, "quarters_available": len(series), "coverage": coverage,
        "eps_growth": eps, "previous_eps_growth": previous_eps,
        "eps_acceleration": acceleration, "sales_growth": sales,
        "previous_sales_growth": previous_sales, "sales_acceleration": sales_acceleration,
        "operating_profit_growth": operating,
        "net_income_growth": latest_yoy.get("net_income"),
        "turnaround_flag": turnaround, "anomaly_flag": anomaly,
        "source": "+".join(sources) or "N/A", "fidelity": fidelity,
        "latest_period": latest.get("period_end") if latest else None,
        "fiscal_quarter": latest.get("fiscal_quarter") if latest else None,
        "period_type": latest.get("period_type") if latest else None,
        "published_date": (latest.get("published_date") or latest.get("filing_date")) if latest else None,
        "available_from": (latest.get("published_date") or latest.get("filing_date") or
                           latest.get("retrieved_at")) if latest else None,
        "publication_date_known": bool(latest and latest.get("publication_date_known")),
        "stale": stale,
        "missing": missing, "reason_codes": list(dict.fromkeys(reasons)),
        "field_diagnostics": field_diagnostics,
    }


def quarterly_diagnostic(code: str, profile: dict, attempted_sources: list[str], *,
                         update_state: str = "CURRENT", next_update_rank: int | None = None,
                         source_attempts: dict[str, str] | None = None,
                         additional_reasons: list[str] | None = None) -> dict:
    coverage = float(profile.get("coverage") or 0)
    return {
        "code": code,
        "status": "AVAILABLE" if coverage >= 50 else "PARTIAL" if coverage else "MISSING",
        "coverage": coverage, "quarters_available": int(profile.get("quarters_available") or 0),
        "source_summary": profile.get("source", "N/A"),
        "fidelity": profile.get("fidelity", "N/A"),
        "latest_period": profile.get("latest_period"),
        "published_date": profile.get("published_date"),
        "reason_codes": list(dict.fromkeys([*profile.get("reason_codes", []),
                                             *(additional_reasons or [])])),
        "attempted_sources": attempted_sources,
        "details": {"missing": profile.get("missing", []),
                    "period_type": profile.get("period_type"),
                    "available_from": profile.get("available_from"),
                    "field_diagnostics": profile.get("field_diagnostics", {}),
                    "update_state": update_state,
                    "next_update_rank": next_update_rank,
                    "source_attempts": source_attempts or {}},
    }


def source_family(source: str | None) -> str:
    text = str(source or "").upper()
    if "J-QUANTS" in text or "JQUANTS" in text:
        return "JQUANTS"
    if "EDINET" in text or "金融庁" in text:
        return "EDINET"
    if "YAHOO" in text:
        return "YAHOO"
    return text or "UNKNOWN"


def quarter_number(value: str | None) -> int:
    text = str(value or "").upper()
    if text == "FY":
        return 4
    for number in range(1, 5):
        if str(number) in text:
            return number
    return 0


def _clean_record(row: dict) -> dict | None:
    period_end = _date_text(row.get("period_end"))
    if not period_end:
        return None
    quarter = str(row.get("fiscal_quarter") or f"Q{pd.Timestamp(period_end).quarter}").upper()
    fiscal_year = str(row.get("fiscal_year") or pd.Timestamp(period_end).year)
    out = {
        "code": str(row.get("code") or ""), "fiscal_year": fiscal_year,
        "fiscal_quarter": quarter, "period_start": _date_text(row.get("period_start")),
        "period_end": period_end, "filing_date": _date_text(row.get("filing_date")),
        "published_date": _date_text(row.get("published_date")),
        "retrieved_at": _date_text(row.get("retrieved_at")) or date.today().isoformat(),
        "source": str(row.get("source") or "UNKNOWN"),
        "fidelity": str(row.get("fidelity") or "PROXY").upper(),
        "period_type": str(row.get("period_type") or "QUARTER").upper(),
        "publication_date_known": int(bool(row.get("publication_date_known") or
                                           row.get("published_date") or row.get("filing_date"))),
        "is_derived": int(bool(row.get("is_derived"))),
        "company_forecast": int(bool(row.get("company_forecast"))),
    }
    field_diagnostics = row.get("field_diagnostics")
    if not field_diagnostics and row.get("field_diagnostics_json"):
        try:
            field_diagnostics = json.loads(row["field_diagnostics_json"])
        except (TypeError, json.JSONDecodeError):
            field_diagnostics = {}
    out["field_diagnostics"] = field_diagnostics or {}
    for field in FIELDS:
        out[field] = _number(row.get(field))
    if all(out[field] is None for field in FIELDS):
        return None
    return out


def _year_ago(current: dict | None, rows: list[dict]) -> dict | None:
    if not current:
        return None
    current_date = pd.Timestamp(current["period_end"])
    candidates = []
    for row in rows:
        if row is current or row["period_type"] != current["period_type"]:
            continue
        days = (current_date - pd.Timestamp(row["period_end"])).days
        if 300 <= days <= 430:
            candidates.append((abs(days - 365), row))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _period_growth(current: dict | None, previous: dict | None, cfg: dict) -> dict:
    if not current or not previous:
        return {field: None for field in FIELDS}
    output: dict[str, Any] = {}
    turnaround = anomaly = False
    for field in FIELDS:
        before, now = _number(previous.get(field)), _number(current.get(field))
        value, field_turnaround, field_anomaly = _growth(before, now, field, cfg)
        output[field] = value
        turnaround = turnaround or field_turnaround
        anomaly = anomaly or field_anomaly
    output["turnaround_flag"] = turnaround
    output["anomaly_flag"] = anomaly
    return output


def _growth(previous: float | None, current: float | None, field: str, cfg: dict):
    if previous is None or current is None:
        return None, False, False
    if previous <= 0 < current:
        return None, field == "basic_eps", False
    near_zero = float(cfg.get("near_zero_eps", 0.5)) if field == "basic_eps" else 0.0
    if abs(previous) <= near_zero:
        return None, False, field == "basic_eps"
    value = (current / abs(previous) - 1) * 100
    anomaly = abs(value) > float(cfg["anomaly_growth_pct"])
    return (None if anomaly else value), False, anomaly


def _lowest_fidelity(values: list[str | None]) -> str:
    order = {"STRICT": 0, "PRACTICAL": 1, "PROXY": 2, "N/A": 3}
    normalized = [str(value or "N/A").upper() for value in values]
    return max(normalized, key=lambda value: order.get(value, 3)) if normalized else "N/A"


def _date_text(value) -> str | None:
    if value is None or value == "":
        return None
    try:
        return str(pd.Timestamp(value).date())
    except (TypeError, ValueError):
        return None


def _number(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None
