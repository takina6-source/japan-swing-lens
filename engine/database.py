from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
import pandas as pd


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    @contextmanager
    def connect(self):
        # Finderからの再起動や複数タブの同時更新でも、親フォルダ消失・短時間の競合に耐える。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = None
        last_error = None
        for delay in (0, 0.05, 0.2):
            if delay:
                time.sleep(delay)
            try:
                con = sqlite3.connect(str(self.path), timeout=30)
                con.execute("PRAGMA busy_timeout=30000")
                break
            except sqlite3.OperationalError as exc:
                last_error = exc
        if con is None:
            raise RuntimeError(f"保存DBを開けません: {self.path} ({last_error})") from last_error
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init(self):
        with self.connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS prices (
              code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
              source TEXT, fetched_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(code,date,source));
            CREATE TABLE IF NOT EXISTS analyses (
              code TEXT, as_of TEXT, strategy TEXT, state TEXT, condition_key TEXT,
              verdict TEXT, role TEXT, layer TEXT, value_json TEXT, reference_json TEXT,
              fidelity TEXT, logic_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(code,as_of,strategy,condition_key,logic_version));
            CREATE TABLE IF NOT EXISTS signals (
              code TEXT, signal_date TEXT, strategy TEXT, state TEXT, pivot REAL, stop REAL,
              logic_version TEXT, forward_1d REAL, forward_3d REAL, forward_5d REAL,
              forward_10d REAL, forward_20d REAL, mfe_20d REAL, mae_20d REAL,
              PRIMARY KEY(code,signal_date,strategy,logic_version));
            CREATE TABLE IF NOT EXISTS fetch_log (
              id INTEGER PRIMARY KEY, fetched_at TEXT DEFAULT CURRENT_TIMESTAMP, source TEXT,
              target TEXT, success INTEGER, message TEXT);
            CREATE TABLE IF NOT EXISTS trade_plans (
              code TEXT, as_of TEXT, status TEXT, entry_low REAL, entry_high REAL,
              stop REAL, target_1r REAL, target_2r REAL, target_extended REAL,
              risk_pct REAL, basis TEXT, warning TEXT, logic_version TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(code,as_of,logic_version));
            CREATE TABLE IF NOT EXISTS securities (
              code TEXT PRIMARY KEY, name TEXT, market TEXT, sector33 TEXT,
              size_class TEXT, source TEXT, source_date TEXT,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS fundamentals (
              code TEXT PRIMARY KEY, filing_date TEXT, eps_growth REAL, sales_growth REAL,
              roe REAL, bps REAL, source TEXT, freshness_note TEXT,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            """)

    def save_prices(self, code: str, df: pd.DataFrame, source: str):
        rows = [(code, str(i.date()), float(r.open), float(r.high), float(r.low), float(r.close),
                 float(r.volume), source) for i, r in df.iterrows()]
        with self.connect() as con:
            con.executemany("INSERT OR REPLACE INTO prices(code,date,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?)", rows)

    def save_prices_bulk(self, frames: dict[str, pd.DataFrame], source: str):
        rows = []
        for code, df in frames.items():
            rows.extend((code, str(i.date()), float(r.open), float(r.high), float(r.low),
                         float(r.close), float(r.volume), source) for i, r in df.iterrows())
        with self.connect() as con:
            con.executemany("INSERT OR REPLACE INTO prices(code,date,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?)", rows)

    def load_prices(self, code: str, source: str | None = None) -> pd.DataFrame:
        query, args = "SELECT date,open,high,low,close,volume,source FROM prices WHERE code=?", [code]
        if source:
            query += " AND source=?"; args.append(source)
        query += " ORDER BY date"
        with self.connect() as con:
            df = pd.read_sql_query(query, con, params=args, parse_dates=["date"])
        return df.set_index("date") if not df.empty else df

    def load_prices_many(self, codes: list[str], source: str) -> dict[str, pd.DataFrame]:
        if not codes:
            return {}
        frames = {}
        with self.connect() as con:
            for start in range(0, len(codes), 800):
                part = codes[start:start + 800]
                marks = ",".join("?" for _ in part)
                query = f"SELECT code,date,open,high,low,close,volume FROM prices WHERE source=? AND code IN ({marks}) ORDER BY code,date"
                df = pd.read_sql_query(query, con, params=[source, *part], parse_dates=["date"])
                for code, rows in df.groupby("code"):
                    frames[str(code)] = rows.drop(columns="code").set_index("date")
        return frames

    def save_securities(self, rows: list[dict]):
        values = [(r["code"], r["name"], r.get("market"), r.get("sector33"),
                   r.get("size_class"), r.get("source"), r.get("source_date")) for r in rows]
        with self.connect() as con:
            con.executemany("""INSERT OR REPLACE INTO securities
            (code,name,market,sector33,size_class,source,source_date) VALUES(?,?,?,?,?,?,?)""", values)

    def load_securities(self) -> list[dict]:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute("SELECT * FROM securities ORDER BY code")]

    def save_fundamentals(self, rows: list[dict]):
        values = [(r["code"], r.get("filing_date"), r.get("eps_growth"),
                   r.get("sales_growth"), r.get("roe"), r.get("bps"), r.get("source", "EDINET"),
                   r.get("freshness_note", "")) for r in rows]
        with self.connect() as con:
            con.executemany("""INSERT OR REPLACE INTO fundamentals
            (code,filing_date,eps_growth,sales_growth,roe,bps,source,freshness_note)
            VALUES(?,?,?,?,?,?,?,?)""", values)

    def load_fundamentals(self, codes: list[str]) -> dict[str, dict]:
        if not codes: return {}
        result = {}
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            for start in range(0, len(codes), 800):
                part = codes[start:start + 800]
                marks = ",".join("?" for _ in part)
                for row in con.execute(f"SELECT * FROM fundamentals WHERE code IN ({marks})", part):
                    result[row["code"]] = dict(row)
        return result

    def source_status(self, source: str) -> dict:
        with self.connect() as con:
            row = con.execute("""SELECT COUNT(DISTINCT code), MIN(date), MAX(date), MAX(fetched_at)
            FROM prices WHERE source=?""", (source,)).fetchone()
        return {"codes": row[0] or 0, "min_date": row[1], "max_date": row[2], "fetched_at": row[3]}

    def log_fetch(self, source: str, target: str, success: bool, message: str):
        with self.connect() as con:
            con.execute("INSERT INTO fetch_log(source,target,success,message) VALUES(?,?,?,?)",
                        (source, target, int(success), message[:1000]))

    def save_analysis(self, analysis, logic_version: str):
        with self.connect() as con:
            for strategy, result in analysis.strategies.items():
                for cond in result.conditions:
                    con.execute("""INSERT OR REPLACE INTO analyses
                    (code,as_of,strategy,state,condition_key,verdict,role,layer,value_json,reference_json,fidelity,logic_version)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (analysis.code, analysis.as_of, strategy,
                    result.state.value, cond.key, cond.verdict.value, cond.role.value, cond.layer.value,
                    json.dumps(cond.value, ensure_ascii=False, default=str),
                    json.dumps(cond.reference, ensure_ascii=False, default=str), cond.fidelity.value, logic_version))
                if result.state.value in ("BREAKOUT", "BREAKOUT WATCH", "PULLBACK"):
                    con.execute("""INSERT OR IGNORE INTO signals
                    (code,signal_date,strategy,state,pivot,stop,logic_version) VALUES(?,?,?,?,?,?,?)""",
                    (analysis.code, analysis.as_of, strategy, result.state.value, result.pivot, result.stop, logic_version))
            plan = analysis.trade_plan
            if plan:
                con.execute("""INSERT OR REPLACE INTO trade_plans
                (code,as_of,status,entry_low,entry_high,stop,target_1r,target_2r,target_extended,
                 risk_pct,basis,warning,logic_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (analysis.code, analysis.as_of, plan.status, plan.entry_low, plan.entry_high,
                 plan.stop, plan.target_1r, plan.target_2r, plan.target_extended,
                 plan.risk_pct, plan.basis, plan.warning, logic_version))
