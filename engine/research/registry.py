from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "research.yaml"
with _CONFIG_PATH.open(encoding="utf-8") as _handle:
    RESEARCH_CONFIG = yaml.safe_load(_handle)
_VERSIONS = RESEARCH_CONFIG["versions"]
_FREEZE = RESEARCH_CONFIG["freeze"]
RESEARCH_LOGIC_VERSION = _VERSIONS["research_logic_version"]
REGISTRY_SCHEMA_VERSION = _VERSIONS["registry_schema_version"]
RESEARCH_SCHEMA_VERSION = _VERSIONS["research_schema_version"]
CONTROL_SELECTION_VERSION = _VERSIONS["control_selection_version"]
VALIDATION_ENGINE_VERSION = _VERSIONS["validation_engine_version"]
COST_MODEL_VERSION = _VERSIONS["cost_model_version"]
FAMILY_ID = _FREEZE["family_id"]
FAMILY_VERSION = str(_FREEZE["family_version"])
FREEZE_DATE = _FREEZE["freeze_date"]
HOLDOUT_START = _FREEZE["holdout_start"]
FAMILY_CLOSE_AT = _FREEZE["family_close_at"]


def now_jst() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")


def cost_model() -> dict[str, Any]:
    return {
        **deepcopy(RESEARCH_CONFIG["cost_model"]),
        "version": COST_MODEL_VERSION,
        "frozen_at": f"{FREEZE_DATE}T19:00:00+09:00",
    }


def hypothesis_registry(data_seen_through: str | None = None) -> list[dict[str, Any]]:
    data_seen_through = data_seen_through or _FREEZE["data_seen_through"]
    common = {
        "origin": "DATA_INFORMED",
        "registered_at": f"{FREEZE_DATE}T19:00:00+09:00",
        "data_seen_through": data_seen_through,
        "correction_family": FAMILY_ID,
        "candidate_ci_level": 0.90,
        "candidate_ci_sidedness": "TWO_SIDED",
        "confirmatory_ci_level": 0.95,
        "confirmatory_ci_sidedness": "TWO_SIDED",
        "analysis_method": "DATE_CLUSTER_BOOTSTRAP",
        "research_version": RESEARCH_LOGIC_VERSION,
        "freeze_status": "FROZEN",
        "frozen_at": f"{FREEZE_DATE}T19:00:00+09:00",
        "holdout_start": HOLDOUT_START,
        "candidate_cutoff_date": FREEZE_DATE,
        "contradicted_rule": {
            "operator": "AND",
            "conditions": ["direction_gate=OPPOSITE", "opposite_effect_size_gate=PASS",
                           "opposite_ci_gate=PASS", "multiplicity_gate=PASS"],
        },
    }
    rows = [
        {
            **common, "hypothesis_id": "H1", "hypothesis_version": "H1-v1",
            "definition": "4/5以上のCore Alignmentは3/5より将来Performanceが優れる",
            "primary_metric": "10d_excess_return_4plus_minus_3",
            "primary_horizon": 10,
            "secondary_metrics": ["5d", "20d", "mfe", "mae", "positive_rate"],
            "direction": "POSITIVE", "sesoi": 0.50,
            "sample_gate": {"group_4plus": 100, "group_3": 40,
                            "unique_stocks": 80, "unique_dates": 20},
            "control_policy": "REUSE_CORE_MATCHED_CONTROL",
            "cost_model": None,
            "candidate_rule": "effect>=0.50pp AND 90pct_CI_lower>0 AND sample_gate",
            "candidate_claim": "DIFFERENCE",
            "confirmed_rule": "expected_direction AND effect>=SESOI AND 95pct_CI AND Holm<0.05",
            "primary_test_type": "TWO_SIDED_ZERO_DIFFERENCE",
            "primary_test_sidedness": "TWO_SIDED",
            "primary_family_p_field": "difference_primary_p",
            "checkpoint_schedule": _checkpoints({"group_4plus": 60, "group_3": 25,
                                                  "unique_stocks": 50, "unique_dates": 15},
                                                 {"group_4plus": 120, "group_3": 50,
                                                  "unique_stocks": 100, "unique_dates": 30}),
        },
        {
            **common, "hypothesis_id": "H2", "hypothesis_version": "H2-v1",
            "definition": "5/5 WATCHからBREAKOUTへ遷移した銘柄はT+1 Open後にMatched Controlを上回る",
            "primary_metric": "10d_net_excess_return_vs_matched",
            "primary_horizon": 10,
            "secondary_metrics": ["5d", "20d", "mfe", "mae", "failed_breakout"],
            "direction": "POSITIVE", "sesoi": 0.75,
            "sample_gate": {"events": 50, "unique_stocks": 30, "unique_dates": 15},
            "control_policy": "BREAKOUT_DATE_MATCHED_CONTROL",
            "cost_model": COST_MODEL_VERSION,
            "candidate_rule": "effect>=0.75pp AND 90pct_CI_lower>0 AND sample_gate",
            "candidate_claim": "DIFFERENCE",
            "confirmed_rule": "expected_direction AND effect>=SESOI AND 95pct_CI AND Holm<0.05",
            "primary_test_type": "TWO_SIDED_ZERO_DIFFERENCE",
            "primary_test_sidedness": "TWO_SIDED",
            "primary_family_p_field": "difference_primary_p",
            "checkpoint_schedule": _checkpoints({"events": 40, "unique_stocks": 25,
                                                  "unique_dates": 12},
                                                 {"events": 75, "unique_stocks": 50,
                                                  "unique_dates": 25}),
        },
        {
            **common, "hypothesis_id": "H3", "hypothesis_version": "H3-v1",
            "definition": "同一BREAKOUT EventのPivot Stop EntryとT+1 Open Entryを比較する",
            "primary_metric": "10d_net_return_pivot_minus_next_open",
            "primary_horizon": 10,
            "secondary_metrics": ["5d", "20d", "mae", "mfe", "stop_rate",
                                  "hit_1r", "hit_2r", "path_ambiguous_rate"],
            "direction": "ABSOLUTE_DIFFERENCE", "sesoi": 0.30,
            "sample_gate": {"paired_events": 50, "unique_stocks": 30, "unique_dates": 15},
            "control_policy": "PAIRED_SAME_EVENT_NO_EXTERNAL_CONTROL",
            "cost_model": COST_MODEL_VERSION,
            "candidate_rule": "DIFFERENCE: abs(effect)>=0.30pp and 90pct_CI excludes 0",
            "candidate_claim": "DIFFERENCE",
            "confirmed_rule": "abs(effect)>=Final_SESOI AND 95pct_CI excludes 0 AND Holm<0.05 AND HIGH robust",
            "primary_test_type": "TWO_SIDED_ZERO_DIFFERENCE",
            "primary_test_sidedness": "TWO_SIDED",
            "primary_family_p_field": "difference_primary_p",
            "equivalence_test_method": "TOST_NORMAL_APPROXIMATION",
            "equivalence_test_alpha": 0.05,
            "equivalence_margin_lower": -0.30,
            "equivalence_margin_upper": 0.30,
            "equivalence_ci_level": 0.95,
            "checkpoint_schedule": _checkpoints({"paired_events": 75, "unique_stocks": 40,
                                                  "unique_dates": 25},
                                                 {"paired_events": 150, "unique_stocks": 75,
                                                  "unique_dates": 40}),
        },
        {
            **common, "hypothesis_id": "H4", "hypothesis_version": "H4-v1",
            "definition": "5/5 BREAKOUTは4/5 BREAKOUTより5営業日以内のFailed Breakout率が低い",
            "primary_metric": "5d_failure_rate_5_minus_4",
            "primary_horizon": 5,
            "secondary_metrics": ["10d_failure", "return", "mfe", "mae"],
            "direction": "NEGATIVE", "sesoi": -8.0,
            "sample_gate": {"group_5": 30, "group_4": 30,
                            "unique_stocks": 40, "unique_dates": 15},
            "control_policy": "SIGNAL_GROUP_COMPARISON_NO_SYNTHETIC_PIVOT_CONTROL",
            "cost_model": None,
            "candidate_rule": "effect<=-8pp AND 90pct_CI_upper<0 AND sample_gate",
            "candidate_claim": "DIFFERENCE",
            "confirmed_rule": "negative_direction AND effect<=-8pp AND 95pct_CI AND Holm<0.05",
            "primary_test_type": "TWO_SIDED_ZERO_DIFFERENCE",
            "primary_test_sidedness": "TWO_SIDED",
            "primary_family_p_field": "difference_primary_p",
            "checkpoint_schedule": _checkpoints({"group_5": 25, "group_4": 25,
                                                  "unique_stocks": 40, "unique_dates": 12},
                                                 {"group_5": 50, "group_4": 50,
                                                  "unique_stocks": 70, "unique_dates": 25}),
        },
    ]
    for row in rows:
        row["definition_hash"] = definition_hash(row)
    return rows


def family_registry() -> dict[str, Any]:
    return {
        "family_id": FAMILY_ID, "family_version": FAMILY_VERSION,
        "members": ["H1-v1", "H2-v1", "H3-v1", "H4-v1"],
        "planned_family_size": 4, "correction_method": "HOLM", "alpha": 0.05,
        "family_freeze_date": FREEZE_DATE, "family_holdout_start": HOLDOUT_START,
        "family_close_rule": "90_TRADING_SESSIONS_FROM_COMMON_HOLDOUT_START",
        "family_close_at": FAMILY_CLOSE_AT,
        "incomplete_member_policy": "KEEP_IN_PLANNED_FAMILY_SIZE",
        "holm_n_policy": "PLANNED_FAMILY_SIZE",
        "insufficient_sample_policy": "NO_RAW_P_VALUE",
        "unobserved_test_handling": "ASSUME_GREATER_THAN_OBSERVED_FOR_HOLM",
        "member_removal_after_freeze": "PROHIBITED",
        "member_addition_after_freeze": "PROHIBITED",
        "primary_test_inclusion_rule": "ALL_EXECUTED_PRIMARY_TESTS",
        "family_status": "FROZEN_COLLECTING_HOLDOUT",
        "frozen_at": f"{FREEZE_DATE}T19:00:00+09:00", "closed_at": None,
    }


def validate_family_sync(hypotheses: list[dict], family: dict) -> None:
    versions = [row["hypothesis_version"] for row in hypotheses]
    if versions != family["members"]:
        raise ValueError("frozen family members cannot be added, removed, or reordered")
    if len(versions) != int(family["planned_family_size"]):
        raise ValueError("planned family size mismatch")
    starts = {row.get("holdout_start") for row in hypotheses}
    if starts != {family.get("family_holdout_start")}:
        raise ValueError("all Phase 1 hypothesis holdout_start values must match family_holdout_start")
    dates = {str(row.get("frozen_at", ""))[:10] for row in hypotheses}
    if dates != {family.get("family_freeze_date")}:
        raise ValueError("all Phase 1 hypotheses must be frozen on the family freeze date")
    for row in hypotheses:
        if row.get("definition_hash") != definition_hash(row):
            raise ValueError(f"frozen definition hash mismatch: {row['hypothesis_version']}")


def definition_hash(row: dict[str, Any]) -> str:
    excluded = {"definition_hash", "checkpoint_a_reached_at", "checkpoint_a_evaluated_at",
                "checkpoint_b_reached_at", "checkpoint_b_evaluated_at"}
    payload = {key: value for key, value in deepcopy(row).items() if key not in excluded}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _checkpoints(a: dict, b: dict) -> dict:
    return {
        "A": {"name": "A", "purpose": "DIAGNOSTIC_ONLY", "minimums": a,
              "formal_decision": False},
        "B": {"name": "B", "purpose": "FINAL_CONFIRMATORY", "minimums": b,
              "formal_decision": True},
    }
