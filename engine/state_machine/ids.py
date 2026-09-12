"""Canonical deterministic identifiers for Phase 2A."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Any

from .models import validate_code


def _normalize(value: Any, *, allow_float: bool) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not allow_float:
            raise ValueError("float is not permitted in an identity mint request")
        if not math.isfinite(value):
            raise ValueError("non-finite float is not canonical")
        return value
    if isinstance(value, list) or isinstance(value, tuple):
        return [_normalize(item, allow_float=allow_float) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("canonical JSON keys must be strings")
        return {
            unicodedata.normalize("NFC", key): _normalize(item, allow_float=allow_float)
            for key, item in value.items()
        }
    raise ValueError(f"unsupported canonical JSON type: {type(value).__name__}")


def canonical_json(value: Any, *, allow_float: bool = True) -> str:
    normalized = _normalize(value, allow_float=allow_float)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def hash_payload(value: Any, *, allow_float: bool = True) -> str:
    return sha256_hex(canonical_json(value, allow_float=allow_float))


def mint_core_setup_uid(
    *, code: str, identity_epoch: str, origin_observation_uid: str,
    origin_slot: str = "core-primary", identity_schema_version: int = 1,
) -> tuple[str, str, str]:
    validate_code(code)
    payload = {
        "code": code,
        "identity_epoch": identity_epoch,
        "identity_schema_version": identity_schema_version,
        "namespace": "CORE_SETUP",
        "origin_observation_uid": origin_observation_uid,
        "origin_slot": origin_slot,
    }
    canonical = canonical_json(payload, allow_float=False)
    full_hash = sha256_hex(canonical)
    return f"csu1:{code}:{full_hash[:40]}", full_hash, canonical


def mint_strategy_setup_uid(
    *, code: str, strategy_slug: str, identity_epoch: str,
    origin_observation_uid: str, identity_schema_version: int = 1,
) -> tuple[str, str, str]:
    validate_code(code)
    if not strategy_slug or not strategy_slug.replace("_", "").isalnum():
        raise ValueError("invalid strategy slug")
    payload = {
        "code": code,
        "identity_epoch": identity_epoch,
        "identity_schema_version": identity_schema_version,
        "namespace": "STRATEGY_SETUP",
        "origin_observation_uid": origin_observation_uid,
        "strategy_slug": strategy_slug,
    }
    canonical = canonical_json(payload, allow_float=False)
    full_hash = sha256_hex(canonical)
    return f"ssu1:{code}:{strategy_slug}:{full_hash[:40]}", full_hash, canonical


def observation_uid(code: str, run_id: str, input_sha256: str) -> str:
    validate_code(code)
    digest = sha256_hex(f"smo1\x1f{run_id}\x1f{code}\x1f{input_sha256}")
    return f"obs1:{code}:{digest[:40]}"


def decision_uid(code: str, observation_id: str, slot: str) -> str:
    validate_code(code)
    digest = sha256_hex(f"sid1\x1f{observation_id}\x1f{slot}")
    return f"sid1:{code}:{digest[:40]}"


def event_uid(
    *, code: str, core_setup_uid: str, event_type: str,
    observation_uid_value: str | None, state_machine_version: str,
    occurrence_ordinal: int,
) -> tuple[str, str]:
    validate_code(code)
    request = "\x1f".join(
        (
            "sev1", core_setup_uid, event_type,
            observation_uid_value or "NO_OBSERVATION",
            state_machine_version, str(occurrence_ordinal),
        )
    )
    full_hash = sha256_hex(request)
    return f"sev1:{code}:{full_hash[:40]}", full_hash


def run_uid(expected_market_date: str, input_sha256: str, lineage: str) -> str:
    digest = sha256_hex(f"smrun1\x1f{expected_market_date}\x1f{input_sha256}\x1f{lineage}")
    return f"smrun1:{expected_market_date.replace('-', '')}:{digest[:24]}"
