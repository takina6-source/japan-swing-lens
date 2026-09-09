import pandas as pd

from engine.research.entry import simulate_entry_methods


def _frame():
    dates = pd.bdate_range("2026-09-09", periods=3)
    return pd.DataFrame({
        "open": [99.0, 103.0, 105.0],
        "high": [102.0, 108.0, 107.0],
        "low": [96.0, 94.0, 101.0],
        "close": [101.0, 106.0, 104.0],
        "volume": [1000.0] * 3,
        "atr14": [5.0] * 3,
    }, index=dates)


def test_three_entry_methods_have_strict_price_basis_and_next_open():
    result = simulate_entry_methods(_frame(), "2026-09-09", 100.0,
                                    "2026-09-08", 97.0)
    assert result["T_PLUS_1_OPEN"].entry_date == "2026-09-10"
    assert result["T_PLUS_1_OPEN"].entry_price == 103.0
    assert result["T_PLUS_1_OPEN"].price_basis == "OPEN"
    assert result["T_CLOSE_REFERENCE"].entry_price == 101.0
    assert result["T_CLOSE_REFERENCE"].reference_only is True
    assert result["T_CLOSE_REFERENCE"].price_basis == "REFERENCE"
    assert result["PIVOT_STOP"].entry_price == 100.0
    assert result["PIVOT_STOP"].price_basis == "PIVOT_TRIGGER"


def test_pivot_stop_requires_prior_known_pivot_and_does_not_guess_path():
    ambiguous = simulate_entry_methods(_frame(), "2026-09-09", 100.0,
                                       "2026-09-08", 97.0)["PIVOT_STOP"]
    assert ambiguous.path_ambiguous is True
    same_day = simulate_entry_methods(_frame(), "2026-09-09", 100.0,
                                      "2026-09-09", 97.0)["PIVOT_STOP"]
    assert same_day.eligible is False
    assert same_day.reason == "PIVOT_NOT_KNOWN_BEFORE_SESSION"


def test_intraday_diagnostics_come_from_daily_bars_only():
    result = simulate_entry_methods(_frame(), "2026-09-09", 100.0,
                                    "2026-09-08", 97.0)["T_PLUS_1_OPEN"]
    assert result.gap_pct == (103 / 101 - 1) * 100
    assert result.gap_atr == (103 - 101) / 5
    assert result.gap_r == (103 - 101) / (101 - 97)
    assert result.breakout_range_atr == (102 - 96) / 5
    assert result.false_breakout is False
