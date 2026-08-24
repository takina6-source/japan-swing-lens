from __future__ import annotations

import math
from typing import Any
from ..models import ConditionResult, Fidelity, Layer, Role, Verdict


def ok(key: str, label: str, passed: bool | None, role: Role, layer: Layer,
       value: Any = None, reference: Any = None, unit: str = "",
       fidelity: Fidelity = Fidelity.PRACTICAL, note: str = "") -> ConditionResult:
    missing = passed is None or (isinstance(value, float) and math.isnan(value))
    verdict = Verdict.NA if missing else (Verdict.PASS if passed else Verdict.FAIL)
    return ConditionResult(key, label, verdict, role, layer, value, reference, unit, fidelity, note)


def tri(key: str, label: str, value: float | None, good, borderline, role: Role,
        layer: Layer, reference: Any = None, unit: str = "",
        fidelity: Fidelity = Fidelity.PRACTICAL, note: str = "") -> ConditionResult:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        verdict = Verdict.NA
    elif good(value):
        verdict = Verdict.PASS
    elif borderline(value):
        verdict = Verdict.BORDERLINE
    else:
        verdict = Verdict.FAIL
    return ConditionResult(key, label, verdict, role, layer, value, reference, unit, fidelity, note)

