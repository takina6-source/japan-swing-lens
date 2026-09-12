"""Read-only adapter from existing Core artifacts to minimal Phase 2A facts."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .ids import hash_payload, observation_uid, run_uid
from .models import CoreObservation, ObservationStatus, PivotFact, validate_code


TREND_STRATEGIES = ("Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas")
ACTIVE_TREND_STATES = frozenset({"SETUP FORMING", "BREAKOUT WATCH", "BREAKOUT"})


@dataclass(frozen=True)
class AdaptedRun:
    run_id: str
    state_lineage: str
    scope_name: str
    expected_market_date: str
    market_session_index: int
    generated_at: str
    scope_members: tuple[dict[str, Any], ...]
    observations: tuple[CoreObservation, ...]
    input_sha256: str
    scope_member_sha256: str
    source_versions: dict[str, str]
    upstream_errors: tuple[dict[str, Any], ...]


def _positive(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _strategy_slug(name: str) -> str:
    return {
        "Minervini": "minervini",
        "Qullamaggie": "qullamaggie",
        "CAN SLIM": "can_slim",
        "Weinstein": "weinstein",
        "Darvas": "darvas",
    }[name]


def _primary_pivot(detail: Mapping[str, Any]) -> PivotFact | None:
    price = detail.get("price")
    strategies = detail.get("strategies") or {}
    candidates: list[tuple[float, int, str, Mapping[str, Any]]] = []
    for order, name in enumerate(TREND_STRATEGIES):
        row = strategies.get(name) or {}
        pivot = row.get("pivot")
        if _positive(pivot):
            distance = abs(float(pivot) - float(price)) if _positive(price) else 0.0
            candidates.append((distance, order, name, row))
    if not candidates:
        top = detail.get("pivot")
        if not _positive(top):
            return None
        return PivotFact(
            price=float(top), strategy="core-primary",
            pivot_type=str(detail.get("pivot_type") or "N/A"),
            basis=str(detail.get("pivot_basis") or "N/A"),
            fidelity=str(detail.get("pivot_fidelity") or "PROXY"),
            reference_date=None,
        )
    _, _, name, row = min(candidates)
    return PivotFact(
        price=float(row["pivot"]), strategy=_strategy_slug(name),
        pivot_type=str(row.get("pivot_type") or "N/A"),
        basis=str(row.get("pivot_basis") or "N/A"),
        fidelity=str(row.get("pivot_fidelity") or "PROXY"),
        reference_date=row.get("pivot_formed_date"),
    )


def _status_for(
    scope_row: Mapping[str, Any], detail: Mapping[str, Any] | None,
    expected_market_date: str,
) -> tuple[ObservationStatus, tuple[str, ...]]:
    explicit = scope_row.get("observation_status")
    if explicit:
        status = ObservationStatus(str(explicit))
        return status, tuple(scope_row.get("reason_codes") or (status.value,))
    required = int(scope_row.get("required_price_history_count") or 200)
    history = scope_row.get("price_history_count")
    if history is not None and int(history) < required:
        return ObservationStatus.INSUFFICIENT_PRICE_HISTORY, ("PRICE_HISTORY_BELOW_MINIMUM",)
    if detail is None:
        return ObservationStatus.ANALYSIS_FAILED, ("CORE_DETAIL_NOT_FOUND",)
    as_of = detail.get("as_of")
    if not as_of:
        return ObservationStatus.INVALID_INPUT, ("ANALYSIS_DATE_MISSING",)
    if str(as_of) != expected_market_date:
        return ObservationStatus.STALE_MARKET_DATE, ("STALE_MARKET_DATE",)
    if not _positive(detail.get("price")):
        return ObservationStatus.INVALID_INPUT, ("INVALID_CLOSE",)
    try:
        primary = _primary_pivot(detail)
    except ValueError:
        primary = None
    if primary is None:
        return ObservationStatus.INVALID_INPUT, ("PRIMARY_PIVOT_UNAVAILABLE",)
    if not primary.reference_date:
        return ObservationStatus.INVALID_INPUT, ("PIVOT_REFERENCE_DATE_MISSING",)
    if primary.reference_date >= expected_market_date:
        return ObservationStatus.INVALID_INPUT, ("PIVOT_LOOKAHEAD_OR_SAME_DAY_EVIDENCE",)
    return ObservationStatus.CURRENT, ()


def adapt_core_artifacts(
    *,
    snapshot: Mapping[str, Any],
    details_by_code: Mapping[str, Mapping[str, Any]],
    scope_members: Sequence[Mapping[str, Any]],
    expected_market_date: str,
    market_session_index: int,
    state_lineage: str = "live-sm1",
    previous_accepted_close_by_code: Mapping[str, float] | None = None,
    config_sha256: str = "",
) -> AdaptedRun:
    """Build one full-scope run without mutating Core or its database."""

    if market_session_index < 0:
        raise ValueError("market_session_index must be non-negative")
    previous = previous_accepted_close_by_code or {}
    generated_at = str(snapshot.get("generated_at") or "")
    scope_name = str(snapshot.get("scope") or "UNKNOWN")
    source_versions = {
        "logic_version": str(snapshot.get("logic_version") or "UNKNOWN"),
        "config_sha256": config_sha256 or "UNKNOWN",
        "adapter_version": "phase2a-adapter-v1",
    }

    normalized_scope: list[dict[str, Any]] = []
    observations: list[CoreObservation] = []
    seen_codes: set[str] = set()
    for raw in sorted(scope_members, key=lambda row: str(row["code"])):
        code = str(raw["code"])
        validate_code(code)
        if code in seen_codes:
            raise ValueError(f"duplicate scope member: {code}")
        seen_codes.add(code)
        in_scope = bool(raw.get("in_scope", True))
        detail = details_by_code.get(code)
        status, reasons = _status_for(raw, detail, expected_market_date)
        if not in_scope:
            status = ObservationStatus.OUT_OF_SCOPE
            reasons = tuple(raw.get("reason_codes") or ("OUT_OF_SCOPE",))
        scope_row = {
            "code": code,
            "in_scope": in_scope,
            "observation_status": status.value,
            "analysis_date": str(detail.get("as_of")) if detail and detail.get("as_of") else None,
            "latest_price_date": raw.get("latest_price_date") or (
                str(detail.get("as_of")) if detail and detail.get("as_of") else None
            ),
            "price_history_count": raw.get("price_history_count"),
            "required_price_history_count": int(raw.get("required_price_history_count") or 200),
            "reason_codes": list(reasons),
            "structured_error_ref": raw.get("structured_error_ref"),
            "observation_uid": None,
        }
        normalized_scope.append(scope_row)
        if status != ObservationStatus.CURRENT or detail is None:
            continue

        methods = detail.get("methods") or {}
        trend_states = {name: str(methods.get(name) or "NOT QUALIFIED") for name in TREND_STRATEGIES}
        aligned = sum(value in ACTIVE_TREND_STATES for value in trend_states.values())
        breakouts = sum(value == "BREAKOUT" for value in trend_states.values())
        primary = _primary_pivot(detail)
        assert primary is not None
        legacy_strategy_ids = {
            _strategy_slug(name): row.get("setup_id")
            for name in TREND_STRATEGIES
            if (row := (detail.get("strategies") or {}).get(name) or {}).get("setup_id")
        }
        prior_close = previous.get(code)
        if prior_close is None:
            prior_rows = [
                row for row in (detail.get("chart") or [])
                if str(row.get("date") or "") < expected_market_date and _positive(row.get("close"))
            ]
            if prior_rows:
                prior_close = float(max(prior_rows, key=lambda row: str(row["date"]))["close"])
        fact = {
            "code": code,
            "analysis_date": expected_market_date,
            "expected_market_date": expected_market_date,
            "market_session_index": market_session_index,
            "close": float(detail["price"]),
            "previous_accepted_close": prior_close,
            "core_observed_state": str(detail.get("state") or "NOT QUALIFIED"),
            "trend_strategy_states": trend_states,
            "connors_state": str(methods.get("Connors") or "NOT QUALIFIED"),
            "aligned_trend_strategy_count": aligned,
            "breakout_trend_strategy_count": breakouts,
            "observed_primary_pivot": {
                "price": primary.price,
                "strategy": primary.strategy,
                "pivot_type": primary.pivot_type,
                "basis": primary.basis,
                "fidelity": primary.fidelity,
                "reference_date": primary.reference_date,
            },
            "source_versions": source_versions,
            "legacy_refs": {
                "consensus_setup_id": detail.get("setup_id"),
                "strategy_setup_ids": legacy_strategy_ids,
            },
        }
        declared_aligned = detail.get("aligned_strategy_count")
        declared_breakouts = detail.get("breakout_strategy_count")
        reason_codes: list[str] = []
        if declared_aligned is not None and int(declared_aligned) != aligned:
            reason_codes.append("DECLARED_ALIGNED_COUNT_RECOMPUTED")
        if declared_breakouts is not None and int(declared_breakouts) != breakouts:
            reason_codes.append("DECLARED_BREAKOUT_COUNT_RECOMPUTED")
        input_sha = hash_payload(fact)
        observations.append(CoreObservation(
            code=code, analysis_date=expected_market_date,
            expected_market_date=expected_market_date,
            market_session_index=market_session_index,
            status=ObservationStatus.CURRENT,
            close=float(detail["price"]),
            previous_accepted_close=prior_close,
            core_observed_state=str(detail.get("state") or "NOT QUALIFIED"),
            aligned_trend_strategy_count=aligned,
            breakout_trend_strategy_count=breakouts,
            observed_primary_pivot=primary,
            trend_strategy_states=trend_states,
            connors_state=str(methods.get("Connors") or "NOT QUALIFIED"),
            reason_codes=tuple(reason_codes), input_sha256=input_sha,
            observed_at=generated_at, source_versions=source_versions,
            legacy_refs=fact["legacy_refs"], history_count=raw.get("price_history_count"),
        ))

    scope_hash = hash_payload([
        {"code": row["code"], "in_scope": row["in_scope"]} for row in normalized_scope
    ])
    run_input = {
        "expected_market_date": expected_market_date,
        "market_session_index": market_session_index,
        "scope_member_sha256": scope_hash,
        "source_versions": source_versions,
        "scope_statuses": [
            {"code": row["code"], "status": row["observation_status"]}
            for row in normalized_scope
        ],
        "observation_hashes": [obs.input_sha256 for obs in observations],
    }
    input_sha = hash_payload(run_input)
    rid = run_uid(expected_market_date, input_sha, state_lineage)
    uid_by_code = {obs.code: observation_uid(obs.code, rid, obs.input_sha256) for obs in observations}
    with_uids = tuple(
        {**row, "observation_uid": uid_by_code.get(row["code"])} for row in normalized_scope
    )
    upstream_errors_raw = snapshot.get("structured_errors") or ()
    upstream_errors = tuple(dict(item) for item in upstream_errors_raw if isinstance(item, Mapping))
    return AdaptedRun(
        run_id=rid, state_lineage=state_lineage, scope_name=scope_name,
        expected_market_date=expected_market_date,
        market_session_index=market_session_index, generated_at=generated_at,
        scope_members=with_uids, observations=tuple(observations), input_sha256=input_sha,
        scope_member_sha256=scope_hash, source_versions=source_versions,
        upstream_errors=upstream_errors,
    )
