from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ExperimentalResult:
    strategy: str
    state: str
    positive: bool
    signal: bool
    metrics: dict[str, Any]
    coverage: float
    fidelity: str
    setup_id: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentalAnalysis:
    code: str
    name: str
    as_of: str
    results: dict[str, ExperimentalResult]
    alignment: int
    combination: str

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "as_of": self.as_of,
                "alignment": self.alignment, "combination": self.combination,
                "results": {key: value.to_dict() for key, value in self.results.items()}}


def analyze_experimental_universe(analyses: list, frames: dict[str, pd.DataFrame],
                                  fundamentals: dict[str, dict], meta: dict[str, dict],
                                  benchmark: pd.DataFrame, cfg: dict) -> dict[str, ExperimentalAnalysis]:
    sectors = sector_context(frames, meta, benchmark, cfg)
    output = {}
    for core in analyses:
        code = core.code
        results = {
            "TURTLE": turtle(frames[code], cfg),
            "EARNINGS": earnings_momentum(fundamentals.get(code, {}), cfg, core.as_of, code),
            "SECTOR_RS": sector_result(code, core.metrics.get("momentum_percentile"),
                                       sectors, cfg, core.as_of),
        }
        positives = [key for key, result in results.items() if result.positive]
        output[code] = ExperimentalAnalysis(
            code, core.name, core.as_of, results, len(positives), "+".join(positives) or "NONE")
    return output


def turtle(frame: pd.DataFrame, cfg: dict) -> ExperimentalResult:
    c = cfg["experimental"]["turtle"]
    entry, alternate = int(c["entry_days"]), int(c["alternate_entry_days"])
    atr_days = int(c["atr_days"])
    minimum = max(alternate + 1, 201)
    if len(frame) < minimum:
        return ExperimentalResult("TURTLE", "NOT QUALIFIED", False, False,
                                  {"reason": "history_short"}, 0, "STRICT", None)
    close, high, low = frame["close"], frame["high"], frame["low"]
    current = float(close.iloc[-1])
    pivot20 = float(high.iloc[-entry - 1:-1].max())
    pivot55 = float(high.iloc[-alternate - 1:-1].max())
    tr = pd.concat([(high - low), (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / atr_days, adjust=False, min_periods=atr_days).mean().iloc[-1])
    atr_pct = atr / current * 100 if current else None
    breakout20 = float(close.iloc[-2]) <= pivot20 and current > pivot20
    breakout55 = float(close.iloc[-2]) <= pivot55 and current > pivot55
    prior_event = _last_donchian_breakout(frame.iloc[:-1], entry)
    failed = bool(prior_event and current < float(prior_event[1]))
    distance = (current / pivot20 - 1) * 100
    trend = bool(current > float(frame["ma50"].iloc[-1]) > float(frame["ma200"].iloc[-1]))
    if failed:
        state = "FAILED"
    elif breakout20 or breakout55:
        state = "BREAKOUT"
    elif -float(c["watch_distance_pct"]) <= distance <= 0 and current > float(frame["ma50"].iloc[-1]):
        state = "BREAKOUT WATCH"
    elif prior_event and current >= float(prior_event[1]):
        state = "TRENDING"
    else:
        state = "NOT QUALIFIED"
    breakout_date = str(frame.index[-1].date()) if state == "BREAKOUT" else (
        str(prior_event[0].date()) if prior_event else None)
    setup_id = (f"TURTLE:{breakout_date}:{entry}" if breakout_date else
                f"TURTLE:WATCH:{pivot20:.8g}:{entry}" if state == "BREAKOUT WATCH" else None)
    metrics = {
        "donchian_period": entry, "alternate_period": alternate,
        "breakout_price": pivot20, "breakout_55_price": pivot55,
        "atr": _finite(atr), "atr_pct": _finite(atr_pct),
        "stop": _finite(current - float(c["stop_atr_multiple"]) * atr),
        "stop_distance": _finite(float(c["stop_atr_multiple"]) * atr),
        "position_risk_proxy_pct": _finite(float(c["stop_atr_multiple"]) * atr / current * 100),
        "trend_state": "UPTREND" if trend else "UNFILTERED",
        "volume_ratio": _finite(frame.iloc[-1].get("volume_ratio")),
        "breakout_date": breakout_date,
    }
    return ExperimentalResult("TURTLE", state,
                              state in ("BREAKOUT", "BREAKOUT WATCH"),
                              state in ("BREAKOUT", "BREAKOUT WATCH"), metrics, 100, "STRICT", setup_id)


def earnings_momentum(metrics: dict, cfg: dict, as_of: str = "", code: str = "") -> ExperimentalResult:
    c = cfg["experimental"]["earnings"]
    quarterly = metrics.get("quarterly_earnings") or {}
    eps_growth = _number(quarterly.get("eps_growth"))
    previous_eps = _number(quarterly.get("previous_eps_growth"))
    sales_growth = _number(quarterly.get("sales_growth"))
    previous_sales = _number(quarterly.get("previous_sales_growth"))
    operating_growth = _number(quarterly.get("operating_profit_growth"))
    acceleration = _number(quarterly.get("eps_acceleration"))
    sales_acceleration = _number(quarterly.get("sales_acceleration"))
    coverage = float(quarterly.get("coverage") or 0)
    turnaround = bool(quarterly.get("turnaround_flag"))
    anomaly = bool(quarterly.get("anomaly_flag"))
    stale = bool(quarterly.get("stale"))
    if stale:
        state = "NOT QUALIFIED"
    elif turnaround:
        state = "IMPROVING"
    elif anomaly:
        state = "IMPROVING"
    elif (eps_growth is not None and sales_growth is not None
          and eps_growth >= float(c["eps_growth_min_pct"])
          and sales_growth >= float(c["sales_growth_min_pct"])
          and acceleration is not None and acceleration > 0
          and coverage >= float(c["strong_coverage_min_pct"])):
        state = "STRONG"
    elif (eps_growth is not None and eps_growth >= float(c["eps_growth_min_pct"])
          and coverage >= float(c["momentum_coverage_min_pct"])
          and (sales_growth is None or sales_growth >= 0)
          and (operating_growth is None or operating_growth >= 0)):
        state = "EARNINGS MOMENTUM"
    elif any(value is not None and value > 0 for value in
             (eps_growth, sales_growth, operating_growth, acceleration)):
        state = "IMPROVING"
    else:
        state = "NOT QUALIFIED"
    source = str(quarterly.get("source") or "N/A")
    filing = quarterly.get("published_date")
    fidelity = str(quarterly.get("fidelity") or "N/A")
    setup_basis = str(quarterly.get("latest_period") or filing or as_of or "unknown")
    setup_id = f"EARNINGS:{code}:{setup_basis}" if state in ("STRONG", "EARNINGS MOMENTUM") else None
    result_metrics = {
        "eps_growth": eps_growth, "previous_eps_growth": previous_eps,
        "sales_growth": sales_growth, "previous_sales_growth": previous_sales,
        "operating_profit_growth": operating_growth,
        "eps_acceleration": _finite(acceleration), "sales_acceleration": sales_acceleration,
        "earnings_revision": None, "turnaround_flag": turnaround,
        "anomaly_flag": anomaly, "data_coverage": coverage,
        "filing_date": filing, "published_date": filing,
        "available_from": quarterly.get("available_from"),
        "publication_date_known": quarterly.get("publication_date_known"),
        "source": source, "fidelity": fidelity,
        "latest_period": quarterly.get("latest_period"),
        "fiscal_quarter": quarterly.get("fiscal_quarter"),
        "period_type": quarterly.get("period_type"),
        "missing": quarterly.get("missing", []),
        "reason_codes": quarterly.get("reason_codes", []),
        "eps_period_match_status": quarterly.get("eps_period_match_status"),
        "eps_continuity_warning": quarterly.get("eps_continuity_warning"),
        "stale": stale,
    }
    return ExperimentalResult("EARNINGS", state,
                              state in ("STRONG", "EARNINGS MOMENTUM"),
                              state in ("STRONG", "EARNINGS MOMENTUM"),
                              result_metrics, coverage, fidelity, setup_id)


def sector_context(frames: dict[str, pd.DataFrame], meta: dict[str, dict],
                   benchmark: pd.DataFrame, cfg: dict) -> dict[str, dict]:
    rows = []
    for code, frame in frames.items():
        sector = (meta.get(code) or {}).get("sector33")
        if not sector or len(frame) < 127:
            continue
        rows.append({"code": code, "sector": sector,
                     "r1": _period_return(frame.close, 21),
                     "r3": _period_return(frame.close, 63),
                     "r6": _period_return(frame.close, 126)})
    if not rows:
        return {}
    table = pd.DataFrame(rows)
    sector = table.groupby("sector")[["r1", "r3", "r6"]].mean()
    weights = cfg["experimental"]["sector"]["weights"]
    sector["score"] = (sector.r1 * float(weights["return_1m"])
                       + sector.r3 * float(weights["return_3m"])
                       + sector.r6 * float(weights["return_6m"]))
    sector["percentile"] = sector["score"].rank(pct=True) * 100
    sector["rank"] = sector["score"].rank(method="min", ascending=False).astype(int)
    b1, b3, b6 = (_period_return(benchmark.close, days) for days in (21, 63, 126))
    result = {}
    for code, info in table.set_index("code").iterrows():
        value = sector.loc[info.sector]
        result[str(code)] = {
            "sector_name": info.sector, "sector_rank": int(value["rank"]),
            "sector_count": int(len(sector)), "sector_percentile": float(value["percentile"]),
            "sector_return_1m": float(value.r1), "sector_return_3m": float(value.r3),
            "sector_return_6m": float(value.r6),
            "sector_relative_strength": float(value.r6 - b6),
            "benchmark_return_1m": b1, "benchmark_return_3m": b3,
            "benchmark_return_6m": b6,
        }
    return result


def sector_result(code: str, stock_percentile, context: dict[str, dict], cfg: dict,
                  as_of: str = "") -> ExperimentalResult:
    metrics = dict(context.get(code) or {})
    stock = _number(stock_percentile)
    metrics["stock_relative_strength"] = stock
    sector = _number(metrics.get("sector_percentile"))
    c = cfg["experimental"]["sector"]
    if sector is None:
        state = "NOT QUALIFIED"
    elif sector >= float(c["leading_percentile"]) and stock is not None and stock >= float(c["stock_momentum_percentile"]):
        state = "LEADING SECTOR"
    elif sector >= float(c["strong_percentile"]) and stock is not None and stock >= float(c["stock_momentum_percentile"]):
        state = "STRONG"
    elif sector >= float(c["strong_percentile"]):
        state = "SECTOR STRONG"
    elif stock is not None and stock >= float(c["stock_momentum_percentile"]):
        state = "STOCK STRONG / SECTOR WEAK"
    else:
        state = "NOT QUALIFIED"
    positive = state in ("LEADING SECTOR", "STRONG")
    setup_id = f"SECTOR:{code}:{metrics.get('sector_name')}:{as_of[:7]}" if positive else None
    return ExperimentalResult("SECTOR_RS", state, positive, positive, metrics,
                              100 if metrics else 0, "PRACTICAL", setup_id)


def _last_donchian_breakout(frame: pd.DataFrame, days: int):
    if len(frame) <= days:
        return None
    rolling = frame.high.shift(1).rolling(days, min_periods=days).max()
    events = frame.close.gt(rolling) & frame.close.shift(1).le(rolling)
    if not events.any():
        return None
    date = events[events].index[-1]
    return date, float(rolling.loc[date])


def _period_return(series: pd.Series, days: int) -> float:
    if len(series) <= days:
        return float("nan")
    return (float(series.iloc[-1]) / float(series.iloc[-days - 1]) - 1) * 100


def _growth(previous: float, current: float) -> float | None:
    if previous == 0:
        return None
    return (current / abs(previous) - 1) * 100


def _number(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _finite(value):
    return _number(value)
