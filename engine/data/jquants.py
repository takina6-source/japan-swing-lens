from __future__ import annotations

import os
import pandas as pd


class JQuantsProvider:
    """J-Quants公式ClientV2を利用する薄い境界層。

    応答列の短縮名はここで正規化し、分析層へ漏らさない。
    """
    name = "J-Quants API V2"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("JQUANTS_API_KEY")
        if not self.api_key:
            raise ValueError("JQUANTS_API_KEYが未設定です")
        try:
            import jquantsapi
        except ImportError as exc:
            raise RuntimeError("J-Quants追加依存関係が未導入です") from exc
        self.client = jquantsapi.ClientV2(api_key=self.api_key)

    def history(self, code: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        raw = self.client.get_eq_bars_daily(code=code, from_yyyymmdd=start.strftime("%Y%m%d"),
                                            to_yyyymmdd=end.strftime("%Y%m%d"))
        return self._normalize_prices(raw)

    def bulk(self, start: pd.Timestamp, end: pd.Timestamp):
        master = self.client.get_eq_master()
        prices = self.client.get_eq_bars_daily_range(start_dt=start.to_pydatetime(), end_dt=end.to_pydatetime())
        fins = self.client.get_fin_summary_range(start_dt=(end - pd.Timedelta(days=500)).to_pydatetime(),
                                                 end_dt=end.to_pydatetime())
        names = {str(r.Code)[:4]: str(r.CoName) for _, r in master.iterrows()
                 if str(r.Code)[:4].isdigit() and str(r.Mkt) in {"0111", "0112", "0113"}}
        histories = {}
        prices["Code4"] = prices["Code"].astype(str).str[:4]
        for code, part in prices.loc[prices["Code4"].isin(names)].groupby("Code4"):
            normalized = self._normalize_prices(part)
            if len(normalized) >= 200:
                histories[code] = normalized
        fundamentals = {code: self._fundamentals(fins.loc[fins["Code"].astype(str).str[:4] == code])
                        for code in histories}
        return {c: names.get(c, c) for c in histories}, histories, fundamentals

    def _normalize_prices(self, raw: pd.DataFrame) -> pd.DataFrame:
        aliases = {"Date": "date", "AdjO": "open", "AdjH": "high", "AdjL": "low",
                   "AdjC": "close", "AdjVo": "volume"}
        missing = set(aliases) - set(raw.columns)
        if missing:
            raise RuntimeError(f"J-Quants応答列が想定と異なります: {sorted(missing)}")
        out = raw.rename(columns=aliases).set_index("date")[["open", "high", "low", "close", "volume"]]
        out.index = pd.to_datetime(out.index)
        return out.sort_index()

    def _fundamentals(self, frame: pd.DataFrame) -> dict:
        if frame.empty:
            return {"eps_growth": None, "sales_growth": None, "source": self.name}
        annual = frame.loc[frame.get("CurPerType", pd.Series(index=frame.index, dtype=str)).astype(str).eq("FY")].copy()
        annual = annual.sort_values("DiscDate").dropna(subset=["EPS", "Sales"]).tail(2)
        if len(annual) < 2:
            return {"eps_growth": None, "sales_growth": None, "source": self.name}
        prev, latest = annual.iloc[0], annual.iloc[1]
        return {"eps_growth": _growth(prev.EPS, latest.EPS),
                "sales_growth": _growth(prev.Sales, latest.Sales), "source": self.name}

    def annual_eps(self, code: str) -> list[dict]:
        """通期決算だけを選び、開示日時点のBasic EPS履歴へ正規化する。"""
        getter = getattr(self.client, "get_fin_summary", None)
        if getter is None:
            raise RuntimeError("J-Quants clientに財務サマリーAPIがありません")
        raw = getter(code=code)
        if raw is None or raw.empty:
            return []
        period_col = next((x for x in ("CurPerType", "TypeOfCurrentPeriod") if x in raw.columns), None)
        eps_col = next((x for x in ("EPS", "EarningsPerShare") if x in raw.columns), None)
        date_col = next((x for x in ("CurFYEnd", "CurrentFiscalYearEndDate", "DiscDate",
                                     "DisclosureDate") if x in raw.columns), None)
        published_col = next((x for x in ("DiscDate", "DisclosureDate") if x in raw.columns), None)
        if not period_col or not eps_col or not date_col:
            return []
        annual = raw.loc[raw[period_col].astype(str).str.upper().eq("FY")].copy()
        rows = []
        for _, item in annual.iterrows():
            try:
                value = float(item[eps_col])
                year = str(pd.Timestamp(item[date_col]).year)
                if pd.notna(value):
                    rows.append({"fiscal_year": year, "eps": value,
                                 "filing_date": str(item[published_col])[:10] if published_col else None,
                                 "published_date": str(item[published_col])[:10] if published_col else None,
                                 "source": self.name, "fidelity": "PRACTICAL",
                                 "period_type": "FY", "concept": eps_col, "priority": 40})
            except (TypeError, ValueError):
                continue
        return sorted(rows, key=lambda row: row["fiscal_year"])


def _growth(previous, latest):
    try:
        previous, latest = float(previous), float(latest)
        return None if previous == 0 else (latest / abs(previous) - 1) * 100
    except (TypeError, ValueError):
        return None
