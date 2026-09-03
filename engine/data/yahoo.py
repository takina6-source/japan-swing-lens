from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
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

    def annual_eps(self, code: str) -> list[dict]:
        """Yahooの年次損益計算書から分割調整後のEPS系列を取得する。

        非公式経路のため、保存時はPRACTICALデータとして扱う。
        """
        yf = _yfinance_client()
        statement = yf.Ticker(f"{code}.T").get_income_stmt(freq="yearly")
        if statement is None or statement.empty:
            return []
        row_name = next((name for name in ("Basic EPS", "BasicEPS", "Diluted EPS", "DilutedEPS")
                         if name in statement.index), None)
        if row_name is None:
            return []
        rows = []
        for column, value in statement.loc[row_name].items():
            try:
                if pd.notna(value):
                    rows.append({"fiscal_year": str(pd.Timestamp(column).year),
                                 "eps": float(value), "filing_date": None,
                                 "source": self.name, "fidelity": "PRACTICAL",
                                 "period_type": "FY", "concept": row_name,
                                 "priority": 50})
            except (TypeError, ValueError):
                continue
        return sorted(rows, key=lambda row: row["fiscal_year"])

    def quarterly_fundamentals(self, code: str) -> list[dict]:
        """Return Yahoo's stand-alone quarterly statement as forward-observer data.

        Yahoo does not provide a dependable publication date here.  The retrieval
        date therefore becomes the earliest usable date and fidelity stays PROXY;
        callers must never backfill these rows into earlier signal dates.
        """
        yf = _yfinance_client()
        ticker = yf.Ticker(f"{code}.T")
        statement = ticker.get_income_stmt(freq="quarterly")
        if statement is None or statement.empty:
            return []
        aliases = {
            "basic_eps": ("Basic EPS", "BasicEPS", "Diluted EPS", "DilutedEPS"),
            "revenue": ("Total Revenue", "TotalRevenue", "Operating Revenue", "OperatingRevenue"),
            "operating_profit": ("Operating Income", "OperatingIncome", "Operating Profit", "OperatingProfit"),
            "net_income": ("Net Income", "NetIncome", "Net Income Common Stockholders",
                           "NetIncomeCommonStockholders"),
        }
        rows_by_metric = {field: next((name for name in names if name in statement.index), None)
                          for field, names in aliases.items()}
        retrieved = date.today().isoformat()
        rows = []
        for column in statement.columns:
            try:
                period_end = pd.Timestamp(column)
            except (TypeError, ValueError):
                continue
            item = {
                "fiscal_year": str(period_end.year),
                "fiscal_quarter": f"Q{period_end.quarter}",
                "period_start": None, "period_end": str(period_end.date()),
                "filing_date": None, "published_date": None,
                "source": "YAHOO_QUARTERLY", "fidelity": "PROXY",
                "period_type": "QUARTER", "publication_date_known": 0,
                "retrieved_at": retrieved, "is_derived": 0,
            }
            for field, row_name in rows_by_metric.items():
                item[field] = _number(statement.at[row_name, column]) if row_name else None
            if any(item[field] is not None for field in aliases):
                rows.append(item)
        # Yahoo's earnings calendar often exposes more EPS observations than the
        # statement endpoint and includes an event date. Align the newest event
        # with the newest statement period, then extend backwards by quarters.
        # This is still PROXY data, but it makes EPS acceleration observable
        # without pretending the event date is an official filing timestamp.
        try:
            earnings = ticker.get_earnings_dates(limit=12)
            actual = earnings.dropna(subset=["Reported EPS"]).sort_index(ascending=False)
        except Exception:
            actual = pd.DataFrame()
        if rows and not actual.empty:
            by_period = {row["period_end"]: row for row in rows}
            statement_periods = sorted(by_period, reverse=True)
            oldest_end = pd.Timestamp(statement_periods[-1])
            for index, (_, event) in enumerate(actual.iterrows()):
                if index < len(statement_periods):
                    period_text = statement_periods[index]
                    period_end = pd.Timestamp(period_text)
                else:
                    period_end = oldest_end - pd.DateOffset(months=3 * (index - len(statement_periods) + 1))
                    period_text = str(period_end.date())
                event_date = pd.Timestamp(event.name)
                if event_date.tzinfo is not None:
                    event_date = event_date.tz_convert("Asia/Tokyo")
                published = str(event_date.date())
                row = by_period.get(period_text)
                if row is None:
                    row = {
                        "fiscal_year": str(period_end.year),
                        "fiscal_quarter": f"Q{period_end.quarter}",
                        "period_start": None, "period_end": period_text,
                        "revenue": None, "operating_profit": None, "net_income": None,
                        "source": "YAHOO_QUARTERLY", "fidelity": "PROXY",
                        "period_type": "QUARTER", "retrieved_at": retrieved,
                        "is_derived": 0,
                    }
                    rows.append(row)
                    by_period[period_text] = row
                row.update({"basic_eps": _number(event.get("Reported EPS")),
                            "filing_date": published, "published_date": published,
                            "publication_date_known": 1})
        return sorted(rows, key=lambda row: row["period_end"])


def _pct(value):
    return None if value is None else float(value) * 100


def _number(value):
    try:
        return float(value) if pd.notna(value) else None
    except (TypeError, ValueError):
        return None
