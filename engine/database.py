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
            CREATE TABLE IF NOT EXISTS annual_eps (
              code TEXT, fiscal_year TEXT, eps REAL, filing_date TEXT, source TEXT,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(code,fiscal_year,source));
            CREATE TABLE IF NOT EXISTS setup_registry (
              code TEXT, strategy TEXT, setup_id TEXT, setup_start_date TEXT,
              pivot_price REAL, pivot_type TEXT, pivot_basis TEXT, pivot_fidelity TEXT,
              pivot_formed_date TEXT, last_seen_date TEXT,
              PRIMARY KEY(code,strategy));
            CREATE TABLE IF NOT EXISTS signal_snapshots (
              signal_id TEXT PRIMARY KEY, setup_id TEXT, signal_date TEXT, code TEXT,
              stock_name TEXT, close REAL, consensus_state TEXT,
              breakout_count INTEGER, aligned_count INTEGER, confluence INTEGER,
              coverage REAL, confidence TEXT, pivot_fidelity TEXT,
              momentum_percentile REAL, volume_ratio REAL, market_regime TEXT,
              trading_value REAL, trading_value_20d REAL, liquidity_level TEXT, liquid INTEGER,
              current_trading_value REAL, trading_value_ratio REAL,
              benchmark_close REAL, strategy_states_json TEXT,
              strategy_pivots_json TEXT, trade_plan_json TEXT, app_version TEXT,
              strategy_version TEXT, threshold_version TEXT, schema_version TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS signal_history (
              signal_id TEXT, date TEXT, session_offset INTEGER, close REAL,
              return_abs REAL, benchmark_relative_return REAL, mfe REAL, mae REAL,
              consensus_state TEXT, breakout_count INTEGER, aligned_count INTEGER,
              coverage REAL, confidence TEXT, momentum_percentile REAL,
              market_regime TEXT, strategy_states_json TEXT,
              trading_value REAL, trading_value_ratio REAL, liquidity_level TEXT,
              failed_breakout INTEGER, hit_1r INTEGER, hit_2r INTEGER, hit_stop INTEGER,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(signal_id,date));
            """)
            self._ensure_columns(con, "signal_snapshots", {
                "trading_value_20d": "REAL",
                "liquidity_level": "TEXT",
                "liquid": "INTEGER",
                "current_trading_value": "REAL",
                "trading_value_ratio": "REAL",
            })
            self._ensure_columns(con, "signal_history", {
                "trading_value": "REAL",
                "trading_value_ratio": "REAL",
                "liquidity_level": "TEXT",
            })

    @staticmethod
    def _ensure_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]):
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

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
        annual = [(r["code"], item.get("fiscal_year"), item.get("eps"),
                   item.get("filing_date") or r.get("filing_date"),
                   item.get("source") or r.get("source", "EDINET"))
                  for r in rows for item in (r.get("annual_eps") or [])
                  if item.get("fiscal_year") and item.get("eps") is not None]
        if annual:
            with self.connect() as con:
                con.executemany("""INSERT OR REPLACE INTO annual_eps
                (code,fiscal_year,eps,filing_date,source) VALUES(?,?,?,?,?)""", annual)

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

    def save_annual_eps(self, code: str, rows: list[dict], source: str):
        values = [(code, str(r["fiscal_year"]), float(r["eps"]), r.get("filing_date"), source)
                  for r in rows if r.get("fiscal_year") and r.get("eps") is not None]
        if values:
            with self.connect() as con:
                con.executemany("""INSERT OR REPLACE INTO annual_eps
                (code,fiscal_year,eps,filing_date,source) VALUES(?,?,?,?,?)""", values)

    def load_annual_eps(self, codes: list[str]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {code: [] for code in codes}
        if not codes:
            return result
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            for start in range(0, len(codes), 800):
                part = codes[start:start + 800]
                marks = ",".join("?" for _ in part)
                rows = con.execute(f"""SELECT code,fiscal_year,eps,filing_date,source
                FROM annual_eps WHERE code IN ({marks}) ORDER BY fiscal_year""", part)
                for row in rows:
                    result.setdefault(row["code"], []).append(dict(row))
        return result

    def load_setup_registry(self, code: str) -> dict[str, dict]:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            return {r["strategy"]: dict(r) for r in con.execute(
                "SELECT * FROM setup_registry WHERE code=?", (code,))}

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
                if result.setup_id and result.pivot is not None:
                    con.execute("""INSERT OR REPLACE INTO setup_registry
                    (code,strategy,setup_id,setup_start_date,pivot_price,pivot_type,pivot_basis,
                     pivot_fidelity,pivot_formed_date,last_seen_date) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (analysis.code, strategy, result.setup_id, result.setup_start_date,
                     result.pivot, result.pivot_type, result.pivot_basis,
                     result.pivot_fidelity.value, result.pivot_formed_date, analysis.as_of))
            plan = analysis.trade_plan
            if plan:
                con.execute("""INSERT OR REPLACE INTO trade_plans
                (code,as_of,status,entry_low,entry_high,stop,target_1r,target_2r,target_extended,
                 risk_pct,basis,warning,logic_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (analysis.code, analysis.as_of, plan.status, plan.entry_low, plan.entry_high,
                 plan.stop, plan.target_1r, plan.target_2r, plan.target_extended,
                 plan.risk_pct, plan.basis, plan.warning, logic_version))

    def save_signal_tracking(self, analysis, frame: pd.DataFrame,
                             benchmark: pd.DataFrame, cfg: dict):
        if not analysis.setup_id:
            return
        eligible = analysis.state.value in ("BREAKOUT", "BREAKOUT WATCH")
        states = {name: result.state.value for name, result in analysis.strategies.items()}
        pivots = {name: {"price": result.pivot, "type": result.pivot_type,
                         "basis": result.pivot_basis,
                         "fidelity": result.pivot_fidelity.value,
                         "formed_date": result.pivot_formed_date,
                         "setup_start_date": result.setup_start_date}
                  for name, result in analysis.strategies.items() if result.pivot is not None}
        signal_id = f"{analysis.setup_id}:{cfg['strategy_version']}"
        plan = vars(analysis.trade_plan) if analysis.trade_plan else {}
        x = frame.iloc[-1]
        if eligible:
            with self.connect() as con:
                con.execute("""INSERT OR IGNORE INTO signal_snapshots
                (signal_id,setup_id,signal_date,code,stock_name,close,consensus_state,
                 breakout_count,aligned_count,confluence,coverage,confidence,pivot_fidelity,
                 momentum_percentile,volume_ratio,market_regime,trading_value,trading_value_20d,
                 liquidity_level,liquid,current_trading_value,trading_value_ratio,benchmark_close,
                 strategy_states_json,strategy_pivots_json,trade_plan_json,app_version,
                 strategy_version,threshold_version,schema_version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (signal_id, analysis.setup_id, analysis.as_of, analysis.code, analysis.name,
                 float(x.close), analysis.state.value, analysis.breakout_strategy_count,
                 analysis.aligned_strategy_count, analysis.confluence, analysis.coverage,
                 analysis.confidence, analysis.pivot_fidelity.value,
                 _finite(analysis.metrics.get("momentum_percentile")),
                 _finite(x.volume_ratio), analysis.metrics.get("market_regime"),
                 _finite(analysis.metrics.get("trading_value_20d")),
                 _finite(analysis.metrics.get("trading_value_20d")),
                 analysis.metrics.get("liquidity_level"),
                 int(bool(analysis.metrics.get("liquid"))),
                 _finite(analysis.metrics.get("trading_value")),
                 _finite(analysis.metrics.get("trading_value_ratio")),
                 _finite(analysis.metrics.get("benchmark_price")),
                 json.dumps(states, ensure_ascii=False), json.dumps(pivots, ensure_ascii=False),
                 json.dumps(plan, ensure_ascii=False), cfg["logic_version"],
                 cfg["strategy_version"], cfg["threshold_version"], cfg["schema_version"]))
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            active = [dict(r) for r in con.execute("""SELECT * FROM signal_snapshots
            WHERE code=? AND signal_date<=? ORDER BY signal_date""", (analysis.code, analysis.as_of))]
        for signal in active:
            start = pd.Timestamp(signal["signal_date"])
            path = frame.loc[(frame.index >= start) & (frame.index <= pd.Timestamp(analysis.as_of))]
            if path.empty:
                continue
            offset = max(0, len(path) - 1)
            if offset > int(cfg["tracking"]["max_sessions"]):
                continue
            signal_price = float(signal["close"])
            absolute = (float(x.close) / signal_price - 1) * 100
            mfe = (float(path.high.max()) / signal_price - 1) * 100
            mae = (float(path.low.min()) / signal_price - 1) * 100
            bench_start = _price_on_or_before(benchmark, start)
            bench_now = _price_on_or_before(benchmark, pd.Timestamp(analysis.as_of))
            relative = absolute - ((bench_now / bench_start - 1) * 100) if bench_start and bench_now else None
            saved_plan = json.loads(signal.get("trade_plan_json") or "{}")
            primary = _primary_pivot(json.loads(signal.get("strategy_pivots_json") or "{}"), signal_price)
            failed = bool(primary and float(x.close) < primary *
                          (1 - float(cfg["pivot"]["failed_below_pivot_pct"]) / 100))
            history = (signal["signal_id"], analysis.as_of, offset, float(x.close), absolute,
                       relative, mfe, mae, analysis.state.value,
                       analysis.breakout_strategy_count, analysis.aligned_strategy_count,
                       analysis.coverage, analysis.confidence,
                       _finite(analysis.metrics.get("momentum_percentile")),
                       analysis.metrics.get("market_regime"), json.dumps(states, ensure_ascii=False),
                       _finite(analysis.metrics.get("trading_value")),
                       _finite(analysis.metrics.get("trading_value_ratio")),
                       analysis.metrics.get("liquidity_level"),
                       int(failed), int(_hit_high(path, saved_plan.get("target_1r"))),
                       int(_hit_high(path, saved_plan.get("target_2r"))),
                       int(_hit_low(path, saved_plan.get("stop"))))
            with self.connect() as con:
                con.execute("""INSERT OR REPLACE INTO signal_history
                (signal_id,date,session_offset,close,return_abs,benchmark_relative_return,mfe,mae,
                 consensus_state,breakout_count,aligned_count,coverage,confidence,momentum_percentile,
                 market_regime,strategy_states_json,trading_value,trading_value_ratio,liquidity_level,
                 failed_breakout,hit_1r,hit_2r,hit_stop)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", history)

    def validation_rows(self) -> tuple[list[dict], list[dict]]:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            signals = [dict(r) for r in con.execute("SELECT * FROM signal_snapshots ORDER BY signal_date,code")]
            history = [dict(r) for r in con.execute("SELECT * FROM signal_history ORDER BY signal_id,date")]
        return signals, history

    def import_validation_rows(self, signals: list[dict], history: list[dict]):
        _insert_dicts(self, "signal_snapshots", signals, "signal_id")
        _insert_dicts(self, "signal_history", history, "signal_id,date")


def _insert_dicts(db: Database, table: str, rows: list[dict], key: str):
    if not rows:
        return
    with db.connect() as con:
        allowed = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    columns = [c for c in rows[0] if c in allowed and c != "created_at"]
    marks = ",".join("?" for _ in columns)
    with db.connect() as con:
        con.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES({marks})",
                        [[row.get(c) for c in columns] for row in rows])


def _finite(value):
    try:
        value = float(value)
        return value if pd.notna(value) else None
    except (TypeError, ValueError):
        return None


def _price_on_or_before(frame: pd.DataFrame, date: pd.Timestamp) -> float | None:
    rows = frame.loc[frame.index <= date]
    return float(rows.close.iloc[-1]) if not rows.empty else None


def _primary_pivot(pivots: dict, signal_price: float) -> float | None:
    values = [float(v["price"]) for v in pivots.values() if v.get("price")]
    return min(values, key=lambda v: abs(v - signal_price)) if values else None


def _hit_high(frame: pd.DataFrame, level) -> bool:
    return bool(level is not None and float(frame.high.max()) >= float(level))


def _hit_low(frame: pd.DataFrame, level) -> bool:
    return bool(level is not None and float(frame.low.min()) <= float(level))
