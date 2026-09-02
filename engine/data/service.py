from __future__ import annotations

import logging
import os
import pandas as pd
from .demo import NAMES, demo_fundamentals, make_demo_history
from .yahoo import YahooProvider
from .jpx import JPXUniverseProvider, select_scope
from ..database import Database
from ..config import load_config
from ..annual_eps import (annual_eps_profile, diagnostic_row, resolve_with_fallback,
                          source_family)

log = logging.getLogger(__name__)


class DataService:
    def __init__(self, db: Database):
        self.db = db

    def universe(self, mode: str) -> dict[str, str]:
        return {k: v for k, v in NAMES.items() if k != "TOPIX"}

    def scan(self, mode: str = "デモ", api_key: str | None = None,
             scope: str = "主要500+Growth", edinet_key: str | None = None,
             progress=None, jquants_key: str | None = None):
        if mode == "無料実用":
            return self._free_scan(scope, edinet_key, progress, jquants_key)
        universe = self.universe(mode)
        raw, fundamentals, sources, errors = {}, {}, {}, []
        for code in universe:
            try:
                raw[code], fundamentals[code], sources[code] = self.get(code, mode, api_key)
            except Exception as exc:
                errors.append(f"{code}: {exc}")
        return universe, raw, fundamentals, sources, errors

    def _free_scan(self, scope: str, edinet_key: str | None, progress=None,
                   jquants_key: str | None = None):
        errors = []
        cached_master = self.db.load_securities()
        try:
            master = JPXUniverseProvider().fetch()
            self.db.save_securities(master)
            self.db.log_fetch("JPX公式 上場銘柄一覧", "国内株式", True, f"{len(master)}銘柄")
        except Exception as exc:
            if not cached_master:
                raise RuntimeError(f"JPX銘柄一覧を取得できず、キャッシュもありません: {exc}") from exc
            master = cached_master
            errors.append(f"JPX銘柄一覧: 前回キャッシュを使用（{exc}）")
        selected = select_scope(master, scope)
        universe = {r["code"]: r["name"] for r in selected}
        codes = list(universe)
        yahoo = YahooProvider()
        cached = self.db.load_prices_many(codes, yahoo.name)
        minimum = 260
        missing = [c for c in codes if len(cached.get(c, ())) < minimum]
        ready = [c for c in codes if c not in set(missing)]
        downloaded = {}

        def batch_progress(done, total, phase=""):
            if progress: progress(phase, done, total)

        if missing:
            fresh, batch_errors = yahoo.histories(
                missing, period="2y", batch_size=80,
                on_batch=lambda d, t: batch_progress(d, t, "初回履歴"))
            downloaded.update(fresh); errors.extend(batch_errors)
            if fresh: self.db.save_prices_bulk(fresh, yahoo.name)
        if ready:
            latest, batch_errors = yahoo.histories(
                ready, period="10d", batch_size=80,
                on_batch=lambda d, t: batch_progress(d, t, "差分更新"))
            downloaded.update(latest); errors.extend(batch_errors)
            if latest: self.db.save_prices_bulk(latest, yahoo.name)
        frames = self.db.load_prices_many(codes, yahoo.name)
        frames = {c: df for c, df in frames.items() if len(df) >= 200}
        if not frames:
            raise RuntimeError("Yahoo日足を取得できませんでした。時間を空けて再実行してください")
        fund_cache = self.db.load_fundamentals(list(frames))
        if edinet_key:
            try:
                from .edinet import EdinetProvider
                updates, edinet_errors = EdinetProvider(edinet_key).update_recent()
                if updates: self.db.save_fundamentals(updates)
                errors.extend(edinet_errors)
                fund_cache = self.db.load_fundamentals(list(frames))
                self.db.log_fetch("金融庁 EDINET API v2", "直近提出書類", True, f"{len(updates)}件更新")
            except Exception as exc:
                errors.append(f"EDINET: 財務更新を継続できませんでした（{exc}）")
                self.db.log_fetch("金融庁 EDINET API v2", "直近提出書類", False, str(exc))
        cfg = load_config()
        annual_cfg = cfg["free_data"]["annual_eps"]
        minimum = int(annual_cfg["minimum_years"])
        preferred = int(annual_cfg["preferred_years"])
        conflict_pct = float(annual_cfg["conflict_pct"])
        annual_cache = self.db.load_annual_eps(list(frames))
        initial_profiles = {code: annual_eps_profile(annual_cache.get(code, []), minimum,
                                                     preferred, conflict_pct)
                            for code in frames}
        missing_annual = [code for code, profile in initial_profiles.items()
                          if profile["years_available"] < minimum]
        attempts: dict[str, list[str]] = {code: [] for code in frames}
        reasons: dict[str, list[str]] = {code: [] for code in frames}
        jq = None
        jq_key = jquants_key or os.getenv("JQUANTS_API_KEY")
        for code in missing_annual:
            existing = annual_cache.get(code, [])
            edinet_years = len({r.get("fiscal_year") for r in existing
                                if source_family(r.get("source")).startswith("EDINET")})
            reasons[code].append("EDINET_NOT_FOUND" if edinet_years == 0
                                 else "EDINET_INSUFFICIENT_YEARS")
            if annual_cfg.get("enable_jquants_fallback", True) and not jq_key:
                reasons[code].append("JQUANTS_NOT_CONFIGURED")
        if missing_annual:
            limit = int(cfg["free_data"]["annual_eps_updates_per_run"])
            # 強い銘柄から順に補完し、毎回少数ずつ自動でCoverageを広げる。
            missing_annual.sort(key=lambda code: _momentum(frames[code]), reverse=True)
            for code in missing_annual[:limit]:
                fetchers = []
                if annual_cfg.get("enable_jquants_fallback", True) and jq_key:
                    def fetch_jquants(target=code):
                        nonlocal jq
                        if jq is None:
                            from .jquants import JQuantsProvider
                            jq = JQuantsProvider(jq_key)
                        return jq.annual_eps(target)
                    fetchers.append(("JQUANTS", fetch_jquants))
                if annual_cfg.get("enable_yahoo_fallback", True):
                    fetchers.append(("YAHOO", lambda target=code: yahoo.annual_eps(target)))
                resolved = resolve_with_fallback(
                    annual_cache.get(code, []), fetchers, minimum, preferred,
                    conflict_pct, reasons[code])
                attempts[code] = resolved["attempted_sources"]
                reasons[code] = resolved["reason_codes"]
                if resolved["added_records"]:
                    self.db.save_annual_eps(code, resolved["added_records"], "Annual EPS fallback")
                errors.extend(f"{code}: 年次EPS {message}" for message in resolved["errors"])
            annual_cache = self.db.load_annual_eps(list(frames))
        profiles = {code: annual_eps_profile(annual_cache.get(code, []), minimum,
                                             preferred, conflict_pct, reasons[code])
                    for code in frames}
        if annual_cfg.get("diagnostics", True):
            for code, profile in profiles.items():
                row = diagnostic_row(code, profile,
                                     initial_profiles[code]["years_available"], attempts[code])
                self.db.save_fundamental_diagnostic(row, cfg["logic_version"])
        fundamentals = {c: {"eps_growth": fund_cache.get(c, {}).get("eps_growth"),
                            "sales_growth": fund_cache.get(c, {}).get("sales_growth"),
                            "operating_profit_growth": fund_cache.get(c, {}).get("operating_profit_growth"),
                            "annual_eps": profiles[c]["records"],
                            "annual_eps_source": profiles[c]["source_summary"],
                            "annual_eps_profile": profiles[c],
                            "fundamental_source": fund_cache.get(c, {}).get("source", "N/A"),
                            "fundamental_date": fund_cache.get(c, {}).get("filing_date")}
                        for c in frames}
        sources = {c: yahoo.name for c in frames}
        self.db.log_fetch(yahoo.name, scope, True, f"{len(frames)}/{len(codes)}銘柄")
        status = {"requested": len(codes), "available": len(frames),
                  "price": self.db.source_status(yahoo.name),
                  "universe_source": "JPX公式", "fundamentals": len(fund_cache)}
        names = {c: universe[c] for c in frames}
        return names, frames, fundamentals, sources, errors, status

    def get(self, code: str, mode: str = "デモ", api_key: str | None = None):
        if mode == "デモ":
            frame, fundamentals, source = make_demo_history(code), demo_fundamentals(code), "DEMO"
        elif mode == "Yahoo Finance":
            provider = YahooProvider()
            frame, fundamentals, source = provider.history(code), provider.fundamentals(code), provider.name
        else:
            try:
                from .jquants import JQuantsProvider
                provider = JQuantsProvider(api_key)
                end = pd.Timestamp.now(tz="Asia/Tokyo")
                frame = provider.history(code, end - pd.Timedelta(days=760), end)
                fundamentals = {"eps_growth": None, "sales_growth": None, "source": provider.name}
                source = provider.name
            except Exception as exc:
                self.db.log_fetch("J-Quants API V2", code, False, str(exc))
                cached = self.db.load_prices(code, "J-Quants API V2")
                if cached.empty:
                    raise
                frame = cached.drop(columns=["source"], errors="ignore")
                fundamentals, source = {"eps_growth": None, "sales_growth": None}, "J-Quants cache"
        self.db.save_prices(code, frame, source)
        self.db.log_fetch(source, code, True, f"{len(frame)} rows")
        return frame, fundamentals, source

    def benchmark(self, mode: str):
        if mode == "デモ":
            return make_demo_history("TOPIX")
        try:
            return YahooProvider().history("TOPIX")
        except Exception as exc:
            log.warning("TOPIX fallback: %s", exc)
            return make_demo_history("TOPIX")


def _momentum(frame: pd.DataFrame) -> float:
    if len(frame) < 64:
        return -1e9
    return float(frame.close.iloc[-1] / frame.close.iloc[-64] - 1)
