from __future__ import annotations

import hashlib
import math
import random
import statistics
from collections import defaultdict
from typing import Callable, Iterable


def mean(values: Iterable[float]) -> float | None:
    numbers = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.fmean(numbers) if numbers else None


def normal_two_sided_p(effect: float | None, standard_error: float | None) -> float | None:
    if effect is None or standard_error is None or standard_error <= 0:
        return None
    z = abs(float(effect) / float(standard_error))
    return math.erfc(z / math.sqrt(2.0))


def tost_p(effect: float | None, standard_error: float | None,
           lower_margin: float, upper_margin: float) -> float | None:
    """Normal-approximation TOST p-value (max of the two one-sided p-values)."""
    if effect is None or standard_error is None or standard_error <= 0:
        return None
    lower_z = (float(effect) - float(lower_margin)) / float(standard_error)
    upper_z = (float(upper_margin) - float(effect)) / float(standard_error)
    lower_p = 0.5 * math.erfc(lower_z / math.sqrt(2.0))
    upper_p = 0.5 * math.erfc(upper_z / math.sqrt(2.0))
    return max(lower_p, upper_p)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return center - half, center + half


def cluster_bootstrap_ci(rows: list[dict], value_key: str, cluster_key: str,
                         level: float, iterations: int = 2000,
                         transform: Callable[[list[float]], float] | None = None,
                         seed_label: str = "research-v1") -> tuple[float | None, float | None]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(value_key)
        cluster = row.get(cluster_key)
        if value is not None and cluster not in (None, ""):
            number = float(value)
            if math.isfinite(number):
                groups[str(cluster)].append(number)
    keys = sorted(groups)
    if len(keys) < 2:
        return None, None
    statistic = transform or statistics.fmean
    seed = int.from_bytes(hashlib.sha256(
        f"{seed_label}|{value_key}|{cluster_key}|{level}".encode()).digest(), "big")
    rng = random.Random(seed)
    estimates = []
    for _ in range(iterations):
        sampled = [rng.choice(keys) for _ in keys]
        values = [value for key in sampled for value in groups[key]]
        estimates.append(float(statistic(values)))
    estimates.sort()
    tail = (1 - level) / 2
    low_index = max(0, min(len(estimates) - 1, int(tail * len(estimates))))
    high_index = max(0, min(len(estimates) - 1, int((1 - tail) * len(estimates)) - 1))
    return estimates[low_index], estimates[high_index]


def cluster_bootstrap_statistic(rows: list[dict], cluster_key: str,
                                statistic: Callable[[list[dict]], float | None],
                                level: float, iterations: int = 2000,
                                seed_label: str = "research-v1") -> dict:
    eligible = [row for row in rows if row.get(cluster_key) not in (None, "")]
    estimate = statistic(eligible) if eligible else None
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in eligible:
        groups[str(row[cluster_key])].append(row)
    keys = sorted(groups)
    if estimate is None or len(keys) < 2:
        return {"estimate": estimate, "ci_lower": None, "ci_upper": None,
                "standard_error": None, "cluster_count": len(keys)}
    seed = int.from_bytes(hashlib.sha256(
        f"{seed_label}|{cluster_key}|{level}".encode()).digest(), "big")
    rng = random.Random(seed)
    estimates = []
    for _ in range(iterations):
        sample_keys = [rng.choice(keys) for _ in keys]
        sampled = [row for key in sample_keys for row in groups[key]]
        value = statistic(sampled)
        if value is not None and math.isfinite(float(value)):
            estimates.append(float(value))
    if len(estimates) < 2:
        return {"estimate": estimate, "ci_lower": None, "ci_upper": None,
                "standard_error": None, "cluster_count": len(keys)}
    estimates.sort()
    tail = (1 - level) / 2
    low_index = max(0, min(len(estimates) - 1, int(tail * len(estimates))))
    high_index = max(0, min(len(estimates) - 1, int((1 - tail) * len(estimates)) - 1))
    return {"estimate": float(estimate), "ci_lower": estimates[low_index],
            "ci_upper": estimates[high_index],
            "standard_error": statistics.stdev(estimates),
            "cluster_count": len(keys)}


def holm_adjust(p_values: dict[str, float | None], planned_family_size: int) -> dict[str, float | None]:
    """Holm adjustment with missing tests retained in the planned family size."""
    observed = sorted(((key, float(value)) for key, value in p_values.items()
                       if value is not None), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float | None] = {key: None for key in p_values}
    running = 0.0
    for index, (key, value) in enumerate(observed):
        multiplier = max(1, planned_family_size - index)
        running = max(running, min(1.0, value * multiplier))
        adjusted[key] = running
    return adjusted
