from __future__ import annotations

import numpy as np
import pandas as pd

NAMES = {
    "7203": "トヨタ自動車", "6758": "ソニーグループ", "8306": "三菱UFJ FG",
    "9984": "ソフトバンクグループ", "8035": "東京エレクトロン", "6501": "日立製作所",
    "7011": "三菱重工業", "4063": "信越化学工業", "6098": "リクルートHD",
    "7974": "任天堂", "TOPIX": "TOPIX（デモ）",
}


def make_demo_history(code: str, periods: int = 330) -> pd.DataFrame:
    seed = sum(ord(c) for c in code)
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=periods)
    style = seed % 5
    drift = [0.0008, 0.0015, 0.0004, 0.0011, -0.0001][style]
    noise = rng.normal(drift, .014 + style * .001, periods)
    if style in (0, 1, 3):
        noise[-45:] *= np.linspace(1.0, .25, 45)
        noise[-8:] += np.linspace(-.002, .003, 8)
    close = (900 + seed % 2400) * np.exp(np.cumsum(noise))
    gap = rng.normal(0, .004, periods)
    open_ = np.r_[close[0], close[:-1]] * (1 + gap)
    spread = rng.uniform(.004, .025, periods)
    high = np.maximum(open_, close) * (1 + spread / 2)
    low = np.minimum(open_, close) * (1 - spread / 2)
    volume = rng.lognormal(13.1, .55, periods).astype(int)
    if style == 1:
        volume[-1] *= 2
        close[-1] = max(close[-1], high[-21:-1].max() * 1.006)
        high[-1] = max(high[-1], close[-1] * 1.006)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": volume}, index=dates)


def demo_fundamentals(code: str) -> dict:
    seed = sum(ord(c) for c in code)
    return {"eps_growth": float((seed * 7) % 70 - 5),
            "sales_growth": float((seed * 3) % 40 - 3), "source": "DEMO"}

