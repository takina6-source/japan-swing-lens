from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache
import pandas as pd

from ..config import ROOT

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _yfinance_client():
    """yfinanceの内部SQLiteを、必ず書き込めるアプリ専用領域へ置く。"""
    import yfinance as yf
    cache_dir = ROOT / "data" / "yfinance-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # macOSの標準Cacheディレクトリを開けない環境でも取得を継続できるようにする。
    yf.set_tz_cache_location(str(cache_dir))
    return yf


class YahooProvider:
    name = "Yahoo Finance (補完・非公式)"

    def history(self, code: str, period: str = "2y") -> pd.DataFrame:
        yf = _yfinance_client()
        # YahooではTOPIX指数記号が安定しないため、JPXがTOPIX連動ETFとして
        # 掲載する1306を無料ベンチマークの代理系列にする。
        symbol = "1306.T" if code == "TOPIX" else f"{code}.T"
        raw = yf.download(symbol, period=period, interval="1d", auto_adjust=True,
                          progress=False, threads=False, timeout=12)
        if raw.empty:
            raise RuntimeError(f"{symbol} の日足を取得できませんでした")
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        out = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        out.index = pd.to_datetime(out.index).tz_localize(None)
        return out.dropna(subset=["close"])

    def histories(self, codes: list[str], period: str = "2y", batch_size: int = 80,
                  on_batch: Callable[[int, int], None] | None = None):
        """複数銘柄を分割取得する。失敗銘柄は例外で全体を止めずerrorsへ返す。"""
        yf = _yfinance_client()
        frames, errors = {}, []
        total = (len(codes) + batch_size - 1) // batch_size
        for batch_no, start in enumerate(range(0, len(codes), batch_size), 1):
            part = codes[start:start + batch_size]
            symbols = [f"{code}.T" for code in part]
            try:
                raw = yf.download(symbols, period=period, interval="1d", auto_adjust=True,
                                  group_by="ticker", progress=False, threads=True, timeout=20)
                if raw.empty:
                    raise RuntimeError("空の応答")
                for code, symbol in zip(part, symbols):
                    try:
                        item = raw[symbol] if isinstance(raw.columns, pd.MultiIndex) else raw
                        item = item.rename(columns=str.lower)
                        out = item[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
                        out.index = pd.to_datetime(out.index).tz_localize(None)
                        if not out.empty:
                            frames[code] = out
                        else:
                            errors.append(f"{code}: 株価なし")
                    except Exception as exc:
                        errors.append(f"{code}: {exc}")
            except Exception as exc:
                errors.extend(f"{code}: batch取得失敗 {exc}" for code in part)
            if on_batch:
                on_batch(batch_no, total)
        return frames, errors

    def fundamentals(self, code: str) -> dict:
        yf = _yfinance_client()
        try:
            info = yf.Ticker(f"{code}.T").get_info()
            return {"eps_growth": _pct(info.get("earningsGrowth")),
                    "sales_growth": _pct(info.get("revenueGrowth")), "source": self.name}
        except Exception as exc:
            log.warning("Yahoo fundamentals failed for %s: %s", code, exc)
            return {"eps_growth": None, "sales_growth": None, "source": self.name}


def _pct(value):
    return None if value is None else float(value) * 100
