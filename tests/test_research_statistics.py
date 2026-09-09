import pytest

from engine.research.statistics import (cluster_bootstrap_statistic, holm_adjust,
                                        tost_p, wilson_interval)


def test_cluster_bootstrap_is_two_sided_and_date_clustered():
    rows = [{"date": f"2026-01-{day:02d}", "value": float(day)}
            for day in range(1, 11)]
    result = cluster_bootstrap_statistic(
        rows, "date", lambda sample: sum(row["value"] for row in sample) / len(sample), .90)
    assert result["ci_lower"] < result["estimate"] < result["ci_upper"]
    assert result["cluster_count"] == 10


def test_wilson_interval_and_tost_are_not_zero_difference_shortcuts():
    low, high = wilson_interval(5, 10)
    assert 0 < low < .5 < high < 1
    assert tost_p(0.0, .05, -.3, .3) < .05
    assert tost_p(0.0, .5, -.3, .3) > .05


def test_holm_missing_values_remain_null_not_fake_p_one():
    result = holm_adjust({"H1": .01, "H2": None}, 4)
    assert result["H1"] == pytest.approx(.04)
    assert result["H2"] is None
