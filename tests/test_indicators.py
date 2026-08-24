import numpy as np
import pandas as pd
from engine.indicators import atr, contraction_widths, relative_strength, rsi, sma


def test_sma():
    s = pd.Series([1, 2, 3, 4])
    assert sma(s, 3).iloc[-1] == 3


def test_rsi_bounds_and_direction():
    rising = pd.Series(range(1, 30), dtype=float)
    falling = pd.Series(range(30, 1, -1), dtype=float)
    assert rsi(rising, 2).iloc[-1] == 100
    assert rsi(falling, 2).iloc[-1] < 1


def test_atr_true_range_gap():
    df = pd.DataFrame({"high": [10, 14, 13], "low": [8, 12, 11], "close": [9, 13, 12]})
    out = atr(df, 2)
    assert out.iloc[-1] > 0


def test_relative_strength():
    idx = pd.bdate_range("2024-01-01", periods=140)
    stock = pd.Series(np.linspace(100, 200, 140), index=idx)
    bench = pd.Series(np.linspace(100, 120, 140), index=idx)
    assert relative_strength(stock, bench, 126).iloc[-1] > 0


def test_vcp_contraction_widths():
    idx = pd.bdate_range("2024-01-01", periods=60)
    center = np.linspace(100, 110, 60)
    amp = np.linspace(15, 1, 60)
    df = pd.DataFrame({"high": center + amp, "low": center - amp, "close": center}, index=idx)
    widths = contraction_widths(df)
    assert widths[0] > widths[1] > widths[2]

