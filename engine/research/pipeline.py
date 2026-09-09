from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ..controls import (deterministic_random_codes, matching_distance)
from ..database import Database
from ..models import SetupState
from ..validation_engine import path_metrics, price_on_or_before
from .entry import simulate_entry_methods
from .gates import (candidate_gate, checkpoint_gate, family_correction,
                    final_decision)
from .models import ValidationSubject
from .registry import (CONTROL_SELECTION_VERSION, FAMILY_CLOSE_AT, FAMILY_ID,
                       FAMILY_VERSION, FREEZE_DATE, HOLDOUT_START,
                       RESEARCH_LOGIC_VERSION, RESEARCH_SCHEMA_VERSION,
                       VALIDATION_ENGINE_VERSION, cost_model, family_registry,
                       hypothesis_registry, now_jst, validate_family_sync)
from .statistics import (cluster_bootstrap_statistic, normal_two_sided_p,
                         tost_p, wilson_interval)
from .storage import ResearchStore


def seed_research(db: Database, base_url: str | None) -> bool:
    if not base_url:
        return False
    try:
        response = requests.get(base_url.rstrip("/") + "/state.json", timeout=30)
        if response.status_code != 200:
            return False
        ResearchStore(db).import_state(response.json())
        return True
    except Exception:
        return False


def run_research(db: Database, analyses: list, frames: dict[str, pd.DataFrame],
                 benchmark: pd.DataFrame, security_meta: dict[str, dict], cfg: dict,
                 *, workflow_started: float | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    store = ResearchStore(db)
    hypotheses = hypothesis_registry()
    family = family_registry()
    validate_family_sync(hypotheses, family)
    store.register_hypotheses(hypotheses)
    store.register_family(family)

    snapshots, histories = db.validation_rows()
    subjects = [_core_subject(row, security_meta) for row in snapshots]
    events, entry_subjects = _research_events(snapshots, histories, frames, benchmark,
                                               security_meta, cfg)
    new_events = store.save_events(events)
    new_subjects = store.save_subjects([subject.to_dict() for subject in
                                        [*subjects, *entry_subjects]])

    matching_started = time.perf_counter()
    controls_created = _create_research_controls(store, analyses, frames, security_meta, cfg)
    _track_research(store, frames, benchmark, cfg)
    matching_seconds = time.perf_counter() - matching_started
    compacted_rows = store.compact_matured_history()

    evidence = _evidence(db, store, hypotheses)
    checkpoint_rows = _update_checkpoints(store, hypotheses, evidence)
    market_dates = [str(frame.index[-1].date()) for frame in frames.values()
                    if frame is not None and not frame.empty]
    benchmark_dates = ([str(value.date()) for value in benchmark.index]
                       if benchmark is not None and not benchmark.empty else [])
    holdout_trading_dates = sorted({value for value in benchmark_dates
                                    if HOLDOUT_START <= value <= FAMILY_CLOSE_AT})
    trading_session_progress = {
        "elapsed": len(holdout_trading_dates),
        "total": 90,
        "source": "BENCHMARK_TRADING_DATES",
        "latest_market_date": max(benchmark_dates) if benchmark_dates else None,
    }
    family_result = _maybe_close_family(store, hypotheses, family,
                                        max(market_dates) if market_dates else "")
    research_seconds = time.perf_counter() - started
    workflow_seconds = ((time.perf_counter() - workflow_started)
                        if workflow_started is not None else research_seconds)
    performance = {
        "new_research_events": new_events,
        "new_validation_subjects": new_subjects,
        "control_groups_created": controls_created,
        "matching_candidates": len(analyses),
        "matching_seconds": matching_seconds,
        "research_control_seconds": matching_seconds,
        "research_total_seconds": research_seconds,
        "workflow_total_seconds": workflow_seconds,
        "research_share_pct": (research_seconds / workflow_seconds * 100
                               if workflow_seconds > 0 else 100.0),
    }
    performance["warning"] = bool(
        performance["research_share_pct"] > 20 or matching_seconds > 60)
    storage = _storage_metrics(db, store, compacted_rows)
    run_id = f"{datetime.now(ZoneInfo('Asia/Tokyo')).date()}:{RESEARCH_LOGIC_VERSION}"
    store.save_telemetry({"run_id": run_id, "run_date": str(datetime.now(
        ZoneInfo("Asia/Tokyo")).date()), "performance": performance, "storage": storage})
    return {"hypotheses": hypotheses, "family": family, "evidence": evidence,
            "checkpoints": checkpoint_rows, "family_result": family_result,
            "performance_metrics": performance, "storage_metrics": storage,
            "trading_session_progress": trading_session_progress}


def export_research(db: Database, output: Path, cfg: dict,
                    run_result: dict[str, Any] | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    store = ResearchStore(db)
    hypotheses = run_result.get("hypotheses") if run_result else hypothesis_registry()
    family = run_result.get("family") if run_result else family_registry()
    evidence = run_result.get("evidence") if run_result else _evidence(db, store, hypotheses)
    checkpoint_rows = [_clean_row(row) for row in store.rows(
        "research_checkpoint_evaluations", "hypothesis_version,checkpoint_name")]
    family_results = store.rows("research_family_results", "family_id,family_version")
    final_payload = family_results[-1].get("payload") if family_results else None
    formal_by_hypothesis = {row["hypothesis_version"]: _clean_row(row)
                            for row in store.rows("research_results", "hypothesis_version")}
    checkpoint_by_hypothesis: dict[str, list[dict]] = defaultdict(list)
    for row in checkpoint_rows:
        checkpoint_by_hypothesis[row["hypothesis_version"]].append(row)
    hypothesis_exports = []
    for hypothesis in hypotheses:
        version = hypothesis["hypothesis_version"]
        final = ((final_payload or {}).get("members") or {}).get(version)
        hypothesis_exports.append({
            **hypothesis,
            "progress": {
                "candidate": evidence[version]["candidate"],
                "holdout": evidence[version]["holdout"],
                "checkpoints": checkpoint_by_hypothesis.get(version, []),
                "formal_result": formal_by_hypothesis.get(version),
                "current_status": (final.get("final_decision") if final else
                                   "AWAITING_FAMILY_CORRECTION" if store.checkpoint(version, "B")
                                   else "COLLECTING_HOLDOUT"),
                "final_decision": final.get("final_decision") if final else None,
            },
        })
    family_export = {**family, "result": final_payload}
    events = [_clean_row(row) for row in store.rows(
        "research_events", "event_date,code,event_type,research_event_id")]
    subjects = [_clean_row(row) for row in store.rows(
        "validation_subjects", "anchor_date,subject_type,code")]
    event_origin_counts = Counter(row.get("data_origin") or "NOT_ELIGIBLE" for row in events)
    event_phase_counts = Counter(row.get("evaluation_phase") or "DISCOVERY" for row in events)
    subject_origin_counts = Counter(row.get("data_origin") or "NOT_ELIGIBLE" for row in subjects)
    subject_phase_counts = Counter(row.get("evaluation_phase") or "DISCOVERY" for row in subjects)
    formal_event_count = sum(
        row.get("data_origin") == "LIVE_FORWARD" and row.get("evaluation_phase") == "HOLDOUT"
        for row in events)
    formal_subject_count = sum(
        row.get("data_origin") == "LIVE_FORWARD" and row.get("evaluation_phase") == "HOLDOUT"
        for row in subjects)
    performance_rows = [{
        "hypothesis_version": version,
        "candidate_status": value["candidate"]["candidate_status"],
        "candidate_basis": value["candidate"]["candidate_basis"],
        **{f"discovery_{key}": val for key, val in value["discovery_stats"].items()
           if not isinstance(val, (dict, list))},
        **{f"holdout_{key}": val for key, val in value["holdout_stats"].items()
           if not isinstance(val, (dict, list))},
    } for version, value in evidence.items()]
    intraday = _intraday_diagnostics(events)
    telemetry = store.rows("research_telemetry", "run_date,run_id")
    latest_telemetry = telemetry[-1].get("payload") if telemetry else {}
    generated = now_jst()

    _write_json(output / "hypotheses.json", hypothesis_exports)
    _write_json(output / "families.json", [family_export])
    _write_json(output / "events.json", events)
    _write_csv(output / "events.csv", events)
    _write_json(output / "performance.json", performance_rows)
    _write_csv(output / "performance.csv", performance_rows)
    _write_json(output / "checkpoints.json", checkpoint_rows)
    _write_json(output / "intraday_diagnostics.json", intraday)
    _write_json(output / "storage_metrics.json", latest_telemetry.get("storage", {}))
    _write_json(output / "performance_metrics.json", latest_telemetry.get("performance", {}))
    state = {
        "research_logic_version": RESEARCH_LOGIC_VERSION,
        "hypotheses": store.rows("research_hypotheses", "hypothesis_version"),
        "families": store.rows("research_families", "family_id,family_version"),
    }
    for table in ("research_events", "validation_subjects", "research_control_members",
                  "research_control_history", "research_subject_history",
                  "research_checkpoint_evaluations", "research_results",
                  "research_family_results", "research_telemetry"):
        state[table] = store.rows(table)
    _write_json(output / "state.json", state)
    available = ["hypotheses.json", "families.json", "events.json", "events.csv",
                 "performance.json", "performance.csv", "checkpoints.json",
                 "intraday_diagnostics.json", "storage_metrics.json",
                 "performance_metrics.json"]
    index = {
        "generated_at": generated,
        "research_logic_version": RESEARCH_LOGIC_VERSION,
        "research_schema_version": RESEARCH_SCHEMA_VERSION,
        "validation_engine_version": VALIDATION_ENGINE_VERSION,
        "control_selection_version": CONTROL_SELECTION_VERSION,
        "cost_model_version": cost_model()["version"],
        "family_id": FAMILY_ID,
        "hypothesis_count": len(hypothesis_exports),
        "event_count": len(events),
        "validation_subject_count": len(subjects),
        "event_data_origin_counts": {
            key: int(event_origin_counts.get(key, 0))
            for key in ("LIVE_FORWARD", "RECONSTRUCTED_LEGACY", "NOT_ELIGIBLE")
        },
        "event_evaluation_phase_counts": {
            key: int(event_phase_counts.get(key, 0))
            for key in ("DISCOVERY", "HOLDOUT", "POST_CONFIRMATION")
        },
        "validation_subject_data_origin_counts": {
            key: int(subject_origin_counts.get(key, 0))
            for key in ("LIVE_FORWARD", "RECONSTRUCTED_LEGACY", "NOT_ELIGIBLE")
        },
        "validation_subject_evaluation_phase_counts": {
            key: int(subject_phase_counts.get(key, 0))
            for key in ("DISCOVERY", "HOLDOUT", "POST_CONFIRMATION")
        },
        "formal_validation_event_count": int(formal_event_count),
        "formal_validation_subject_count": int(formal_subject_count),
        "trading_session_progress": ((run_result or {}).get("trading_session_progress") or {
            "elapsed": None, "total": 90, "source": "NOT_AVAILABLE",
            "latest_market_date": None,
        }),
        "available_files": available,
        "core_ranking_affected": False,
        "notes": [
            "Confirmatory evidenceはLIVE_FORWARDかつHOLDOUTのみ",
            "Checkpoint BはHypothesis Versionごとに一度だけ評価",
            "Family Close前は最終判定を確定しない",
            "T CLOSEはREFERENCE_ONLY、日足内の順序不明はPATH_AMBIGUOUS",
        ],
    }
    _write_json(output / "index.json", index)
    return index


def _core_subject(snapshot: dict, meta: dict[str, dict]) -> ValidationSubject:
    origin, phase = _origin_phase(snapshot["signal_date"])
    security = meta.get(snapshot["code"], {})
    return ValidationSubject(
        validation_subject_id=f"core:{snapshot['signal_id']}", subject_type="CORE_SIGNAL",
        code=snapshot["code"], stock_name=snapshot.get("stock_name") or "",
        anchor_date=snapshot["signal_date"], anchor_price=float(snapshot["close"]),
        price_basis="CLOSE", benchmark_anchor_price=_number(snapshot.get("benchmark_close")),
        momentum_percentile=_number(snapshot.get("momentum_percentile")),
        trading_value=_number(snapshot.get("current_trading_value")),
        trading_value_20d=_number(snapshot.get("trading_value_20d")),
        liquidity_level=snapshot.get("liquidity_level"), market=security.get("market"),
        size_class=security.get("size_class"),
        strategy_version=snapshot.get("strategy_version") or "not_available",
        threshold_version=snapshot.get("threshold_version") or "not_available",
        schema_version=snapshot.get("schema_version") or "not_available",
        research_version=RESEARCH_LOGIC_VERSION, source_event_id=None,
        data_origin=origin, evaluation_phase=phase,
        created_at=snapshot.get("created_at") or now_jst())


def _research_events(snapshots: list[dict], histories: list[dict],
                     frames: dict[str, pd.DataFrame], benchmark: pd.DataFrame,
                     meta: dict[str, dict], cfg: dict) -> tuple[list[dict], list[ValidationSubject]]:
    history_by_signal: dict[str, list[dict]] = defaultdict(list)
    for row in histories:
        history_by_signal[row["signal_id"]].append(row)
    events: list[dict] = []
    subjects: list[ValidationSubject] = []
    for snapshot in snapshots:
        signal_id = snapshot["signal_id"]
        frame = frames.get(snapshot["code"])
        initial = snapshot.get("consensus_state")
        if initial == SetupState.BREAKOUT_WATCH.value:
            events.append(_base_event(snapshot, "WATCH_OBSERVED", snapshot["signal_date"],
                                      frame, "CLOSE", initial))
        breakout_date = snapshot["signal_date"] if initial == SetupState.BREAKOUT.value else None
        if breakout_date is None:
            transition = next((row for row in sorted(history_by_signal.get(signal_id, []),
                                                      key=lambda item: item["date"])
                               if row.get("consensus_state") == SetupState.BREAKOUT.value), None)
            breakout_date = transition.get("date") if transition else None
        if not breakout_date:
            continue
        pivot, formed_date = _primary_pivot(snapshot)
        plan = _loads(snapshot.get("trade_plan_json"))
        stop = _number(plan.get("stop"))
        breakout = _base_event(snapshot, "BREAKOUT_CONFIRMED", breakout_date, frame,
                               "CLOSE", "ELIGIBLE" if _ohlc(frame, breakout_date) else "NOT_ELIGIBLE")
        watch_to_breakout_5of5 = bool(
            initial == SetupState.BREAKOUT_WATCH.value
            and int(snapshot.get("aligned_count") or 0) == 5)
        breakout.update({"pivot_price": pivot, "stop": stop,
                         "initial_risk": (float(breakout["close"]) - stop
                                          if breakout.get("close") is not None and stop is not None else None),
                         "payload_json": json.dumps({"pivot_known_date": formed_date,
                                                     "watch_to_breakout_5of5": watch_to_breakout_5of5},
                                                    ensure_ascii=False, separators=(",", ":"))})
        breakout["research_event_id"] = _event_id(signal_id, "BREAKOUT_CONFIRMED", breakout_date)
        events.append(breakout)
        if frame is None or frame.empty:
            continue
        simulations = simulate_entry_methods(frame, breakout_date, pivot, formed_date, stop)
        for method, simulation in simulations.items():
            event_type = {"T_PLUS_1_OPEN": "ENTRY_NEXT_OPEN",
                          "T_CLOSE_REFERENCE": "ENTRY_T_CLOSE_REFERENCE",
                          "PIVOT_STOP": "ENTRY_PIVOT_STOP"}[method]
            entry_date = simulation.entry_date or breakout_date
            event = _base_event(snapshot, event_type, entry_date, frame,
                                simulation.price_basis,
                                "REFERENCE_ONLY" if simulation.reference_only else
                                "ELIGIBLE" if simulation.eligible else "NOT_ELIGIBLE")
            event_id = _event_id(signal_id, event_type, entry_date)
            event.update({
                "research_event_id": event_id,
                "source_signal_id": signal_id,
                "pivot_price": pivot, "stop": stop,
                "close": simulation.entry_price if simulation.price_basis == "REFERENCE" else event.get("close"),
                "initial_risk": (simulation.entry_price - stop
                                 if simulation.entry_price is not None and stop is not None else None),
                "payload_json": json.dumps({**simulation.to_dict(),
                    "source_breakout_event_id": breakout["research_event_id"],
                    "watch_to_breakout_5of5": watch_to_breakout_5of5},
                    ensure_ascii=False, separators=(",", ":"), allow_nan=False),
            })
            events.append(event)
            if simulation.path_ambiguous:
                ambiguous = dict(event)
                ambiguous["research_event_id"] = _event_id(signal_id, "PATH_AMBIGUOUS", entry_date)
                ambiguous["event_type"] = "PATH_AMBIGUOUS"
                ambiguous["event_state"] = "AMBIGUOUS_EXCLUDED_FROM_PRIMARY"
                events.append(ambiguous)
            if simulation.eligible:
                subjects.append(_entry_subject(snapshot, event, simulation, benchmark,
                                               meta.get(snapshot["code"], {})))
    return events, subjects


def _entry_subject(snapshot: dict, event: dict, simulation, benchmark: pd.DataFrame,
                   security: dict) -> ValidationSubject:
    origin, phase = _origin_phase(simulation.entry_date)
    benchmark_anchor = _exact_price(benchmark, simulation.entry_date,
                                    "open" if simulation.price_basis == "OPEN" else "close")
    return ValidationSubject(
        validation_subject_id=f"research:{event['research_event_id']}",
        subject_type="RESEARCH_ENTRY_EVENT", code=snapshot["code"],
        stock_name=snapshot.get("stock_name") or "", anchor_date=simulation.entry_date,
        anchor_price=float(simulation.entry_price), price_basis=simulation.price_basis,
        benchmark_anchor_price=benchmark_anchor,
        momentum_percentile=_number(snapshot.get("momentum_percentile")),
        trading_value=_number(snapshot.get("current_trading_value")),
        trading_value_20d=_number(snapshot.get("trading_value_20d")),
        liquidity_level=snapshot.get("liquidity_level"), market=security.get("market"),
        size_class=security.get("size_class"),
        strategy_version=snapshot.get("strategy_version") or "not_available",
        threshold_version=snapshot.get("threshold_version") or "not_available",
        schema_version=snapshot.get("schema_version") or "not_available",
        research_version=RESEARCH_LOGIC_VERSION, source_event_id=event["research_event_id"],
        data_origin=origin, evaluation_phase=phase, created_at=now_jst())


def _create_research_controls(store: ResearchStore, analyses: list,
                              frames: dict[str, pd.DataFrame], meta: dict[str, dict],
                              cfg: dict) -> int:
    by_date: dict[str, list[dict]] = defaultdict(list)
    existing_subjects = {row["validation_subject_id"] for row in
                         store.rows("research_control_members")
                         if row["selection_version"] == CONTROL_SELECTION_VERSION}
    for subject in store.rows("validation_subjects", "anchor_date,validation_subject_id"):
        if (subject["subject_type"] == "RESEARCH_ENTRY_EVENT"
                and subject["price_basis"] == "OPEN"
                and subject["validation_subject_id"] not in existing_subjects):
            by_date[subject["anchor_date"]].append(subject)
    event_by_id = {row["research_event_id"]: row for row in store.rows("research_events")}
    feature_cache: dict[str, dict[str, SimpleNamespace]] = {}
    executable_cache: dict[tuple[str, str], list[SimpleNamespace]] = {}
    signal_codes_by_date: dict[str, set[str]] = defaultdict(set)
    for event in event_by_id.values():
        if event["event_type"] == "BREAKOUT_CONFIRMED":
            signal_codes_by_date[event["event_date"]].add(event["code"])
    all_rows = []
    created_groups: set[str] = set()
    for anchor_date, subjects in by_date.items():
        for subject in subjects:
            entry_event = event_by_id.get(subject.get("source_event_id"), {})
            payload = entry_event.get("payload") or _loads(entry_event.get("payload_json"))
            breakout = event_by_id.get(payload.get("source_breakout_event_id"), {})
            selection_date = breakout.get("event_date")
            if not selection_date:
                continue
            if selection_date not in feature_cache:
                feature_cache[selection_date] = _feature_matrix_at(
                    frames, meta, selection_date)
            universe = feature_cache[selection_date]
            signal = universe.get(subject["code"])
            if signal is None:
                continue
            excluded = signal_codes_by_date.get(selection_date, set())
            cache_key = (selection_date, anchor_date)
            if cache_key not in executable_cache:
                executable_cache[cache_key] = [
                    item for item in universe.values() if item.code not in excluded
                    and _exact_price(frames.get(item.code), anchor_date, "open") is not None]
            candidates = [item for item in executable_cache[cache_key]
                          if item.code != subject["code"]]
            if not candidates:
                continue
            random_codes = deterministic_random_codes(
                subject["validation_subject_id"], [item.code for item in candidates],
                int(cfg["controls"]["random_count"]), CONTROL_SELECTION_VERSION)
            scored = sorted((matching_distance(signal, item, meta, cfg), item.code)
                            for item in candidates)
            matched_codes = [code for _, code in scored[:int(cfg["controls"]["matched_count"])]]
            candidate_map = {item.code: item for item in candidates}
            for kind, codes in (("RANDOM", random_codes), ("MATCHED", matched_codes)):
                group_id = _control_group_id(subject["validation_subject_id"], kind)
                created_groups.add(group_id)
                for rank, code in enumerate(codes, 1):
                    item = candidate_map[code]
                    feature = {
                        "selection_date": selection_date,
                        "execution_anchor_date": anchor_date,
                        "momentum_percentile": item.metrics.get("momentum_percentile"),
                        "trading_value_20d": item.metrics.get("trading_value_20d"),
                        "market": meta.get(code, {}).get("market"),
                        "size_class": meta.get(code, {}).get("size_class"),
                        "price": item.metrics.get("price"),
                    }
                    feature_json = json.dumps(feature, ensure_ascii=False, sort_keys=True,
                                              separators=(",", ":"), default=str)
                    all_rows.append({
                        "control_group_id": group_id,
                        "validation_subject_id": subject["validation_subject_id"],
                        "control_code": code, "control_name": item.name,
                        "control_type": kind, "control_rank": rank,
                        "match_score": (matching_distance(signal, item, meta, cfg)
                                        if kind == "MATCHED" else None),
                        "anchor_date": anchor_date,
                        "anchor_price": _exact_price(frames[code], anchor_date, "open"),
                        "price_basis": "OPEN", "selection_version": CONTROL_SELECTION_VERSION,
                        "feature_snapshot_json": feature_json,
                        "feature_hash": hashlib.sha256(feature_json.encode()).hexdigest(),
                    })
    store.save_control_members(all_rows)
    return len(created_groups)


def _track_research(store: ResearchStore, frames: dict[str, pd.DataFrame],
                    benchmark: pd.DataFrame, cfg: dict) -> None:
    subject_rows = store.rows("validation_subjects", "anchor_date,validation_subject_id")
    event_by_id = {row["research_event_id"]: row for row in store.rows("research_events")}
    subject_history = []
    for subject in subject_rows:
        if subject["subject_type"] != "RESEARCH_ENTRY_EVENT" or subject["price_basis"] == "REFERENCE":
            continue
        frame = frames.get(subject["code"])
        path = _path_from(frame, subject["anchor_date"])
        if path is None:
            continue
        offset = min(len(path) - 1, int(cfg["tracking"]["max_sessions"]))
        path = path.iloc[:offset + 1]
        metrics = path_metrics(path, float(subject["anchor_price"]), benchmark,
                               pd.Timestamp(subject["anchor_date"]), path.index[-1],
                               "open" if subject["price_basis"] == "OPEN" else "close")
        event = event_by_id.get(subject.get("source_event_id"), {})
        stop = _number(event.get("stop"))
        risk = float(subject["anchor_price"]) - stop if stop is not None else None
        subject_history.append({
            "validation_subject_id": subject["validation_subject_id"],
            "date": str(path.index[-1].date()), "session_offset": offset, **metrics,
            "failed_breakout": int(bool(event.get("pivot_price") is not None
                and offset <= 5 and float(path.close.iloc[-1]) < float(event["pivot_price"]) * .97)),
            "hit_1r": int(bool(risk and float(path.high.max()) >= float(subject["anchor_price"]) + risk)),
            "hit_2r": int(bool(risk and float(path.high.max()) >= float(subject["anchor_price"]) + 2 * risk)),
            "hit_stop": int(bool(stop is not None and float(path.low.min()) <= stop)),
            "path_ambiguous": int(bool(event.get("event_type") == "ENTRY_PIVOT_STOP"
                and stop is not None and float(path.iloc[0].low) <= stop)),
        })
    store.save_subject_history(subject_history)
    control_history = []
    for member in store.rows("research_control_members", "anchor_date,control_group_id,control_rank"):
        frame = frames.get(member["control_code"])
        path = _path_from(frame, member["anchor_date"])
        if path is None:
            continue
        offset = min(len(path) - 1, int(cfg["tracking"]["max_sessions"]))
        path = path.iloc[:offset + 1]
        metrics = path_metrics(path, float(member["anchor_price"]), benchmark,
                               pd.Timestamp(member["anchor_date"]), path.index[-1],
                               "open" if member["price_basis"] == "OPEN" else "close")
        control_history.append({
            "control_group_id": member["control_group_id"],
            "validation_subject_id": member["validation_subject_id"],
            "control_code": member["control_code"], "date": str(path.index[-1].date()),
            "session_offset": offset, **metrics,
        })
    store.save_control_history(control_history)


def _evidence(db: Database, store: ResearchStore,
              hypotheses: list[dict]) -> dict[str, dict[str, Any]]:
    rows_by_hypothesis = _evidence_rows(db, store)
    output = {}
    for hypothesis in hypotheses:
        version = hypothesis["hypothesis_version"]
        rows = rows_by_hypothesis[hypothesis["hypothesis_id"]]
        discovery = [row for row in rows if row.get("evaluation_phase") == "DISCOVERY"
                     and row.get("data_origin") in {"RECONSTRUCTED_LEGACY", "LIVE_FORWARD"}]
        holdout = [row for row in rows if row.get("evaluation_phase") == "HOLDOUT"
                   and row.get("data_origin") == "LIVE_FORWARD"]
        discovery_stats = _hypothesis_stats(hypothesis, discovery)
        holdout_stats = _hypothesis_stats(hypothesis, holdout)
        output[version] = {
            "candidate": candidate_gate(hypothesis, discovery_stats),
            "holdout": {**holdout_stats, "eligible_rule": "LIVE_FORWARD_AND_HOLDOUT"},
            "discovery_stats": discovery_stats, "holdout_stats": holdout_stats,
        }
    return output


def _evidence_rows(db: Database, store: ResearchStore) -> dict[str, list[dict]]:
    snapshots, history = db.validation_rows()
    controls, control_history = db.control_validation_rows()
    history_at = {(row["signal_id"], int(row["session_offset"])): row for row in history}
    control_groups: dict[tuple[str, int], list[float]] = defaultdict(list)
    member_type = {(row["control_group_id"], row["control_code"]): row["control_type"]
                   for row in controls}
    for row in control_history:
        if member_type.get((row["control_group_id"], row["control_code"])) == "MATCHED":
            control_groups[(row["signal_id"], int(row["session_offset"]))].append(
                float(row["return_abs"]))
    h1, h4 = [], []
    for snapshot in snapshots:
        origin, phase = _origin_phase(snapshot["signal_date"])
        obs10 = history_at.get((snapshot["signal_id"], 10))
        controls10 = control_groups.get((snapshot["signal_id"], 10), [])
        if obs10 and controls10 and int(snapshot.get("aligned_count") or 0) in {3, 4, 5}:
            h1.append({"code": snapshot["code"], "date": snapshot["signal_date"],
                       "group": "4PLUS" if int(snapshot["aligned_count"]) >= 4 else "3",
                       "value": float(obs10["return_abs"]) - statistics.fmean(controls10),
                       "data_origin": origin, "evaluation_phase": phase})
        obs5 = history_at.get((snapshot["signal_id"], 5))
        if obs5 and int(snapshot.get("aligned_count") or 0) in {4, 5}:
            h4.append({"code": snapshot["code"], "date": snapshot["signal_date"],
                       "group": str(int(snapshot["aligned_count"])),
                       "value": float(bool(obs5.get("failed_breakout"))) * 100,
                       "data_origin": origin, "evaluation_phase": phase})

    subjects = store.rows("validation_subjects")
    subject_by_id = {row["validation_subject_id"]: row for row in subjects}
    events = store.rows("research_events")
    event_by_id = {row["research_event_id"]: row for row in events}
    subject_history = store.rows("research_subject_history")
    research_at = {(row["validation_subject_id"], int(row["session_offset"])): row
                   for row in subject_history}
    members = store.rows("research_control_members")
    research_control_type = {(row["control_group_id"], row["control_code"]): row["control_type"]
                             for row in members}
    research_controls: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in store.rows("research_control_history"):
        if research_control_type.get((row["control_group_id"], row["control_code"])) == "MATCHED":
            research_controls[(row["validation_subject_id"], int(row["session_offset"]))].append(
                float(row["return_abs"]))
    h2 = []
    pair_map: dict[str, dict[str, dict]] = defaultdict(dict)
    for subject in subjects:
        if subject["subject_type"] != "RESEARCH_ENTRY_EVENT":
            continue
        event = event_by_id.get(subject.get("source_event_id"), {})
        payload = event.get("payload") or _loads(event.get("payload_json"))
        method = payload.get("method")
        obs = research_at.get((subject["validation_subject_id"], 10))
        if method == "T_PLUS_1_OPEN" and payload.get("watch_to_breakout_5of5") is True and obs:
            control_values = research_controls.get((subject["validation_subject_id"], 10), [])
            if control_values:
                h2.append({"code": subject["code"], "date": subject["anchor_date"],
                           "value": float(obs["return_abs"]) - statistics.fmean(control_values)
                                    - cost_model()["BASE"]["total"],
                           "data_origin": subject["data_origin"],
                           "evaluation_phase": subject["evaluation_phase"]})
        if method in {"T_PLUS_1_OPEN", "PIVOT_STOP"} and obs:
            breakout_id = payload.get("source_breakout_event_id") or ""
            pair_map[breakout_id][method] = {"subject": subject, "observation": obs,
                                             "payload": payload}
    h3 = []
    for pair in pair_map.values():
        if set(pair) != {"T_PLUS_1_OPEN", "PIVOT_STOP"}:
            continue
        pivot, open_entry = pair["PIVOT_STOP"], pair["T_PLUS_1_OPEN"]
        if pivot["payload"].get("path_ambiguous"):
            continue
        raw = float(pivot["observation"]["return_abs"]) - float(open_entry["observation"]["return_abs"])
        subject = pivot["subject"]
        h3.append({"code": subject["code"], "date": subject["anchor_date"],
                   "raw_value": raw, "value": raw - cost_model()["BASE"]["total"],
                   "high_cost_value": raw - cost_model()["HIGH"]["total"],
                   "data_origin": subject["data_origin"],
                   "evaluation_phase": subject["evaluation_phase"]})
    return {"H1": h1, "H2": h2, "H3": h3, "H4": h4}


def _hypothesis_stats(hypothesis: dict, rows: list[dict]) -> dict[str, Any]:
    hypothesis_id = hypothesis["hypothesis_id"]
    if hypothesis_id == "H1":
        statistic = lambda sample: _group_difference(sample, "4PLUS", "3")
    elif hypothesis_id == "H4":
        statistic = lambda sample: _group_difference(sample, "5", "4")
    else:
        statistic = lambda sample: statistics.fmean([float(row["value"]) for row in sample]) if sample else None
    boot90 = cluster_bootstrap_statistic(rows, "date", statistic, .90,
                                         seed_label=f"{hypothesis_id}-candidate")
    boot95 = cluster_bootstrap_statistic(rows, "date", statistic, .95,
                                         seed_label=f"{hypothesis_id}-confirm")
    legacy = sum(row.get("data_origin") == "RECONSTRUCTED_LEGACY" for row in rows)
    live = sum(row.get("data_origin") == "LIVE_FORWARD" for row in rows)
    stats: dict[str, Any] = {
        "total_n": len(rows), "effective_n": len(rows),
        "legacy_n": legacy, "live_pre_candidate_n": live,
        "legacy_share_pct": legacy / len(rows) * 100 if rows else 0.0,
        "unique_stocks": len({row["code"] for row in rows}),
        "unique_dates": len({row["date"] for row in rows}),
        "effect_estimate": boot90["estimate"],
        "ci_90_lower": boot90["ci_lower"], "ci_90_upper": boot90["ci_upper"],
        "ci_95_lower": boot95["ci_lower"], "ci_95_upper": boot95["ci_upper"],
        "standard_error": boot95["standard_error"],
    }
    if hypothesis_id == "H1":
        stats.update(group_4plus=sum(row["group"] == "4PLUS" for row in rows),
                     group_3=sum(row["group"] == "3" for row in rows))
    elif hypothesis_id == "H4":
        stats.update(group_5=sum(row["group"] == "5" for row in rows),
                     group_4=sum(row["group"] == "4" for row in rows))
    elif hypothesis_id == "H3":
        stats["paired_events"] = len(rows)
        stats["high_cost_effect"] = (statistics.fmean([row["high_cost_value"] for row in rows])
                                     if rows else None)
    else:
        stats["events"] = len(rows)
    return stats


def _update_checkpoints(store: ResearchStore, hypotheses: list[dict],
                        evidence: dict[str, dict[str, Any]]) -> list[dict]:
    saved = []
    now = now_jst()
    for hypothesis in hypotheses:
        version = hypothesis["hypothesis_version"]
        stats = evidence[version]["holdout_stats"]
        for name in ("A", "B"):
            gate = checkpoint_gate(hypothesis, name, stats)
            if not gate["reached"] or store.checkpoint(version, name):
                continue
            row = {"hypothesis_version": version, "checkpoint_name": name,
                   "reached_at": now, "evaluated_at": now,
                   "effective_n": gate["effective_n"],
                   "unique_stocks": gate["unique_stocks"],
                   "unique_dates": gate["unique_dates"], "result": gate["result"],
                   **gate}
            store.save_checkpoint_once(row)
            saved.append(row)
            if name == "B":
                store.save_result_once(_formal_result(hypothesis, stats))
    return saved


def _formal_result(hypothesis: dict, stats: dict[str, Any]) -> dict[str, Any]:
    effect, standard_error = stats.get("effect_estimate"), stats.get("standard_error")
    low, high = stats.get("ci_95_lower"), stats.get("ci_95_upper")
    claim = hypothesis.get("candidate_claim")
    difference_p = normal_two_sided_p(effect, standard_error)
    result: dict[str, Any] = {
        "hypothesis_version": hypothesis["hypothesis_version"],
        "test_execution_status": "EXECUTED", "effect_estimate": effect,
        "ci_lower": low, "ci_upper": high,
        "difference_primary_p": difference_p,
        "primary_family_p_field": hypothesis["primary_family_p_field"],
        "raw_primary_p": difference_p,
        "multiplicity_status": "AWAITING_FAMILY_CORRECTION",
        "holm_adjusted_p": None, "multiplicity_gate": "NOT_AVAILABLE_BEFORE_CLOSE",
        "final_decision": "AWAITING_FAMILY_CORRECTION",
    }
    if hypothesis["hypothesis_id"] == "H3" and claim == "EQUIVALENCE":
        margin = abs(float(hypothesis["sesoi"]))
        equivalence_p = tost_p(effect, standard_error, -margin, margin)
        result.update({
            "equivalence_test_method": hypothesis["equivalence_test_method"],
            "equivalence_test_alpha": hypothesis["equivalence_test_alpha"],
            "equivalence_margin_lower": -margin, "equivalence_margin_upper": margin,
            "equivalence_primary_p": equivalence_p,
            "raw_primary_p": equivalence_p,
            "equivalence_test_pass": bool(equivalence_p is not None and equivalence_p < .05),
            "equivalence_ci_level": .95, "equivalence_ci_lower": low,
            "equivalence_ci_upper": high,
            "equivalence_ci_containment_gate": (
                "PASS" if low is not None and high is not None
                and low >= -margin and high <= margin else "FAIL"),
            "high_cost_robustness_gate": _high_cost_equivalence(stats, margin),
        })
        return result
    direction = hypothesis["direction"]
    sesoi = float(hypothesis["sesoi"])
    if direction == "POSITIVE":
        expected, opposite = effect is not None and effect > 0, effect is not None and effect < 0
        effect_pass = effect is not None and effect >= sesoi
        ci_pass = low is not None and low > 0
        opposite_effect = effect is not None and effect <= -abs(sesoi)
        opposite_ci = high is not None and high < 0
    elif direction == "NEGATIVE":
        expected, opposite = effect is not None and effect < 0, effect is not None and effect > 0
        effect_pass = effect is not None and effect <= sesoi
        ci_pass = high is not None and high < 0
        opposite_effect = effect is not None and effect >= abs(sesoi)
        opposite_ci = low is not None and low > 0
    else:
        expected, opposite = effect is not None, False
        effect_pass = effect is not None and abs(effect) >= abs(sesoi)
        ci_pass = low is not None and high is not None and (low > 0 or high < 0)
        opposite_effect = opposite_ci = False
    result.update({
        "direction_gate": "EXPECTED" if expected else "OPPOSITE" if opposite else "UNKNOWN",
        "effect_size_gate": "PASS" if effect_pass else "FAIL",
        "ci_gate": "PASS" if ci_pass else "FAIL",
        "opposite_effect_size_gate": "PASS" if opposite_effect else "FAIL",
        "opposite_ci_gate": "PASS" if opposite_ci else "FAIL",
        "high_cost_robustness_gate": (_high_cost_difference(stats, effect)
                                      if hypothesis["hypothesis_id"] == "H3" else "PASS"),
    })
    return result


def _maybe_close_family(store: ResearchStore, hypotheses: list[dict], family: dict,
                        latest_date: str = "") -> dict | None:
    if latest_date < family["family_close_at"]:
        return None
    raw = {}
    for row in store.rows("research_results", "hypothesis_version"):
        raw[row["hypothesis_version"]] = row.get("payload") or _clean_row(row)
    for hypothesis in hypotheses:
        version = hypothesis["hypothesis_version"]
        raw.setdefault(version, {
            "hypothesis_version": version,
            "test_execution_status": "INSUFFICIENT_SAMPLE",
            "raw_primary_p": None, "holm_adjusted_p": None,
            "final_decision": "INSUFFICIENT_SAMPLE",
            "primary_family_p_field": hypothesis["primary_family_p_field"],
        })
    corrected = family_correction(raw, family["planned_family_size"], family["alpha"])
    members = {}
    for hypothesis in hypotheses:
        version = hypothesis["hypothesis_version"]
        result = corrected[version]
        result["final_decision"] = final_decision(hypothesis, result, family_closed=True,
                                                   alpha=family["alpha"])
        if result.get("equivalence_primary_p") is not None:
            result["equivalence_holm_adjusted_p"] = result.get("holm_adjusted_p")
            result["equivalence_multiplicity_gate"] = result.get("multiplicity_gate")
        members[version] = result
    payload = {"family_id": family["family_id"], "family_version": family["family_version"],
               "family_status": "CLOSED", "closed_at": now_jst(), "members": members,
               "planned_family_size": family["planned_family_size"]}
    store.save_family_result(payload)
    return payload


def _intraday_diagnostics(events: list[dict]) -> dict[str, Any]:
    entries = []
    for event in events:
        if event.get("event_type") not in {"ENTRY_NEXT_OPEN", "ENTRY_PIVOT_STOP",
                                          "ENTRY_T_CLOSE_REFERENCE"}:
            continue
        payload = event.get("payload") or _loads(event.get("payload_json"))
        if payload:
            entries.append({"date": event.get("event_date"), "code": event.get("code"), **payload})
    diagnostics = {}
    for key in ("gap_pct", "gap_atr", "gap_r", "breakout_range_atr"):
        rows = [row for row in entries if row.get(key) is not None]
        boot = cluster_bootstrap_statistic(rows, "date",
            lambda sample, k=key: statistics.fmean(float(row[k]) for row in sample), .95,
            seed_label=f"intraday-{key}")
        values = sorted(float(row[key]) for row in rows)
        diagnostics[key] = {"count": len(rows), "coverage_pct": len(rows) / len(entries) * 100
                            if entries else 0, "mean": boot["estimate"],
                            "cluster_ci_95_lower": boot["ci_lower"],
                            "cluster_ci_95_upper": boot["ci_upper"],
                            "quantiles": _quantiles(values)}
    for key in ("path_ambiguous", "false_breakout"):
        rows = [row for row in entries if row.get(key) is not None]
        successes = sum(bool(row[key]) for row in rows)
        wilson = wilson_interval(successes, len(rows))
        boot = cluster_bootstrap_statistic(rows, "date",
            lambda sample, k=key: statistics.fmean(float(bool(row[k])) for row in sample), .95,
            seed_label=f"intraday-{key}")
        diagnostics[key] = {"count": len(rows), "successes": successes,
                            "rate": successes / len(rows) if rows else None,
                            "wilson_ci_95_lower": wilson[0], "wilson_ci_95_upper": wilson[1],
                            "cluster_ci_95_lower": boot["ci_lower"],
                            "cluster_ci_95_upper": boot["ci_upper"]}
    return {"research_version": RESEARCH_LOGIC_VERSION,
            "threshold_optimized": False, "unique_events": len(entries),
            "unique_stocks": len({row["code"] for row in entries}),
            "unique_dates": len({row["date"] for row in entries}),
            "diagnostics": diagnostics}


def _storage_metrics(db: Database, store: ResearchStore, compacted_rows: int) -> dict[str, Any]:
    core = db.validation_rows()
    controls = db.control_validation_rows()
    experimental = db.experimental_rows()
    validation_bytes = len(json.dumps([*core, *controls, experimental], default=str,
                                      ensure_ascii=False).encode())
    members = store.rows("research_control_members")
    history = store.rows("research_control_history")
    all_tables = [store.rows(table) for table in (
        "research_hypotheses", "research_families", "research_events",
        "validation_subjects", "research_control_members", "research_control_history",
        "research_subject_history", "research_checkpoint_evaluations",
        "research_results", "research_family_results")]
    membership_bytes = len(json.dumps(members, default=str, ensure_ascii=False).encode())
    history_bytes = len(json.dumps(history, default=str, ensure_ascii=False).encode())
    research_bytes = len(json.dumps(all_tables, default=str, ensure_ascii=False).encode())
    telemetry = store.rows("research_telemetry", "run_date,run_id")
    previous = ((telemetry[-1].get("payload") or {}).get("storage") or {}).get(
        "research_state_bytes", research_bytes) if telemetry else research_bytes
    growth = max(0, research_bytes - int(previous or 0))
    projected = research_bytes + growth * 180
    share = research_bytes / (validation_bytes + research_bytes) * 100 \
        if validation_bytes + research_bytes else 0
    status = ("RED" if share > 50 or projected > 500 * 1024 * 1024 or growth > 5 * 1024 * 1024
              else "YELLOW" if share >= 30 or projected >= 250 * 1024 * 1024 else "GREEN")
    return {
        "research_control_groups": len({row["control_group_id"] for row in members}),
        "research_control_members": len(members),
        "research_control_history_rows": len(history),
        "membership_bytes": membership_bytes, "history_bytes": history_bytes,
        "research_state_bytes": research_bytes, "validation_state_bytes": validation_bytes,
        "daily_growth_bytes": growth, "rolling_30d_growth_mb": growth * 30 / 1024 / 1024,
        "projected_180d_size_mb": projected / 1024 / 1024,
        "research_control_share_pct": share, "budget_status": status,
        "compacted_rows": compacted_rows,
    }


def _base_event(snapshot: dict, event_type: str, date: str,
                frame: pd.DataFrame | None, price_basis: str, state: str) -> dict:
    ohlc = _ohlc(frame, date) or {}
    origin, phase = _origin_phase(date)
    return {
        "research_event_id": _event_id(snapshot["signal_id"], event_type, date),
        "code": snapshot["code"], "setup_id": snapshot.get("setup_id"),
        "source_signal_id": snapshot["signal_id"], "event_type": event_type,
        "event_date": date, "aligned_count": snapshot.get("aligned_count"),
        "breakout_count": snapshot.get("breakout_count"), "pivot_price": None,
        "close": ohlc.get("close"), "open": ohlc.get("open"),
        "high": ohlc.get("high"), "low": ohlc.get("low"), "stop": None,
        "initial_risk": None, "event_state": state, "price_basis": price_basis,
        "strategy_version": snapshot.get("strategy_version"),
        "threshold_version": snapshot.get("threshold_version"),
        "research_version": RESEARCH_LOGIC_VERSION,
        "data_origin": origin if ohlc else "NOT_ELIGIBLE",
        "evaluation_phase": phase, "payload_json": "{}",
    }


def _primary_pivot(snapshot: dict) -> tuple[float | None, str | None]:
    pivots = _loads(snapshot.get("strategy_pivots_json"))
    values = [value for value in pivots.values() if value.get("price") is not None]
    if not values:
        return None, None
    close = float(snapshot["close"])
    selected = min(values, key=lambda value: abs(float(value["price"]) - close))
    return float(selected["price"]), selected.get("formed_date")


def _origin_phase(date: str) -> tuple[str, str]:
    if not date:
        return "NOT_ELIGIBLE", "DISCOVERY"
    origin = "LIVE_FORWARD" if date >= FREEZE_DATE else "RECONSTRUCTED_LEGACY"
    if date > FAMILY_CLOSE_AT:
        phase = "POST_CONFIRMATION"
    elif origin == "LIVE_FORWARD" and date >= HOLDOUT_START:
        phase = "HOLDOUT"
    else:
        phase = "DISCOVERY"
    return origin, phase


def _event_id(signal_id: str, event_type: str, date: str) -> str:
    digest = hashlib.sha256(f"{signal_id}|{event_type}|{date}|{RESEARCH_LOGIC_VERSION}".encode()).hexdigest()
    return f"re-{digest[:24]}"


def _control_group_id(subject_id: str, kind: str) -> str:
    digest = hashlib.sha256(f"{subject_id}|{kind}|{CONTROL_SELECTION_VERSION}".encode()).hexdigest()
    return f"rctrl-{digest[:24]}"


def _ohlc(frame: pd.DataFrame | None, date: str) -> dict[str, float] | None:
    if frame is None or frame.empty or pd.Timestamp(date) not in frame.index:
        return None
    row = frame.loc[pd.Timestamp(date)]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    return {key: float(row[key]) for key in ("open", "high", "low", "close")}


def _exact_price(frame: pd.DataFrame | None, date: str | None, column: str) -> float | None:
    if frame is None or frame.empty or date is None or pd.Timestamp(date) not in frame.index:
        return None
    row = frame.loc[pd.Timestamp(date)]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    return float(row[column])


def _path_from(frame: pd.DataFrame | None, date: str) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None
    path = frame.loc[frame.index >= pd.Timestamp(date)]
    return path if not path.empty and path.index[0] == pd.Timestamp(date) else None


def _feature_matrix_at(frames: dict[str, pd.DataFrame], meta: dict[str, dict],
                       date: str) -> dict[str, SimpleNamespace]:
    """Build matching features using only bars known by the breakout close."""
    cutoff = pd.Timestamp(date)
    raw: dict[str, dict[str, float]] = {}
    for code, frame in frames.items():
        if frame is None or frame.empty:
            continue
        known = frame.loc[frame.index <= cutoff]
        if known.empty or known.index[-1] != cutoff or len(known) < 20:
            continue
        lookback = known.iloc[-126:] if len(known) >= 126 else known
        start = float(lookback.close.iloc[0])
        price = float(known.close.iloc[-1])
        trading_value = (known.close.iloc[-20:].astype(float)
                         * known.volume.iloc[-20:].astype(float)).mean()
        raw[code] = {
            "raw_momentum": (price / start - 1) * 100 if start > 0 else 0.0,
            "trading_value_20d": float(trading_value), "price": price,
        }
    ordered = sorted(raw, key=lambda code: (raw[code]["raw_momentum"], code))
    denominator = max(1, len(ordered) - 1)
    percentile = {code: rank / denominator * 100 for rank, code in enumerate(ordered)}
    return {code: SimpleNamespace(
        code=code, name=meta.get(code, {}).get("name") or code,
        metrics={"momentum_percentile": percentile[code],
                 "trading_value_20d": values["trading_value_20d"],
                 "price": values["price"]})
        for code, values in raw.items()}


def _loads(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _group_difference(rows: list[dict], first: str, second: str) -> float | None:
    one = [float(row["value"]) for row in rows if row.get("group") == first]
    two = [float(row["value"]) for row in rows if row.get("group") == second]
    return statistics.fmean(one) - statistics.fmean(two) if one and two else None


def _high_cost_difference(stats: dict, base_effect: float | None) -> str:
    high = stats.get("high_cost_effect")
    if high is None or base_effect is None:
        return "NOT_AVAILABLE"
    return "PASS" if high == 0 or (high > 0) == (base_effect > 0) else "FAIL"


def _high_cost_equivalence(stats: dict, margin: float) -> str:
    high = stats.get("high_cost_effect")
    return "PASS" if high is not None and abs(float(high)) <= margin else "FAIL"


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "p50": None, "p90": None}
    def at(q: float) -> float:
        return values[min(len(values) - 1, max(0, round((len(values) - 1) * q)))]
    return {"p10": at(.1), "p50": at(.5), "p90": at(.9)}


def _clean_row(row: dict) -> dict:
    return {key: value for key, value in row.items()
            if key not in {"payload_json", "feature_snapshot_json", "created_at"}}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False, default=str),
                    encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    flat = []
    for row in rows:
        flat.append({key: (json.dumps(value, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"))
                           if isinstance(value, (dict, list)) else value)
                     for key, value in row.items()})
    fields = sorted({key for row in flat for key in row})
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flat)
