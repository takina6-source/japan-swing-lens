"""Physical publication accounting; history is measured, never synthesized."""
from __future__ import annotations

import math
from pathlib import Path

LIMIT_BYTES = 1_000_000_000  # Conservative decimal interpretation of GitHub's 1 GB.


def tree_bytes(root: Path) -> int:
    files = list(root.rglob('*'))
    if any(p.is_symlink() for p in files):
        raise ValueError('publication must not contain symlinks')
    return sum(p.stat().st_size for p in files if p.is_file())


def trend(points: list[dict], sessions: list[str], window: int) -> dict:
    current = points[-1]
    eligible = [p for p in points if p['format'] == current['format']]
    dates = sorted(set(sessions))
    dates = [d for d in dates if d <= current['market_date']]
    wanted = dates[-(window + 1):]
    by_date = {p['market_date']: p for p in eligible}
    complete = len(wanted) == window + 1 and all(d in by_date for d in wanted)
    return {'window_sessions': window, 'observations': sum(d in by_date for d in wanted),
            'required_observations': window + 1, 'complete': complete,
            'start_market_date': wanted[0] if wanted else None,
            'end_market_date': current['market_date'],
            'bytes_per_session': ((current['pages_bytes'] - by_date[wanted[0]]['pages_bytes']) / window
                                  if complete else None)}


def assess(size: int, growth: float | None) -> dict:
    projection = size + max(0, growth) * 180 if growth is not None else None
    runway = max(0, (LIMIT_BYTES - size) / growth) if growth is not None and growth > 0 else None
    status = ('RED' if size >= LIMIT_BYTES * .85
              or (projection is not None and projection >= LIMIT_BYTES)
              or (runway is not None and runway <= 70)
              else 'YELLOW' if size >= LIMIT_BYTES * .70 or growth is None
              or (projection is not None and projection >= LIMIT_BYTES * .85)
              else 'GREEN')
    return {'budget_status': status, 'limit_bytes': LIMIT_BYTES,
            'pages_bytes': size, 'usage_pct': round(size / LIMIT_BYTES * 100, 6),
            'remaining_bytes': max(0, LIMIT_BYTES - size),
            'projected_180_sessions_bytes': math.ceil(projection) if projection is not None else None,
            'sessions_to_limit': round(runway, 6) if runway is not None else None,
            'growth_bytes_per_session': growth,
            'forecast_confidence': 'INSUFFICIENT_HISTORY' if growth is None else 'MEASURED_TREND',
            'unit': 'bytes; MiB=1048576 bytes; limit=decimal 1 GB'}


def metrics(points: list[dict], sessions: list[str]) -> dict:
    if not points:
        raise ValueError('metrics require a measured point')
    seven, thirty = trend(points, sessions, 7), trend(points, sessions, 30)
    rates = [t['bytes_per_session'] for t in (seven, thirty) if t['complete']]
    current = points[-1]
    previous = points[-2] if len(points) > 1 else None
    comparable = previous is not None and previous['format'] == current['format']
    result = assess(current['pages_bytes'], max(rates) if rates else None)
    result.update({
        'logical_state_bytes': current['logical_state_bytes'],
        'compressed_state_bytes': current['compressed_state_bytes'],
        'market_date': current['market_date'], 'generation_id': current['generation_id'],
        'previous_delta_bytes': current['pages_bytes'] - previous['pages_bytes'] if comparable else None,
        'format_transition_delta_bytes': current['pages_bytes'] - previous['pages_bytes']
        if previous and not comparable else None,
        'trend_7_sessions': seven, 'trend_30_sessions': thirty,
        'forecast_method': 'max(complete 7/30 session slopes), negative growth floored at zero for projection',
    })
    return result
