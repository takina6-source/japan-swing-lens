from __future__ import annotations

import io
import logging
import pandas as pd
import requests

log = logging.getLogger(__name__)


class JPXUniverseProvider:
    name = "JPX公式 上場銘柄一覧"
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

    def fetch(self) -> list[dict]:
        response = requests.get(self.url, timeout=30,
                                headers={"User-Agent": "MomentumSwingEngine/0.3 personal research"})
        response.raise_for_status()
        df = pd.read_excel(io.BytesIO(response.content), dtype={"コード": str})
        domestic = df.loc[df["市場・商品区分"].astype(str).str.contains("内国株式", na=False)].copy()
        rows = []
        for _, r in domestic.iterrows():
            code = str(r["コード"]).split(".")[0].zfill(4)[:4]
            if code.isdigit():
                rows.append({"code": code, "name": str(r["銘柄名"]),
                             "market": str(r["市場・商品区分"]),
                             "sector33": str(r["33業種区分"]),
                             "size_class": str(r["規模区分"]),
                             "source": self.name, "source_date": str(r["日付"])})
        if len(rows) < 3000:
            raise RuntimeError(f"JPX銘柄一覧が想定より少ないため採用しません（{len(rows)}件）")
        return rows


def select_scope(rows: list[dict], scope: str) -> list[dict]:
    if scope == "全上場銘柄":
        return rows
    if scope == "主要500":
        wanted = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")
        chosen = [r for r in rows if any(x in str(r.get("size_class")) for x in wanted)]
        return chosen
    # 主要500にGrowth全銘柄を加え、強い新興株を落としにくくする。
    wanted = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")
    return [r for r in rows if "グロース" in str(r.get("market")) or
            any(x in str(r.get("size_class")) for x in wanted)]
