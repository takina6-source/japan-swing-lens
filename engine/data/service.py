from __future__ import annotations

import logging
import os
from datetime import date
import pandas as pd
from .demo import NAMES, demo_fundamentals, make_demo_history
from .yahoo import YahooProvider
from .jpx import JPXUniverseProvider, select_scope
from ..database import Database
from ..config import load_config
from ..annual_eps import (annual_eps_profile, diagnostic_row, resolve_with_fallback,
                          source_family, update_queue_metadata, classify_source_error)
from ..quarterly_fundamentals import quarterly_diagnostic, quarterly_profile
from ..update_queue import attempt_record, build_fair_update_queue

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
        previous_annual_diagnostics = {
            row["code"]: row for row in self.db.load_fundamental_diagnostics(list(frames))}
        initial_profiles = {code: annual_eps_profile(annual_cache.get(code, []), minimum,
                                                     preferred, conflict_pct)
                            for code in frames}
        missing_annual = [code for code, profile in initial_profiles.items()
                          if profile["years_available"] < minimum]
        attempts: dict[str, list[str]] = {
            code: list(previous_annual_diagnostics.get(code, {}).get("attempted_sources") or [])
            for code in frames}
        reasons: dict[str, list[str]] = {
            code: [reason for reason in
                   (previous_annual_diagnostics.get(code, {}).get("reason_codes") or [])
                   if reason not in {"INSUFFICIENT_TOTAL_YEARS", "UPDATE_LIMIT_NOT_ATTEMPTED"}]
            for code in frames}
        source_attempts: dict[str, dict[str, str]] = {
            code: dict((previous_annual_diagnostics.get(code, {}).get("details") or {}).get(
                "source_attempts") or {}) for code in frames}
        jq = None
        jq_key = jquants_key or os.getenv("JQUANTS_API_KEY")
        cooldowns = cfg["free_data"].get("update_queue", {}).get("cooldown_days", {})
        for code in missing_annual:
            existing = annual_cache.get(code, [])
            edinet_years = len({r.get("fiscal_year") for r in existing
                                if source_family(r.get("source")).startswith("EDINET")})
            reasons[code].append("EDINET_NOT_FOUND" if edinet_years == 0
                                 else "EDINET_INSUFFICIENT_YEARS")
            if "EDINET" not in attempts[code]:
                attempts[code].append("EDINET")
            source_attempts[code]["EDINET"] = ("EDINET_NOT_FOUND" if edinet_years == 0
                                                   else "EDINET_INSUFFICIENT_YEARS")
            if annual_cfg.get("enable_jquants_fallback", True) and not jq_key:
                reasons[code].append("JQUANTS_NOT_CONFIGURED")
                if "JQUANTS" not in attempts[code]:
                    attempts[code].append("JQUANTS")
                source_attempts[code]["JQUANTS"] = "JQUANTS_NOT_CONFIGURED"
        queue = {}
        if missing_annual:
            limit = int(cfg["free_data"]["annual_eps_updates_per_run"])
            enabled_annual_sources = []
            if annual_cfg.get("enable_jquants_fallback", True) and jq_key:
                enabled_annual_sources.append("JQUANTS")
            if annual_cfg.get("enable_yahoo_fallback", True):
                enabled_annual_sources.append("YAHOO")
            missing_annual, queue = build_fair_update_queue(
                missing_annual, self.db.load_update_attempts("ANNUAL_EPS", missing_annual),
                {code: initial_profiles[code]["years_available"] for code in missing_annual},
                {code: _momentum(frames[code]) for code in missing_annual}, limit,
                {code: enabled_annual_sources for code in missing_annual})
            selected_annual = missing_annual[:limit]
            for code in selected_annual:
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
                attempts[code] = list(dict.fromkeys([*attempts[code],
                                                     *resolved["attempted_sources"]]))
                reasons[code] = resolved["reason_codes"]
                source_attempts[code].update(resolved["source_attempts"])
                attempted_now = dict(resolved["source_attempts"])
                if annual_cfg.get("enable_jquants_fallback", True) and not jq_key:
                    attempted_now["JQUANTS"] = "JQUANTS_NOT_CONFIGURED"
                saved_attempts = []
                for source, reason in attempted_now.items():
                    record = attempt_record(code, "ANNUAL_EPS", source, reason,
                                            cooldown_days=cooldowns)
                    self.db.save_update_attempt(record)
                    saved_attempts.append(record)
                if saved_attempts:
                    queue[code]["last_attempt_at"] = max(
                        row["last_attempt_at"] for row in saved_attempts)
                    queue[code]["next_eligible_at"] = min(
                        row["next_eligible_at"] for row in saved_attempts
                        if row["source"] in enabled_annual_sources)
                if resolved["added_records"]:
                    self.db.save_annual_eps(code, resolved["added_records"], "Annual EPS fallback")
                errors.extend(f"{code}: 年次EPS {message}" for message in resolved["errors"])
            for code in missing_annual[limit:]:
                reasons[code].append("UPDATE_LIMIT_NOT_ATTEMPTED")
            annual_cache = self.db.load_annual_eps(list(frames))
        profiles = {code: annual_eps_profile(annual_cache.get(code, []), minimum,
                                             preferred, conflict_pct, reasons[code])
                    for code in frames}
        if annual_cfg.get("diagnostics", True):
            for code, profile in profiles.items():
                row = diagnostic_row(code, profile,
                                     initial_profiles[code]["years_available"], attempts[code],
                                     update_state=queue.get(code, {}).get(
                                         "update_state", "CURRENT"),
                                     next_update_rank=queue.get(code, {}).get("next_update_rank"),
                                     source_attempts=source_attempts[code],
                                     queue_metadata=queue.get(code))
                self.db.save_fundamental_diagnostic(row, cfg["logic_version"])
        quarterly_cfg = cfg["free_data"]["quarterly_fundamentals"]
        quarterly_cache = self.db.load_quarterly_fundamentals(list(frames))
        previous_quarterly_diagnostics = {
            row["code"]: row for row in self.db.load_quarterly_diagnostics(list(frames))}
        as_of_by_code = {code: str(frame.index[-1].date()) for code, frame in frames.items()}
        initial_quarterly = {code: quarterly_profile(quarterly_cache.get(code, []), cfg,
                                                     as_of_by_code[code])
                             for code in frames}
        refresh_quarterly = []
        for code, profile in initial_quarterly.items():
            records = quarterly_cache.get(code, [])
            newest = max((str(row.get("updated_at") or "")[:10] for row in records), default="")
            stale = (not newest or
                     (pd.Timestamp(date.today()) - pd.Timestamp(newest)).days >= int(quarterly_cfg["cache_days"]))
            needs_data = profile["quarters_available"] < int(quarterly_cfg["minimum_quarters"])
            if needs_data or stale:
                refresh_quarterly.append(code)
        limit = int(quarterly_cfg["updates_per_run"])
        enabled_quarterly_sources = []
        if quarterly_cfg.get("enable_jquants", True) and jq_key:
            enabled_quarterly_sources.append("JQUANTS")
        if quarterly_cfg.get("enable_yahoo", True):
            enabled_quarterly_sources.append("YAHOO")
        refresh_quarterly, quarterly_queue = build_fair_update_queue(
            refresh_quarterly,
            self.db.load_update_attempts("QUARTERLY_FUNDAMENTALS", refresh_quarterly),
            {code: initial_quarterly[code]["quarters_available"] for code in refresh_quarterly},
            {code: _momentum(frames[code]) for code in refresh_quarterly}, limit,
            {code: enabled_quarterly_sources for code in refresh_quarterly})
        quarterly_attempts: dict[str, list[str]] = {
            code: list(previous_quarterly_diagnostics.get(code, {}).get(
                "attempted_sources") or []) for code in frames}
        quarterly_source_attempts: dict[str, dict[str, str]] = {
            code: dict((previous_quarterly_diagnostics.get(code, {}).get("details") or {}).get(
                "source_attempts") or {}) for code in frames}
        quarterly_field_diagnostics: dict[str, dict] = {
            code: dict((previous_quarterly_diagnostics.get(code, {}).get("details") or {}).get(
                "field_diagnostics") or {}) for code in frames}
        quarterly_reasons: dict[str, list[str]] = {code: [] for code in frames}
        for code in refresh_quarterly[:limit]:
            current = list(quarterly_cache.get(code, []))
            attempted_now = {}
            if quarterly_cfg.get("enable_jquants", True):
                if "JQUANTS" not in quarterly_attempts[code]:
                    quarterly_attempts[code].append("JQUANTS")
                if jq_key:
                    try:
                        if jq is None:
                            from .jquants import JQuantsProvider
                            jq = JQuantsProvider(jq_key)
                        rows = jq.quarterly_fundamentals(code)
                        if rows:
                            self.db.save_quarterly_fundamentals(code, rows, jq.name)
                            current.extend(rows)
                            quarterly_source_attempts[code]["JQUANTS"] = "SUCCESS"
                            attempted_now["JQUANTS"] = "SUCCESS"
                        else:
                            quarterly_reasons[code].append("JQUANTS_NO_DATA")
                            quarterly_source_attempts[code]["JQUANTS"] = "JQUANTS_NO_DATA"
                            attempted_now["JQUANTS"] = "JQUANTS_NO_DATA"
                    except Exception as exc:
                        reason = classify_source_error("JQUANTS", exc)
                        quarterly_reasons[code].append(reason)
                        quarterly_source_attempts[code]["JQUANTS"] = reason
                        attempted_now["JQUANTS"] = reason
                        errors.append(f"{code}: J-Quants四半期財務 {reason}")
                else:
                    quarterly_reasons[code].append("JQUANTS_NOT_CONFIGURED")
                    quarterly_source_attempts[code]["JQUANTS"] = "JQUANTS_NOT_CONFIGURED"
                    attempted_now["JQUANTS"] = "JQUANTS_NOT_CONFIGURED"
            # EDINET quarterly coverage is not dependable after the disclosure-system change.
            if "EDINET" not in quarterly_attempts[code]:
                quarterly_attempts[code].append("EDINET")
            quarterly_reasons[code].append("EDINET_NOT_AVAILABLE")
            quarterly_source_attempts[code]["EDINET"] = "EDINET_NOT_AVAILABLE"
            interim = quarterly_profile(current, cfg, as_of_by_code[code])
            if (quarterly_cfg.get("enable_yahoo", True)
                    and interim["coverage"] < float(quarterly_cfg["target_coverage_pct"])):
                if "YAHOO" not in quarterly_attempts[code]:
                    quarterly_attempts[code].append("YAHOO")
                try:
                    rows = yahoo.quarterly_fundamentals(code)
                    quarterly_field_diagnostics[code] = dict(
                        getattr(yahoo, "last_quarterly_diagnostics", {}) or {})
                    if rows:
                        self.db.save_quarterly_fundamentals(code, rows, yahoo.name)
                        quarterly_source_attempts[code]["YAHOO"] = "SUCCESS"
                        attempted_now["YAHOO"] = "SUCCESS"
                    else:
                        quarterly_reasons[code].append("YAHOO_NO_DATA")
                        quarterly_source_attempts[code]["YAHOO"] = "YAHOO_NO_DATA"
                        attempted_now["YAHOO"] = "YAHOO_NO_DATA"
                except Exception as exc:
                    reason = classify_source_error("YAHOO", exc)
                    quarterly_reasons[code].append(reason)
                    quarterly_source_attempts[code]["YAHOO"] = reason
                    attempted_now["YAHOO"] = reason
                    errors.append(f"{code}: Yahoo四半期財務 {reason}")
            saved_attempts = []
            for source, reason in attempted_now.items():
                record = attempt_record(code, "QUARTERLY_FUNDAMENTALS", source, reason,
                                        cooldown_days=cooldowns)
                self.db.save_update_attempt(record)
                saved_attempts.append(record)
            if saved_attempts:
                quarterly_queue[code]["last_attempt_at"] = max(
                    row["last_attempt_at"] for row in saved_attempts)
                eligible_saved = [row for row in saved_attempts
                                  if row["source"] in enabled_quarterly_sources]
                if eligible_saved:
                    quarterly_queue[code]["next_eligible_at"] = min(
                        row["next_eligible_at"] for row in eligible_saved)
        for code in refresh_quarterly[limit:]:
            quarterly_reasons[code].append("UPDATE_LIMIT_NOT_ATTEMPTED")
        quarterly_cache = self.db.load_quarterly_fundamentals(list(frames))
        quarterly_profiles = {code: quarterly_profile(quarterly_cache.get(code, []), cfg,
                                                       as_of_by_code[code])
                              for code in frames}
        for code, profile in quarterly_profiles.items():
            diagnostic = quarterly_diagnostic(
                code, profile, quarterly_attempts[code],
                update_state=quarterly_queue.get(code, {}).get("update_state", "CURRENT"),
                next_update_rank=quarterly_queue.get(code, {}).get("next_update_rank"),
                source_attempts=quarterly_source_attempts[code],
                additional_reasons=quarterly_reasons[code])
            previous_details = (previous_quarterly_diagnostics.get(code, {}).get("details") or {})
            diagnostic["details"]["last_attempt_at"] = (
                date.today().isoformat() if code in refresh_quarterly[:limit]
                else previous_details.get("last_attempt_at"))
            diagnostic["details"].update(quarterly_queue.get(code, {}))
            if quarterly_field_diagnostics[code]:
                diagnostic["details"]["field_diagnostics"] = quarterly_field_diagnostics[code]
            self.db.save_quarterly_diagnostic(
                diagnostic, cfg["logic_version"])
        fundamentals = {c: {"eps_growth": fund_cache.get(c, {}).get("eps_growth"),
                            "sales_growth": fund_cache.get(c, {}).get("sales_growth"),
                            "operating_profit_growth": fund_cache.get(c, {}).get("operating_profit_growth"),
                            "annual_eps": profiles[c]["records"],
                            "annual_eps_source": profiles[c]["source_summary"],
                            "annual_eps_profile": profiles[c],
                            "quarterly_earnings": quarterly_profiles[c],
                            "fundamental_source": fund_cache.get(c, {}).get("source", "N/A"),
                            "fundamental_date": fund_cache.get(c, {}).get("filing_date")}
                        for c in frames}
        sources = {c: yahoo.name for c in frames}
        self.db.log_fetch(yahoo.name, scope, True, f"{len(frames)}/{len(codes)}銘柄")
        status = {"requested": len(codes), "available": len(frames),
                  "price": self.db.source_status(yahoo.name),
                  "universe_source": "JPX公式", "fundamentals": len(fund_cache),
                  "quarterly_fundamentals": sum(bool(p["quarters_available"])
                                                for p in quarterly_profiles.values())}
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
