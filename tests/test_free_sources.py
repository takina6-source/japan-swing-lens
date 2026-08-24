import io
import zipfile
import pandas as pd
import pytest
from engine.data.edinet import parse_edinet_csv
from engine.data.jpx import select_scope


def test_jpx_scope_keeps_growth_and_major():
    rows = [
        {"code": "1", "market": "プライム（内国株式）", "size_class": "TOPIX Core30"},
        {"code": "2", "market": "グロース（内国株式）", "size_class": "-"},
        {"code": "3", "market": "スタンダード（内国株式）", "size_class": "TOPIX Small 2"},
    ]
    assert {r["code"] for r in select_scope(rows, "主要500+Growth")} == {"1", "2"}
    assert {r["code"] for r in select_scope(rows, "主要500")} == {"1"}
    assert len(select_scope(rows, "全上場銘柄")) == 3


def test_edinet_csv_growth_extraction():
    df = pd.DataFrame([
        ["NetSalesSummaryOfBusinessResults", "売上高", "CurrentYearDuration", "1200"],
        ["NetSalesSummaryOfBusinessResults", "売上高", "Prior1YearDuration", "1000"],
        ["BasicEarningsLossPerShareSummaryOfBusinessResults", "1株当たり利益", "CurrentYearDuration", "150"],
        ["BasicEarningsLossPerShareSummaryOfBusinessResults", "1株当たり利益", "Prior1YearDuration", "100"],
    ], columns=["要素ID", "項目名", "コンテキストID", "値"])
    csv = df.to_csv(index=False).encode("utf-16")
    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w") as z: z.writestr("XBRL_TO_CSV/test.csv", csv)
    result = parse_edinet_csv(buff.getvalue(), "7203", "2026-08-01")
    assert result["sales_growth"] == pytest.approx(20)
    assert result["eps_growth"] == pytest.approx(50)


def test_database_bulk_free_source_roundtrip(tmp_path):
    from engine.database import Database
    db = Database(tmp_path / "free.db")
    idx = pd.bdate_range("2026-01-01", periods=3)
    frame = pd.DataFrame({"open": [1,2,3], "high": [2,3,4], "low": [0,1,2],
                          "close": [1,2,3], "volume": [10,20,30]}, index=idx)
    db.save_prices_bulk({"7203": frame, "6758": frame}, "Yahoo Finance (補完・非公式)")
    loaded = db.load_prices_many(["7203", "6758"], "Yahoo Finance (補完・非公式)")
    assert set(loaded) == {"7203", "6758"}
    assert db.source_status("Yahoo Finance (補完・非公式)")["codes"] == 2


def test_yfinance_cache_is_inside_app_data():
    from engine.data.yahoo import _yfinance_client
    from engine.config import ROOT
    yf = _yfinance_client()
    assert (ROOT / "data" / "yfinance-cache").is_dir()
    # 公開APIを利用していることも確認し、yfinance更新時の破壊的変更を検知する。
    assert callable(yf.set_tz_cache_location)
