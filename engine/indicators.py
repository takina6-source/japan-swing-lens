from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, days: int) -> pd.Series:
    return series.rolling(days, min_periods=days).mean()


def ema(series: pd.Series, days: int) -> pd.Series:
    return series.ewm(span=days, adjust=False, min_periods=days).mean()


def rsi(series: pd.Series, days: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / days, adjust=False, min_periods=days).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / days, adjust=False, min_periods=days).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss.ne(0), 100.0)


def true_range(frame: pd.DataFrame) -> pd.Series:
    prev = frame["close"].shift()
    return pd.concat([(frame["high"] - frame["low"]),
                      (frame["high"] - prev).abs(),
                      (frame["low"] - prev).abs()], axis=1).max(axis=1)


def atr(frame: pd.DataFrame, days: int = 14) -> pd.Series:
    return true_range(frame).ewm(alpha=1 / days, adjust=False, min_periods=days).mean()


def returns(series: pd.Series, days: int) -> pd.Series:
    return series.pct_change(days) * 100


def relative_strength(stock: pd.Series, benchmark: pd.Series, days: int = 126) -> pd.Series:
    aligned = pd.concat([stock, benchmark], axis=1).ffill().dropna()
    ratio = aligned.iloc[:, 0] / aligned.iloc[:, 1]
    return ratio.pct_change(days) * 100


def slope(series: pd.Series, lookback: int = 20) -> pd.Series:
    return (series / series.shift(lookback) - 1) * 100


def rolling_percentile(values: pd.Series) -> pd.Series:
    return values.rank(pct=True) * 100


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.sort_index().copy()
    for day in (10, 20, 50, 150, 200):
        df[f"ma{day}"] = sma(df["close"], day)
    df["rsi2"] = rsi(df["close"], 2)
    df["atr14"] = atr(df, 14)
    df["atr_pct"] = df["atr14"] / df["close"] * 100
    df["vol_ma20"] = sma(df["volume"], 20)
    df["volume_ratio"] = df["volume"] / df["vol_ma20"]
    df["trading_value"] = df["close"] * df["volume"]
    for label, day in (("1m", 21), ("3m", 63), ("6m", 126), ("12m", 252)):
        df[f"momentum_{label}"] = returns(df["close"], day)
    df["high52"] = df["high"].rolling(252, min_periods=126).max()
    df["low52"] = df["low"].rolling(252, min_periods=126).min()
    return df


def contraction_widths(frame: pd.DataFrame, windows=(60, 30, 15)) -> list[float]:
    widths = []
    for window in windows:
        part = frame.tail(window)
        if len(part) < window:
            return []
        midpoint = part["close"].mean()
        widths.append(round((part["high"].max() - part["low"].min()) / midpoint * 100, 1))
    return widths

