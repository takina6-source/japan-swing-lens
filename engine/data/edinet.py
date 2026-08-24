from __future__ import annotations

import io
import re
import zipfile
from datetime import date, timedelta
import pandas as pd
import requests


class EdinetProvider:
    name = "金融庁 EDINET API v2"
    base = "https://api.edinet-fsa.go.jp/api/v2"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("EDINET APIキーが未設定です")
        self.api_key = api_key

    def update_recent(self, days: int = 10, max_documents: int = 80):
        filings, errors = [], []
        for offset in range(days):
            target = date.today() - timedelta(days=offset)
            try:
                response = requests.get(f"{self.base}/documents.json",
                                        params={"date": target.isoformat(), "type": 2,
                                                "Subscription-Key": self.api_key}, timeout=25)
                response.raise_for_status()
                for row in response.json().get("results", []):
                    description = str(row.get("docDescription") or "")
                    sec_code = str(row.get("secCode") or "")[:4]
                    if sec_code.isdigit() and re.search(r"有価証券報告書|半期報告書|四半期報告書", description):
                        filings.append((sec_code, row.get("docID"), str(row.get("submitDateTime", ""))[:10]))
            except Exception as exc:
                errors.append(f"{target}: 書類一覧 {exc}")
        results = []
        seen = set()
        for code, doc_id, filing_date in filings[:max_documents]:
            if not doc_id or (code, doc_id) in seen: continue
            seen.add((code, doc_id))
            try:
                response = requests.get(f"{self.base}/documents/{doc_id}",
                                        params={"type": 5, "Subscription-Key": self.api_key}, timeout=40)
                response.raise_for_status()
                result = parse_edinet_csv(response.content, code, filing_date)
                if result: results.append(result)
            except Exception as exc:
                errors.append(f"{code}: 財務CSV {exc}")
        return results, errors


def parse_edinet_csv(content: bytes, code: str, filing_date: str) -> dict | None:
    """EDINETのXBRL変換CSVから当期・前期の代表値を抽出する。

    タクソノミ差を吸収するため、要素IDと日本語項目名の両方を照合する。
    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names: return None
        parts = []
        for name in names:
            raw = archive.read(name)
            for encoding in ("utf-16", "utf-8-sig", "cp932"):
                try:
                    parts.append(pd.read_csv(io.BytesIO(raw), encoding=encoding, dtype=str))
                    break
                except Exception:
                    continue
        if not parts: return None
    df = pd.concat(parts, ignore_index=True)
    cols = {str(c).strip(): c for c in df.columns}
    element_col = next((c for k, c in cols.items() if "要素ID" in k or "element" in k.lower()), None)
    label_col = next((c for k, c in cols.items() if "項目名" in k or "label" in k.lower()), None)
    context_col = next((c for k, c in cols.items() if "コンテキスト" in k or "context" in k.lower()), None)
    value_col = next((c for k, c in cols.items() if k == "値" or "value" in k.lower()), None)
    if not all((element_col, context_col, value_col)): return None
    text = df[element_col].fillna("").astype(str)
    if label_col: text = text + " " + df[label_col].fillna("").astype(str)

    def pair(patterns):
        mask = pd.Series(False, index=df.index)
        for pattern in patterns: mask |= text.str.contains(pattern, case=False, regex=True)
        subset = df.loc[mask, [context_col, value_col]].copy()
        subset["number"] = subset[value_col].map(_number)
        subset = subset.dropna(subset=["number"])
        current = subset.loc[subset[context_col].astype(str).str.contains("Current|当期", case=False), "number"]
        prior = subset.loc[subset[context_col].astype(str).str.contains("Prior1|前期", case=False), "number"]
        return (current.iloc[0] if len(current) else None, prior.iloc[0] if len(prior) else None)

    sales, prior_sales = pair([r"NetSales", r"Revenue.*Summary", r"売上高"])
    eps, prior_eps = pair([r"BasicEarningsLossPerShare", r"EarningsPerShare", r"1株当たり.*利益"])
    roe, _ = pair([r"RateOfReturnOnEquity", r"自己資本利益率"])
    bps, _ = pair([r"NetAssetsPerShare", r"1株当たり純資産"])
    if sales is None and eps is None: return None
    return {"code": code, "filing_date": filing_date,
            "eps_growth": _growth(prior_eps, eps), "sales_growth": _growth(prior_sales, sales),
            "roe": roe, "bps": bps, "source": "金融庁 EDINET API v2",
            "freshness_note": "有価証券報告書等。決算短信より遅い場合があります"}


def _number(value):
    try:
        return float(str(value).replace(",", "").replace("△", "-").strip())
    except (TypeError, ValueError):
        return None


def _growth(previous, current):
    if previous in (None, 0) or current is None: return None
    return (current / abs(previous) - 1) * 100
