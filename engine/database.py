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
              operating_profit_growth REAL, roe REAL, bps REAL, source TEXT, freshness_note TEXT,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS annual_eps (
              code TEXT, fiscal_year TEXT, eps REAL, filing_date TEXT, source TEXT,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(code,fiscal_year,source));
            CREATE TABLE IF NOT EXISTS quarterly_fundamentals (
              code TEXT, fiscal_year TEXT, fiscal_quarter TEXT,
              period_start TEXT, period_end TEXT, filing_date TEXT, published_date TEXT,
              revenue REAL, operating_profit REAL, net_income REAL, basic_eps REAL,
              source TEXT, fidelity TEXT, period_type TEXT, publication_date_known INTEGER,
              retrieved_at TEXT, is_derived INTEGER DEFAULT 0, company_forecast INTEGER DEFAULT 0,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(code,period_end,period_type,source,is_derived));
            CREATE TABLE IF NOT EXISTS quarterly_fundamental_diagnostics (
              code TEXT PRIMARY KEY, status TEXT, coverage REAL, quarters_available INTEGER,
              source_summary TEXT, fidelity TEXT, latest_period TEXT, published_date TEXT,
              reason_codes_json TEXT, attempted_sources_json TEXT, details_json TEXT,
              logic_version TEXT, diagnosed_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS fundamental_data_diagnostics (
              code TEXT PRIMARY KEY, status TEXT, fidelity TEXT,
              years_available INTEGER, initial_years INTEGER, source_summary TEXT,
              fallback_used INTEGER, reason_code TEXT, reason_codes_json TEXT,
              attempted_sources_json TEXT, details_json TEXT,
              diagnosed_at TEXT, logic_version TEXT,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
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
              experimental_version TEXT, experimental_alignment INTEGER,
              experimental_combination TEXT, experimental_states_json TEXT,
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
            CREATE TABLE IF NOT EXISTS control_members (
              control_group_id TEXT, signal_id TEXT, signal_date TEXT,
              control_code TEXT, control_name TEXT, control_type TEXT,
              control_rank INTEGER, match_score REAL, matched_at TEXT,
              initial_close REAL, signal_momentum_percentile REAL,
              control_momentum_percentile REAL, signal_trading_value REAL,
              control_trading_value REAL, signal_market TEXT, control_market TEXT,
              signal_size_class TEXT, control_size_class TEXT,
              signal_price REAL, control_price REAL, selection_version TEXT,
              app_version TEXT, strategy_version TEXT, threshold_version TEXT,
              schema_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(control_group_id,control_code));
            CREATE TABLE IF NOT EXISTS control_history (
              control_group_id TEXT, signal_id TEXT, control_code TEXT,
              control_type TEXT, date TEXT, session_offset INTEGER, close REAL,
              return_abs REAL, benchmark_relative_return REAL, mfe REAL, mae REAL,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(control_group_id,control_code,date));
            CREATE INDEX IF NOT EXISTS idx_control_members_signal_id
              ON control_members(signal_id);
            CREATE INDEX IF NOT EXISTS idx_control_members_control_code
              ON control_members(control_code);
            CREATE INDEX IF NOT EXISTS idx_control_history_signal_type_offset
              ON control_history(signal_id,control_type,session_offset);
            CREATE TABLE IF NOT EXISTS experimental_snapshots (
              experimental_signal_id TEXT PRIMARY KEY, signal_date TEXT, code TEXT,
              stock_name TEXT, strategy TEXT, initial_state TEXT, close REAL,
              benchmark_close REAL, experimental_alignment INTEGER,
              experimental_combination TEXT, core_signal INTEGER, core_state TEXT,
              cross_signal TEXT, metrics_json TEXT, coverage REAL, fidelity TEXT,
              setup_id TEXT, experiment_start_date TEXT, experimental_version TEXT,
              schema_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS experimental_history (
              experimental_signal_id TEXT, date TEXT, session_offset INTEGER,
              close REAL, return_abs REAL, benchmark_relative_return REAL,
              mfe REAL, mae REAL, state TEXT, failed INTEGER,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(experimental_signal_id,date));
            CREATE TABLE IF NOT EXISTS experimental_control_members (
              control_group_id TEXT, experimental_signal_id TEXT, signal_date TEXT,
              control_code TEXT, control_name TEXT, control_type TEXT,
              control_rank INTEGER, match_score REAL, initial_close REAL,
              selection_version TEXT, experimental_version TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(control_group_id,control_code));
            CREATE TABLE IF NOT EXISTS experimental_control_history (
              control_group_id TEXT, experimental_signal_id TEXT, control_code TEXT,
              control_type TEXT, date TEXT, session_offset INTEGER, close REAL,
              return_abs REAL, benchmark_relative_return REAL, mfe REAL, mae REAL,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(control_group_id,control_code,date));
            CREATE INDEX IF NOT EXISTS idx_experimental_snapshot_code_date
              ON experimental_snapshots(code,signal_date);
            CREATE INDEX IF NOT EXISTS idx_experimental_history_signal_offset
              ON experimental_history(experimental_signal_id,session_offset);
            CREATE INDEX IF NOT EXISTS idx_experimental_controls_signal
              ON experimental_control_members(experimental_signal_id);
            CREATE INDEX IF NOT EXISTS idx_quarterly_code_period
              ON quarterly_fundamentals(code,period_end);
            CREATE INDEX IF NOT EXISTS idx_quarterly_publication
              ON quarterly_fundamentals(code,published_date);
            """)
            self._ensure_columns(con, "fundamentals", {
                "operating_profit_growth": "REAL",
            })
            self._ensure_columns(con, "signal_snapshots", {
                "trading_value_20d": "REAL",
                "liquidity_level": "TEXT",
                "liquid": "INTEGER",
                "current_trading_value": "REAL",
                "trading_value_ratio": "REAL",
            })
            self._ensure_columns(con, "annual_eps", {
                "fidelity": "TEXT",
                "published_date": "TEXT",
                "retrieved_at": "TEXT",
                "status": "TEXT",
                "period_type": "TEXT",
                "concept": "TEXT",
                "anomaly": "TEXT",
                "priority": "INTEGER",
            })
            self._ensure_columns(con, "quarterly_fundamentals", {
                "field_diagnostics_json": "TEXT",
            })
            self._ensure_columns(con, "signal_history", {
                "trading_value": "REAL",
                "trading_value_ratio": "REAL",
                "liquidity_level": "TEXT",
            })
            self._ensure_columns(con, "signal_snapshots", {
                "experimental_version": "TEXT",
                "experimental_alignment": "INTEGER",
                "experimental_combination": "TEXT",
                "experimental_states_json": "TEXT",
            })
            con.execute("PRAGMA optimize")

    @staticmethod
    def _ensure_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]):
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

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
                   r.get("sales_growth"), r.get("operating_profit_growth"),
                   r.get("roe"), r.get("bps"), r.get("source", "EDINET"),
                   r.get("freshness_note", "")) for r in rows]
        with self.connect() as con:
            con.executemany("""INSERT OR REPLACE INTO fundamentals
            (code,filing_date,eps_growth,sales_growth,operating_profit_growth,roe,bps,source,freshness_note)
            VALUES(?,?,?,?,?,?,?,?,?)""", values)
        for result in rows:
            annual = []
            for item in result.get("annual_eps") or []:
                enriched = dict(item)
                enriched["filing_date"] = item.get("filing_date") or result.get("filing_date")
                annual.append(enriched)
            self.save_annual_eps(result["code"], annual, result.get("source", "EDINET"))

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
        values = [(code, str(r["fiscal_year"]), float(r["eps"]), r.get("filing_date"),
                   r.get("source") or source, r.get("fidelity"),
                   r.get("published_date") or r.get("filing_date"), r.get("retrieved_at"),
                   r.get("status", "AVAILABLE"), r.get("period_type", "FY"),
                   r.get("concept"), r.get("anomaly"), r.get("priority"))
                  for r in rows if r.get("fiscal_year") and r.get("eps") is not None]
        if values:
            with self.connect() as con:
                con.executemany("""INSERT OR REPLACE INTO annual_eps
                (code,fiscal_year,eps,filing_date,source,fidelity,published_date,retrieved_at,
                 status,period_type,concept,anomaly,priority) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)

    def load_annual_eps(self, codes: list[str]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {code: [] for code in codes}
        if not codes:
            return result
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            for start in range(0, len(codes), 800):
                part = codes[start:start + 800]
                marks = ",".join("?" for _ in part)
                rows = con.execute(f"""SELECT code,fiscal_year,eps,filing_date,source,
                fidelity,published_date,retrieved_at,status,period_type,concept,anomaly,priority,updated_at
                FROM annual_eps WHERE code IN ({marks}) ORDER BY fiscal_year""", part)
                for row in rows:
                    result.setdefault(row["code"], []).append(dict(row))
        return result

    def save_quarterly_fundamentals(self, code: str, rows: list[dict], source: str):
        columns = ["code", "fiscal_year", "fiscal_quarter", "period_start", "period_end",
                   "filing_date", "published_date", "revenue", "operating_profit",
                   "net_income", "basic_eps", "source", "fidelity", "period_type",
                   "publication_date_known", "retrieved_at", "is_derived", "company_forecast",
                   "field_diagnostics_json"]
        values = []
        for row in rows:
            if not row.get("period_end"):
                continue
            item = dict(row)
            item.update({"code": code, "source": row.get("source") or source,
                         "period_type": row.get("period_type", "QUARTER"),
                         "fiscal_quarter": row.get("fiscal_quarter", "UNKNOWN"),
                         "fiscal_year": str(row.get("fiscal_year") or "UNKNOWN"),
                         "publication_date_known": int(bool(row.get("publication_date_known") or
                                                            row.get("published_date") or row.get("filing_date"))),
                         "is_derived": int(bool(row.get("is_derived"))),
                         "company_forecast": int(bool(row.get("company_forecast"))),
                         "field_diagnostics_json": json.dumps(
                             row.get("field_diagnostics", {}), ensure_ascii=False)})
            values.append([item.get(column) for column in columns])
        if not values:
            return
        marks = ",".join("?" for _ in columns)
        with self.connect() as con:
            con.executemany(f"""INSERT OR REPLACE INTO quarterly_fundamentals
            ({','.join(columns)}) VALUES({marks})""", values)

    def load_quarterly_fundamentals(self, codes: list[str]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {code: [] for code in codes}
        if not codes:
            return result
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            for start in range(0, len(codes), 800):
                part = codes[start:start + 800]
                marks = ",".join("?" for _ in part)
                rows = con.execute(f"""SELECT * FROM quarterly_fundamentals
                    WHERE code IN ({marks}) ORDER BY code,period_end,source""", part)
                for row in rows:
                    item = dict(row)
                    try:
                        item["field_diagnostics"] = json.loads(
                            item.get("field_diagnostics_json") or "{}")
                    except json.JSONDecodeError:
                        item["field_diagnostics"] = {}
                    result.setdefault(row["code"], []).append(item)
        return result

    def save_quarterly_diagnostic(self, row: dict, logic_version: str):
        with self.connect() as con:
            con.execute("""INSERT OR REPLACE INTO quarterly_fundamental_diagnostics
            (code,status,coverage,quarters_available,source_summary,fidelity,latest_period,
             published_date,reason_codes_json,attempted_sources_json,details_json,logic_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                row["code"], row["status"], row.get("coverage"), row.get("quarters_available", 0),
                row.get("source_summary", "N/A"), row.get("fidelity", "N/A"),
                row.get("latest_period"), row.get("published_date"),
                json.dumps(row.get("reason_codes", []), ensure_ascii=False),
                json.dumps(row.get("attempted_sources", []), ensure_ascii=False),
                json.dumps(row.get("details", {}), ensure_ascii=False), logic_version))

    def load_quarterly_diagnostics(self, codes: list[str] | None = None) -> list[dict]:
        query, args = "SELECT * FROM quarterly_fundamental_diagnostics", []
        if codes:
            query += f" WHERE code IN ({','.join('?' for _ in codes)})"
            args.extend(codes)
        query += " ORDER BY code"
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            rows = [dict(row) for row in con.execute(query, args)]
        for row in rows:
            for source, target, fallback in (("reason_codes_json", "reason_codes", []),
                                             ("attempted_sources_json", "attempted_sources", []),
                                             ("details_json", "details", {})):
                try:
                    row[target] = json.loads(row.get(source) or json.dumps(fallback))
                except json.JSONDecodeError:
                    row[target] = fallback
        return rows

    def save_fundamental_diagnostic(self, row: dict, logic_version: str):
        with self.connect() as con:
            con.execute("""INSERT OR REPLACE INTO fundamental_data_diagnostics
            (code,status,fidelity,years_available,initial_years,source_summary,
             fallback_used,reason_code,reason_codes_json,attempted_sources_json,
             details_json,diagnosed_at,logic_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                row["code"], row["status"], row["fidelity"], row["years_available"],
                row.get("initial_years", 0), row.get("source_summary", "N/A"),
                int(row.get("fallback_used", False)), row.get("reason_code"),
                json.dumps(row.get("reason_codes", []), ensure_ascii=False),
                json.dumps(row.get("attempted_sources", []), ensure_ascii=False),
                json.dumps(row.get("details", {}), ensure_ascii=False),
                row.get("diagnosed_at"), logic_version))

    def load_fundamental_diagnostics(self, codes: list[str] | None = None) -> list[dict]:
        query = "SELECT * FROM fundamental_data_diagnostics"
        args: list[str] = []
        if codes:
            query += f" WHERE code IN ({','.join('?' for _ in codes)})"
            args.extend(codes)
        query += " ORDER BY code"
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            rows = [dict(row) for row in con.execute(query, args)]
        for row in rows:
            for source, target in (("reason_codes_json", "reason_codes"),
                                   ("attempted_sources_json", "attempted_sources"),
                                   ("details_json", "details")):
                try:
                    row[target] = json.loads(row.get(source) or ("{}" if target == "details" else "[]"))
                except json.JSONDecodeError:
                    row[target] = {} if target == "details" else []
        return rows

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

    def signal_snapshot(self, signal_id: str) -> dict | None:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM signal_snapshots WHERE signal_id=?",
                              (signal_id,)).fetchone()
        return dict(row) if row else None

    def save_control_members(self, rows: list[dict]):
        _insert_dicts(self, "control_members", rows, "control_group_id,control_code")

    def load_control_members(self, signal_id: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM control_members", []
        if signal_id:
            query += " WHERE signal_id=?"
            args.append(signal_id)
        query += " ORDER BY signal_date,signal_id,control_type,control_rank,control_code"
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            return [dict(row) for row in con.execute(query, args)]

    def save_control_history(self, rows: list[dict]):
        if not rows:
            return
        columns = ["control_group_id", "signal_id", "control_code", "control_type",
                   "date", "session_offset", "close", "return_abs",
                   "benchmark_relative_return", "mfe", "mae"]
        marks = ",".join("?" for _ in columns)
        with self.connect() as con:
            con.executemany(f"""INSERT OR REPLACE INTO control_history
            ({','.join(columns)}) VALUES({marks})""",
                            [[row.get(column) for column in columns] for row in rows])

    def control_validation_rows(self) -> tuple[list[dict], list[dict]]:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            members = [dict(row) for row in con.execute("""SELECT * FROM control_members
                ORDER BY signal_date,signal_id,control_type,control_rank,control_code""")]
            history = [dict(row) for row in con.execute("""SELECT * FROM control_history
                ORDER BY signal_id,control_type,control_code,date""")]
        return members, history

    def import_validation_rows(self, signals: list[dict], history: list[dict],
                               controls: list[dict] | None = None,
                               control_history: list[dict] | None = None):
        _insert_dicts(self, "signal_snapshots", signals, "signal_id")
        _insert_dicts(self, "signal_history", history, "signal_id,date")
        _insert_dicts(self, "control_members", controls or [],
                      "control_group_id,control_code")
        _insert_dicts(self, "control_history", control_history or [],
                      "control_group_id,control_code,date")

    def attach_core_experimental(self, signal_id: str, signal_date: str, analysis, cfg: dict):
        states = {name: result.state for name, result in analysis.results.items()}
        with self.connect() as con:
            con.execute("""UPDATE signal_snapshots SET experimental_version=?,
            experimental_alignment=?,experimental_combination=?,experimental_states_json=?
            WHERE signal_id=? AND signal_date=? AND experimental_version IS NULL""",
            (cfg["experimental_version"], analysis.alignment, analysis.combination,
             json.dumps(states, ensure_ascii=False), signal_id, signal_date))

    def save_experimental_snapshots(self, rows: list[dict]):
        _insert_dicts(self, "experimental_snapshots", rows, "experimental_signal_id")

    def save_experimental_history(self, rows: list[dict]):
        _replace_dicts(self, "experimental_history", rows)

    def save_experimental_control_members(self, rows: list[dict]):
        _insert_dicts(self, "experimental_control_members", rows,
                      "control_group_id,control_code")

    def save_experimental_control_history(self, rows: list[dict]):
        _replace_dicts(self, "experimental_control_history", rows)

    def experimental_rows(self) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            snapshots = [dict(row) for row in con.execute(
                "SELECT * FROM experimental_snapshots ORDER BY signal_date,code,strategy")]
            history = [dict(row) for row in con.execute(
                "SELECT * FROM experimental_history ORDER BY experimental_signal_id,date")]
            controls = [dict(row) for row in con.execute(
                "SELECT * FROM experimental_control_members ORDER BY signal_date,experimental_signal_id,control_type,control_rank")]
            control_history = [dict(row) for row in con.execute(
                "SELECT * FROM experimental_control_history ORDER BY experimental_signal_id,control_type,control_code,date")]
        return snapshots, history, controls, control_history

    def experimental_snapshots(self, code: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM experimental_snapshots", []
        if code:
            query += " WHERE code=?"
            args.append(code)
        query += " ORDER BY signal_date,experimental_signal_id"
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            return [dict(row) for row in con.execute(query, args)]

    def experimental_controls(self, signal_id: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM experimental_control_members", []
        if signal_id:
            query += " WHERE experimental_signal_id=?"
            args.append(signal_id)
        query += " ORDER BY signal_date,experimental_signal_id,control_type,control_rank"
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            return [dict(row) for row in con.execute(query, args)]

    def import_experimental_rows(self, snapshots: list[dict], history: list[dict],
                                 controls: list[dict], control_history: list[dict]):
        _insert_dicts(self, "experimental_snapshots", snapshots, "experimental_signal_id")
        _insert_dicts(self, "experimental_history", history, "experimental_signal_id,date")
        _insert_dicts(self, "experimental_control_members", controls,
                      "control_group_id,control_code")
        _insert_dicts(self, "experimental_control_history", control_history,
                      "control_group_id,control_code,date")


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


def _replace_dicts(db: Database, table: str, rows: list[dict]):
    if not rows:
        return
    with db.connect() as con:
        allowed = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    columns = [column for column in rows[0] if column in allowed and column != "created_at"]
    marks = ",".join("?" for _ in columns)
    with db.connect() as con:
        con.executemany(f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES({marks})",
                        [[row.get(column) for column in columns] for row in rows])


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
