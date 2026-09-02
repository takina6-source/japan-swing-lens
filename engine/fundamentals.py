from __future__ import annotations

import math

from .models import ConditionResult, Fidelity, Layer, Role, Verdict


def annual_earnings_condition(metrics: dict, cfg: dict) -> ConditionResult:
    """CAN SLIM Aを年次EPS履歴から評価する。

    4期以上かつ各前年比25%以上はSTRICT。3期以上で黒字継続・減益なし・
    3年CAGR 25%以上はPRACTICAL。赤字からの転換や単年異常値は境界扱いにし、
    欠損をFAILへ変換しない。
    """
    c = cfg["oneil"]["annual_earnings"]
    raw = metrics.get("annual_eps") or []
    values = []
    as_of = str(metrics.get("as_of") or "")[:10]
    for row in raw:
        try:
            eps = float(row["eps"])
            year = str(row.get("fiscal_year") or row.get("year"))
            published = str(row.get("published_date") or row.get("filing_date") or "")[:10]
            if as_of and ((published and published > as_of)
                          or (not published and year[:4] > as_of[:4])):
                continue
            if math.isfinite(eps):
                values.append((year, eps))
        except (KeyError, TypeError, ValueError):
            continue
    values = sorted(dict(values).items())
    minimum = int(c["minimum_years"])
    if len(values) < minimum:
        profile = metrics.get("annual_eps_profile") or {}
        reason = profile.get("reason_code")
        detail = f" 原因: {reason}" if reason else ""
        return ConditionResult("annual_earnings", "A: 年次EPS成長", Verdict.NA,
                               Role.REQUIRED, Layer.QUALITY_MOMENTUM,
                               values or None, f"{minimum}期以上", "",
                               Fidelity.PRACTICAL,
                               f"年次EPS履歴が不足（{len(values)}/{minimum}期）。Coverageへ反映。{detail}".strip())
    recent = values[-max(minimum, 4):]
    eps = [v for _, v in recent]
    if eps[-1] > 0 and any(v <= 0 for v in eps[:-1]):
        return ConditionResult("annual_earnings", "A: 年次EPS成長", Verdict.BORDERLINE,
                               Role.REQUIRED, Layer.QUALITY_MOMENTUM, recent,
                               f"3年CAGR {c['cagr_min_pct']}%", "",
                               Fidelity.PRACTICAL, "赤字から黒字へ転換。CAGR比較不能のため境界")
    if any(v <= 0 for v in eps):
        return ConditionResult("annual_earnings", "A: 年次EPS成長", Verdict.FAIL,
                               Role.REQUIRED, Layer.QUALITY_MOMENTUM, recent,
                               "継続黒字", "", Fidelity.PRACTICAL, "年次EPSに赤字を含む")
    growth = [(eps[i] / eps[i - 1] - 1) * 100 for i in range(1, len(eps))]
    years = len(eps) - 1
    cagr = (eps[-1] / eps[0]) ** (1 / years) * 100 - 100 if years else float("nan")
    anomaly = any(abs(v) > float(c["max_single_year_growth_pct"]) for v in growth)
    source = str(metrics.get("annual_eps_source") or "")
    source_is_official = "EDINET" in source or "J-Quants" in source
    strict_source = ((metrics.get("annual_eps_profile") or {}).get("fidelity") == "STRICT"
                     if metrics.get("annual_eps_profile") else source_is_official)
    strict = (strict_source and len(eps) >= 4
              and all(v >= float(c["minimum_each_year_growth_pct"]) for v in growth[-3:])
              and not anomaly)
    practical = (cagr >= float(c["cagr_min_pct"]) and all(v >= 0 for v in growth[-2:])
                 and not anomaly)
    verdict = Verdict.PASS if strict or practical else (Verdict.BORDERLINE if cagr >= 15 else Verdict.FAIL)
    fidelity = Fidelity.STRICT if strict else Fidelity.PRACTICAL
    note = f"{len(eps)}期、CAGR {cagr:.1f}%、前年比 " + "/".join(f"{v:.1f}%" for v in growth)
    if anomaly:
        verdict, note = Verdict.BORDERLINE, note + "。単年の異常な伸びを含むため境界"
    return ConditionResult("annual_earnings", "A: 年次EPS成長", verdict,
                           Role.REQUIRED, Layer.QUALITY_MOMENTUM,
                           round(cagr, 2), c["cagr_min_pct"], "%", fidelity, note)
