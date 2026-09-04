from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date
from functools import lru_cache
import pandas as pd

from ..config import ROOT, load_config
from ..annual_eps import SourceFetchError

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
            raise SourceFetchError("YAHOO_PARSE_ERROR")
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
            self.last_quarterly_diagnostics = _missing_field_diagnostics("STATEMENT_EMPTY")
            return []
        retrieved = date.today().isoformat()
        rows, field_diagnostics = normalize_yahoo_quarterly_statement(statement, retrieved)
        try:
            earnings = ticker.get_earnings_dates(limit=12)
        except Exception:
            earnings = pd.DataFrame()
        split_dates = _ticker_split_dates(ticker)
        max_days = int(load_config()["free_data"]["quarterly_fundamentals"].get(
            "yahoo_eps_match_max_days", 120))
        rows, eps_diagnostic = merge_yahoo_reported_eps(
            rows, earnings, split_dates, max_days=max_days)
        field_diagnostics["basic_eps"].update(eps_diagnostic)
        self.last_quarterly_diagnostics = field_diagnostics
        return sorted(rows, key=lambda row: row["period_end"])


QUARTERLY_ALIASES = {
    "basic_eps": ("Basic EPS", "BasicEPS", "Diluted EPS", "DilutedEPS"),
    "revenue": ("Total Revenue", "TotalRevenue", "Operating Revenue",
                "OperatingRevenue", "Revenue", "Net Sales", "NetSales"),
    "operating_profit": ("Operating Income", "OperatingIncome", "Operating Profit",
                         "OperatingProfit", "Operating Earnings", "OperatingEarnings"),
    "net_income": ("Net Income", "NetIncome", "Net Income Common Stockholders",
                   "NetIncomeCommonStockholders", "Net Income Continuous Operations",
                   "NetIncomeContinuousOperations"),
}


def normalize_yahoo_quarterly_statement(statement: pd.DataFrame,
                                         retrieved_at: str) -> tuple[list[dict], dict]:
    """Normalize either Yahoo statement orientation using fixed, explicit aliases."""
    if statement is None or statement.empty:
        return [], _missing_field_diagnostics("STATEMENT_EMPTY")
    frame = statement.copy()
    if isinstance(frame.index, pd.MultiIndex):
        frame.index = [" ".join(str(part) for part in value if str(part) != "")
                       for value in frame.index]
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [next((part for part in value if _date_value(part)), value[-1])
                         for value in frame.columns]
    alias_keys = {_label_key(alias) for names in QUARTERLY_ALIASES.values() for alias in names}
    index_hits = sum(_label_key(value) in alias_keys for value in frame.index)
    column_hits = sum(_label_key(value) in alias_keys for value in frame.columns)
    if column_hits > index_hits:
        frame = frame.transpose()
        index_hits, column_hits = column_hits, index_hits
    if index_hits == 0:
        return [], _missing_field_diagnostics("ITEM_NAME_NOT_FOUND")
    labels = {_label_key(value): value for value in frame.index}
    selected = {}
    diagnostics = {}
    for field, aliases in QUARTERLY_ALIASES.items():
        item_name = next((labels[_label_key(alias)] for alias in aliases
                          if _label_key(alias) in labels), None)
        selected[field] = item_name
        diagnostics[field] = ({"status": "AVAILABLE", "item_name": str(item_name),
                               "reason": None} if item_name is not None else
                              {"status": "MISSING", "item_name": None,
                               "reason": "ITEM_NAME_NOT_FOUND"})
    rows = []
    for column in frame.columns:
        period_end = _date_value(column)
        if period_end is None:
            continue
        item_diagnostics = {key: dict(value) for key, value in diagnostics.items()}
        item = {
            "fiscal_year": str(period_end.year),
            "fiscal_quarter": f"Q{period_end.quarter}",
            "period_start": None, "period_end": str(period_end.date()),
            "filing_date": None, "published_date": None,
            "source": "YAHOO_QUARTERLY", "fidelity": "PROXY",
            "period_type": "QUARTER", "publication_date_known": 0,
            "retrieved_at": retrieved_at, "is_derived": 0,
        }
        for field, row_name in selected.items():
            value = _number(frame.at[row_name, column]) if row_name is not None else None
            item[field] = value
            if row_name is not None and value is None:
                item_diagnostics[field] = {"status": "MISSING", "item_name": str(row_name),
                                           "reason": "VALUE_EMPTY_OR_INVALID"}
        if item.get("basic_eps") is not None:
            item["eps_period_match_status"] = "MATCHED"
            item_diagnostics["basic_eps"]["period_match_status"] = "MATCHED"
        item["field_diagnostics"] = item_diagnostics
        if any(item[field] is not None for field in QUARTERLY_ALIASES):
            rows.append(item)
    if not rows:
        return [], {field: ({**value, "status": "MISSING",
                             "reason": value.get("reason") or "NO_VALID_PERIOD_VALUES"})
                    for field, value in diagnostics.items()}
    return sorted(rows, key=lambda row: row["period_end"]), diagnostics


EPS_PERIOD_ALIASES = (
    "Period End", "PeriodEnd", "Quarter End", "QuarterEnd",
    "Fiscal Period End", "FiscalPeriodEnd", "Earnings Period", "EarningsPeriod",
)


def merge_yahoo_reported_eps(rows: list[dict], earnings: pd.DataFrame | None,
                             split_dates: list | None = None,
                             max_days: int = 120) -> tuple[list[dict], dict]:
    """Fill calendar EPS only when its fiscal period is explicit and unambiguous."""
    output = [dict(row) for row in rows]
    for row in output:
        row["field_diagnostics"] = {
            key: dict(value) for key, value in (row.get("field_diagnostics") or {}).items()}
    counts = {"MATCHED": 0, "AMBIGUOUS": 0, "UNMATCHED": 0}
    warnings = 0
    if earnings is not None and not earnings.empty and "Reported EPS" in earnings.columns:
        for event_index, event in earnings.dropna(subset=["Reported EPS"]).iterrows():
            explicit = next((_date_value(event.get(alias)) for alias in EPS_PERIOD_ALIASES
                             if alias in event.index and _date_value(event.get(alias))), None)
            if explicit is None:
                counts["UNMATCHED"] += 1
                continue
            period_text = str(explicit.date())
            candidates = [row for row in output if row.get("period_end") == period_text]
            if len(candidates) > 1:
                counts["AMBIGUOUS"] += 1
                for row in candidates:
                    row["eps_period_match_status"] = "AMBIGUOUS"
                continue
            if not candidates:
                counts["UNMATCHED"] += 1
                continue
            event_date = _date_value(event_index)
            days = (event_date - explicit).days if event_date is not None else None
            if days is None or days < 0 or days > max_days:
                counts["UNMATCHED"] += 1
                candidates[0]["eps_period_match_status"] = "UNMATCHED"
                continue
            row = candidates[0]
            row["eps_period_match_status"] = "MATCHED"
            published = str(event_date.date())
            has_split = any(explicit.date() < value <= event_date.date()
                            for value in _normalized_dates(split_dates or []))
            if has_split:
                warnings += 1
                row["eps_continuity_warning"] = "STOCK_SPLIT_IN_PERIOD_WINDOW"
            elif row.get("basic_eps") is None:
                row["basic_eps"] = _number(event.get("Reported EPS"))
                row.update({"filing_date": published, "published_date": published,
                            "publication_date_known": 1})
            row.setdefault("field_diagnostics", {})["basic_eps"] = {
                "status": "WARNING" if has_split else "AVAILABLE",
                "item_name": "Reported EPS", "reason": row.get("eps_continuity_warning"),
                "period_match_status": "MATCHED",
            }
            counts["MATCHED"] += 1
    warnings += _mark_eps_continuity_warnings(output, split_dates or [])
    return output, {
        "period_match_counts": counts,
        "period_match_status": ("AMBIGUOUS" if counts["AMBIGUOUS"] else
                                "MATCHED" if counts["MATCHED"] else "UNMATCHED"),
        "split_warning_count": warnings,
    }


def _mark_eps_continuity_warnings(rows: list[dict], split_dates: list) -> int:
    splits = _normalized_dates(split_dates)
    if not splits:
        return 0
    warned = 0
    ordered = sorted((row for row in rows if row.get("basic_eps") is not None),
                     key=lambda row: row.get("period_end") or "")
    for current in ordered:
        current_end = _date_value(current.get("period_end"))
        if current_end is None:
            continue
        prior = [row for row in ordered if row is not current and
                 _date_value(row.get("period_end")) is not None and
                 300 <= (current_end - _date_value(row.get("period_end"))).days <= 430]
        if not prior:
            continue
        previous = min(prior, key=lambda row: abs(
            (current_end - _date_value(row.get("period_end"))).days - 365))
        previous_end = _date_value(previous.get("period_end")).date()
        if any(previous_end < split <= current_end.date() for split in splits):
            if not current.get("eps_continuity_warning"):
                warned += 1
            current["eps_continuity_warning"] = "STOCK_SPLIT_BETWEEN_COMPARABLE_PERIODS"
            current.setdefault("field_diagnostics", {})["basic_eps"] = {
                "status": "WARNING", "item_name": "Basic EPS",
                "reason": current["eps_continuity_warning"],
                "period_match_status": current.get("eps_period_match_status", "MATCHED"),
            }
    return warned


def _ticker_split_dates(ticker) -> list:
    try:
        actions = ticker.actions
        if actions is None or actions.empty or "Stock Splits" not in actions.columns:
            return []
        return [index for index, value in actions["Stock Splits"].items()
                if _number(value) not in (None, 0.0)]
    except Exception:
        return []


def _normalized_dates(values: list) -> list[date]:
    result = []
    for value in values:
        parsed = _date_value(value)
        if parsed is not None:
            if parsed.tzinfo is not None:
                parsed = parsed.tz_convert("Asia/Tokyo")
            result.append(parsed.date())
    return result


def _label_key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _date_value(value) -> pd.Timestamp | None:
    try:
        parsed = pd.Timestamp(value)
        return None if pd.isna(parsed) else parsed
    except (TypeError, ValueError):
        return None


def _missing_field_diagnostics(reason: str) -> dict:
    return {field: {"status": "MISSING", "item_name": None, "reason": reason}
            for field in QUARTERLY_ALIASES}


def _pct(value):
    return None if value is None else float(value) * 100


def _number(value):
    try:
        return float(value) if pd.notna(value) else None
    except (TypeError, ValueError):
        return None
