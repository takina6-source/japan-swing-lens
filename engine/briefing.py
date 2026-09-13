"""Offline, Core-only Morning Brief adapter. No database or analysis imports."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from engine.simple_changes import briefing_summary

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "morning_brief.schema.json"
METHODS = ("Minervini", "Qullamaggie", "CAN SLIM", "Weinstein", "Darvas", "Connors")
STATES = ("BREAKOUT", "BREAKOUT WATCH", "SETUP FORMING", "PULLBACK", "EXTENDED", "FAILED", "NOT QUALIFIED")
NUMBERS = {
    "close": "price", "aligned_count": "aligned_strategy_count",
    "breakout_count": "breakout_strategy_count", "confluence": "confluence",
    "pivot": "pivot", "momentum_percentile": "momentum_percentile",
    "avg_trading_value_20d": "trading_value_20d", "current_trading_value": "trading_value",
    "trading_value_ratio": "trading_value_ratio", "coverage": "coverage",
}
TEXTS = {key: key for key in ("name", "pivot_type", "pivot_basis", "pivot_fidelity",
                              "consensus_pivot_fidelity", "confidence")}
TEXTS.update(consensus_state="state", liquidity_state="liquidity_level")
PLAN = {"entry_zone_low": "entry_low", "entry_zone_high": "entry_high",
        "stop": "stop", "target_1r": "target_1r", "target_2r": "target_2r"}
DEFINITIONS = {
    "rank": "snapshot全候補内の1始まり順位。再ランキングなし。",
    "close": "円。調整済み日足終値。リアルタイム価格ではない。",
    "analysis_date": "銘柄別detail.as_of。全体as_ofや生成日時とは別。",
    "aligned_count": "5手法のBREAKOUT/WATCH/SETUP FORMING数。Confluenceとは別。",
    "breakout_count": "5手法中BREAKOUT数。Connorsは分母に含めない。",
    "confluence": "既存Coreの総合合致数。Adapterでは再評価しない。",
    "pivot_distance_pct": "(pivot/close-1)*100。正ならPivotが終値より上。%単位。",
    "risk_per_share": "max(entry_low,min(close,entry_high))-stop。円/株。前提確認可能時のみ。",
    "trade_plan": "entry/stop/targetは円。既存の参考価格。実現利益や注文ではない。",
    "momentum_percentile": "分析母集団内の6か月騰落率percentile。0～100。",
    "trading_value": "円。終値×出来高の概算。20本平均は当日を含む。",
    "trading_value_ratio": "当日概算売買代金/当日を含む20本平均。倍。",
    "volume_ratio": "当日出来高/当日を含む20本平均。倍。Core条件値由来。",
    "coverage": "6手法の条件非N/A率。%。市場カバー率ではない。",
    "confidence": "既存条件品質指標。利益確率ではない。",
    "pivot_fidelity": "最寄りPivotの品質。consensus_pivot_fidelityとは別。",
    "conditions": "既存評価の観測結果。○/△/×/N/Aを保持。原票の全履歴は含まない。",
    "provenance": "hashは入力再現用。同run生成の証明ではない。SAME_JOB_ATTESTEDは呼出元の保証。",
}


def _text(value):
    if not isinstance(value, str):
        return None
    # Error messages can contain local paths or credentials; never expose them.
    if re.search(r"(?:/Users/|/home/|/private/|/tmp/|[A-Za-z]:\\|(?:api[_-]?key|token|password)\s*[=:])", value, re.I):
        return None
    return value


def _number(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) else None
    except OverflowError:
        return None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _day(value):
    try:
        return value if isinstance(value, str) and date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return value if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=True).encode()).hexdigest()


def _issue(issues, field, reason):
    item = {"field": field, "reason": reason}
    if item not in issues:
        issues.append(item)


def _get(data, key, issues, field=None, numeric=False):
    value = (_number if numeric else _text)(data.get(key))
    if value is None:
        reason = "MISSING_FIELD" if key not in data else (
            "STRUCTURAL_NA" if data[key] is None else "INVALID_OR_NONFINITE_VALUE")
        _issue(issues, field or key, reason)
    return value


def _condition_value(value, issues, field):
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str):
        cleaned = _text(value)
    elif isinstance(value, (list, tuple)):
        # Existing annual EPS condition can hold a short year/value series.
        return [_condition_value(v, issues, field) for v in value]
    else:
        cleaned = _number(value)  # Objects are not a public passthrough channel.
    if cleaned is None:
        _issue(issues, field, "INVALID_OR_NONFINITE_VALUE")
    return cleaned


def _detail_matches(candidate, detail, as_of):
    if not isinstance(detail, dict) or detail.get("code") != candidate["code"]:
        return False
    if detail.get("as_of") is not None and (not _day(detail["as_of"]) or detail["as_of"] > as_of):
        return False
    common = set(NUMBERS.values()) | set(TEXTS.values()) | {"methods", "trade_plan", "liquid"}
    for key in common:
        if key in candidate and (key not in detail or _hash(candidate[key]) != _hash(detail[key])):
            return False
    strategies = detail.get("strategies", {})
    if not isinstance(strategies, dict):
        return False
    for name, state in (candidate.get("methods") or {}).items():
        if name in strategies and (not isinstance(strategies[name], dict) or strategies[name].get("state") != state):
            return False
    if any(name in strategies and not isinstance(strategies[name], dict) for name in METHODS):
        return False
    metrics = detail.get("metrics", {})
    if not isinstance(metrics, dict):
        return False
    if "price" in metrics and _hash(metrics["price"]) != _hash(candidate.get("price")):
        return False
    return True


def _stock(candidate, detail, rank, as_of, config, trusted, detail_error=None):
    issues = []
    if detail_error or detail is None:
        _issue(issues, "detail", detail_error or "DETAIL_MISSING")
        detail = {}
    elif not _detail_matches(candidate, detail, as_of):
        _issue(issues, "detail", "DETAIL_MISMATCH")
        detail = {}
    row = {"rank": rank, "code": candidate["code"]}
    row.update({out: _get(candidate, key, issues, out, True) for out, key in NUMBERS.items()})
    row.update({out: _get(candidate, key, issues, out) for out, key in TEXTS.items()})
    for key in ("aligned_count", "breakout_count", "confluence"):
        value = row[key]
        if value is not None and (not 0 <= value <= 5 or value != int(value)):
            row[key] = None
            _issue(issues, key, "INVALID_COUNT")
    for key in ("coverage", "momentum_percentile"):
        if row[key] is not None and not 0 <= row[key] <= 100:
            row[key] = None
            _issue(issues, key, "INVALID_PERCENTAGE")
    if row["consensus_state"] not in STATES:
        row["consensus_state"] = None
        _issue(issues, "consensus_state", "INVALID_STATE")
    row["source"] = _get(detail, "source", issues)
    row["analysis_date"] = _day(detail.get("as_of"))
    row["date_status"] = "UNKNOWN" if row["analysis_date"] is None else (
        "OLDER_THAN_SNAPSHOT" if row["analysis_date"] < as_of else "MATCHES_SNAPSHOT")
    if row["date_status"] != "MATCHES_SNAPSHOT":
        _issue(issues, "analysis_date", row["date_status"])
    row["liquid"] = candidate.get("liquid") if type(candidate.get("liquid")) is bool else None
    if row["liquid"] is None:
        _issue(issues, "liquid", "MISSING_OR_INVALID_FIELD")
    price, pivot = row["close"], row["pivot"]
    row["pivot_distance_pct"] = _number((pivot / price - 1) * 100) if price and price > 0 and pivot is not None else None
    if row["pivot_distance_pct"] is None:
        _issue(issues, "pivot_distance_pct", "DERIVATION_UNAVAILABLE")
    plan = candidate.get("trade_plan")
    plan = plan if isinstance(plan, dict) else {}
    row["trade_plan"] = output_plan = {
        key: _get(plan, key, issues, "trade_plan." + key) for key in ("status", "basis", "warning")}
    output_plan.update({out: _get(plan, key, issues, "trade_plan." + out, True) for out, key in PLAN.items()})
    output_plan["risk_per_share"] = None
    low, high, stop = (output_plan[key] for key in ("entry_zone_low", "entry_zone_high", "stop"))
    if output_plan["status"] in ("見送り", "算出不能"):
        _issue(issues, "trade_plan", "PLAN_SKIPPED" if output_plan["status"] == "見送り" else "PLAN_UNAVAILABLE")
    elif (trusted and config.get("strategy_version") == "pivot-consensus-v1"
          and output_plan["status"] in ("押し目候補", "エントリー帯", "指値待機")
          and all(v is not None and v > 0 for v in (price, low, high, stop)) and low <= high):
        entry = max(low, min(price, high))
        risk = _number(entry - stop)
        multipliers = _mapping(config.get("trade_plan"))
        consistent = risk is not None and risk > 0
        for target, setting in (("target_1r", "first_target_r_multiple"), ("target_2r", "main_target_r_multiple")):
            multiple = _number(multipliers.get(setting))
            actual = output_plan[target]
            if not consistent or actual is None or multiple is None or multiple <= 0 or not math.isclose(actual, entry + risk * multiple, rel_tol=1e-8, abs_tol=1e-6):
                consistent = False
        if consistent:
            output_plan["risk_per_share"] = risk
    if output_plan["risk_per_share"] is None:
        _issue(issues, "trade_plan.risk_per_share", "DERIVATION_UNAVAILABLE")
    methods = candidate.get("methods") or {}
    details = detail.get("strategies") or {}
    row["strategies"] = {}
    row["volume_ratio"] = None
    for name in METHODS:
        strategy = details.get(name) or {}
        state = methods.get(name)
        if state not in STATES:
            state = None
            _issue(issues, "strategies." + name, "MISSING_OR_INVALID_STATE")
        conditions = strategy.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            _issue(issues, "strategies." + name, "CONDITIONS_UNAVAILABLE")
            conditions = []
        cleaned = []
        for condition in conditions:
            if not isinstance(condition, dict):
                _issue(issues, "strategies." + name, "INVALID_CONDITION")
                continue
            prefix = "strategies." + name + ".conditions"
            public = {key: _get(condition, key, issues, prefix + "." + key)
                      for key in ("key", "label", "verdict", "role", "layer", "unit", "fidelity", "note")}
            for key in ("value", "reference"):
                public[key] = _condition_value(condition.get(key), issues, prefix + "." + key)
                if key not in condition:
                    _issue(issues, prefix + "." + key, "MISSING_FIELD")
                elif condition[key] is None:
                    _issue(issues, prefix + "." + key, "STRUCTURAL_NA")
            if public["verdict"] not in ("○", "△", "×", "N/A"):
                public["verdict"] = None
                _issue(issues, prefix, "INVALID_VERDICT")
            if public["verdict"] == "N/A":
                _issue(issues, prefix + "." + (public["key"] or "unknown"), "CONDITION_NA")
            cleaned.append(public)
            if name == "Qullamaggie" and public["key"] == "dryup":
                row["volume_ratio"] = _number(public["value"])
        row["strategies"][name] = {"state": state, "conditions": cleaned}
    if row["volume_ratio"] is None:
        _issue(issues, "volume_ratio", "CORE_CONDITION_UNAVAILABLE")
    if row["confidence"] == "LOW":
        _issue(issues, "confidence", "LOW_CONFIDENCE")
    if row["coverage"] is not None and row["coverage"] < 100:
        _issue(issues, "coverage", "COVERAGE_INCOMPLETE")
    if row["pivot_fidelity"] == "PROXY":
        _issue(issues, "pivot_fidelity", "PROXY_FIDELITY")
    row["issues"] = issues
    row["status"] = "PARTIAL" if issues else "OK"
    return row


def build_brief(snapshot, details, config, *, generated_at, limit=20,
                same_run_config=False, detail_errors=None):
    """Pure transformation. same_run_config is a caller attestation, not inference."""
    if type(limit) is not int or limit < 0:
        raise ValueError("INVALID_LIMIT")
    result = {
        "schema_version": "1.0", "adapter_version": "1.0", "status": "UNAVAILABLE",
        "generated_at": generated_at, "snapshot_generated_at": None, "as_of": None,
        "scope": None, "declared_count": None, "candidate_count": 0, "output_count": 0,
        "limit": limit, "benchmark_source": None,
        "versions": {"logic_version": None, "strategy_version": None,
                     "threshold_version": None, "status": "UNVERIFIED"},
        "input_hashes": {"snapshot": _hash(snapshot), "details": {}, "config": None},
        "definitions": dict(DEFINITIONS), "issues": [], "stocks": [],
    }
    issues = result["issues"]
    if not isinstance(snapshot, dict):
        _issue(issues, "snapshot", "INVALID_SNAPSHOT")
        return result
    rows = snapshot.get("candidates")
    if (not isinstance(rows, list) or not rows
            or any(not isinstance(r, dict) or not isinstance(r.get("code"), str)
                   or not re.fullmatch(r"[0-9A-Z]{4}", r["code"])
                   or ("methods" in r and not isinstance(r["methods"], dict)) for r in rows)):
        _issue(issues, "snapshot", "EMPTY_OR_INVALID_CANDIDATES")
        return result
    if len({r["code"] for r in rows}) != len(rows):
        _issue(issues, "snapshot", "DUPLICATE_CODE")
        return result
    result["as_of"] = _day(snapshot.get("as_of"))
    result["snapshot_generated_at"] = _timestamp(snapshot.get("generated_at"))
    if result["as_of"] is None or result["snapshot_generated_at"] is None:
        _issue(issues, "snapshot", "INVALID_SNAPSHOT_DATE")
        return result
    if result["as_of"] > result["snapshot_generated_at"][:10]:
        _issue(issues, "snapshot", "INVALID_SNAPSHOT_DATE")
        return result
    result["candidate_count"] = len(rows)
    count = snapshot.get("universe_count")
    result["declared_count"] = count if type(count) is int and count >= 0 else None
    if result["declared_count"] != len(rows):
        _issue(issues, "declared_count", "COUNT_MISMATCH")
    result["scope"] = _get(snapshot, "scope", issues)
    result["benchmark_source"] = _get(snapshot, "benchmark_source", issues)
    if result["benchmark_source"] and ("代替" in result["benchmark_source"] or "demo" in result["benchmark_source"].lower()):
        _issue(issues, "benchmark_source", "BENCHMARK_FALLBACK")
    if snapshot.get("errors"):
        _issue(issues, "snapshot", "UPSTREAM_ERRORS_REPORTED")
    config = config if isinstance(config, dict) else {}
    versions = result["versions"]
    versions["logic_version"] = _text(snapshot.get("logic_version"))
    trusted = (same_run_config and versions["logic_version"] is not None
               and versions["logic_version"] == config.get("logic_version")
               and all(_text(config.get(k)) for k in ("strategy_version", "threshold_version")))
    selected_config = {k: _text(config.get(k)) for k in ("logic_version", "strategy_version", "threshold_version")}
    selected_config["trade_plan"] = {k: _number(_mapping(config.get("trade_plan")).get(k))
                                    for k in ("first_target_r_multiple", "main_target_r_multiple")}
    result["input_hashes"]["config"] = _hash(selected_config)
    if trusted:
        versions.update({k: config[k] for k in ("strategy_version", "threshold_version")})
        versions["status"] = "SAME_JOB_ATTESTED"
    else:
        _issue(issues, "versions", "VERSION_UNVERIFIED")
    for rank, candidate in enumerate(rows[:limit] if limit else rows, 1):
        code = candidate["code"]
        detail = details.get(code)
        result["input_hashes"]["details"][code] = _hash(detail) if detail is not None else None
        result["stocks"].append(_stock(candidate, detail, rank, result["as_of"], config, trusted,
                                        (detail_errors or {}).get(code)))
    result["output_count"] = len(result["stocks"])
    result["status"] = "PARTIAL" if issues or any(r["issues"] for r in result["stocks"]) else "OK"
    return result


def validate_brief(payload):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
    json.dumps(payload, allow_nan=False)


def _atomic_write(path, payload):
    validate_brief(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".brief-", delete=False) as fh:
            tmp = Path(fh.name)
            json.dump(payload, fh, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def export_brief(dashboard_root, output_dir, config, *, generated_at, limit=20, same_run_config=False):
    """Read only the selected local inputs; replace only briefing/latest.json."""
    root, output = Path(dashboard_root).resolve(), Path(output_dir).resolve()
    if output == root or output.is_relative_to(root / "data") or output in root.parents:
        raise ValueError("OUTPUT_OVERLAPS_INPUT")
    target = output / "latest.json"
    if target.is_symlink():
        raise ValueError("SYMLINK_OUTPUT")
    try:
        snapshot = json.loads((root / "data" / "snapshot.json").read_text(encoding="utf-8"))
        # Validate shape before using any candidate as a filename.
        initial = build_brief(snapshot, {}, config, generated_at=generated_at, limit=limit,
                              same_run_config=same_run_config)
        details, errors = {}, {}
        if initial["status"] != "UNAVAILABLE":
            rows = snapshot["candidates"][:limit] if limit else snapshot["candidates"]
            directory = root / "data" / "details"
            for row in rows:
                path = directory / (row["code"] + ".json")
                if directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory.resolve():
                    errors[row["code"]] = "DETAIL_UNSAFE_PATH"
                    continue
                try:
                    details[row["code"]] = json.loads(path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    errors[row["code"]] = "DETAIL_MISSING"
                except (OSError, ValueError):
                    errors[row["code"]] = "DETAIL_UNREADABLE"
            # A concurrent rewrite must not silently combine two input generations.
            reread = json.loads((root / "data" / "snapshot.json").read_text(encoding="utf-8"))
            if _hash(reread) != _hash(snapshot):
                raise ValueError("INPUT_CHANGED")
        payload = build_brief(snapshot, details, config, generated_at=generated_at, limit=limit,
                              same_run_config=same_run_config, detail_errors=errors)
        # What Changed is optional and must never make the Core brief unavailable.
        try:
            changes = json.loads((output / "what-changed.json").read_text(encoding="utf-8"))
            if changes.get("schema_version") == "simple-what-changed-v1":
                payload["what_changed"] = briefing_summary(changes)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        validate_brief(payload)
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError):
        payload = build_brief(None, {}, {}, generated_at=generated_at, limit=max(0, limit))
        payload["issues"] = [{"field": "input", "reason": "INPUT_UNREADABLE_OR_CHANGED"}]
    except Exception:
        # Schema failures must not publish partially transformed stock rows.
        payload = build_brief(None, {}, {}, generated_at=generated_at, limit=max(0, limit))
        payload["issues"] = [{"field": "adapter", "reason": "TRANSFORM_FAILED"}]
    try:
        _atomic_write(target, payload)
    except Exception:
        # An old successful file must not survive a failed current export.
        target.unlink(missing_ok=True)
        raise
    return payload
