import pytest

from engine.storage_metrics import LIMIT_BYTES, assess, metrics


def point(day, size, fmt='gzip-v1'):
    return {'market_date': day, 'pages_bytes': size, 'format': fmt,
            'generation_id': 'test', 'logical_state_bytes': 1000, 'compressed_state_bytes': 100}


def test_before_and_after_warning_never_hides_insufficient_history():
    assert assess(888039338, None)['budget_status'] == 'RED'
    assert assess(400000000, None)['budget_status'] == 'YELLOW'
    assert assess(400000000, 1_000_000)['budget_status'] == 'GREEN'
    assert assess(800000000, 4_000_000)['budget_status'] == 'RED'


def test_actual_history_and_format_boundary():
    import pandas as pd
    sessions = [str(d.date()) for d in pd.bdate_range('2026-08-01', periods=31)]
    rows = [point(day, 100000000+i*1000) for i, day in enumerate(sessions)]
    value = metrics(rows, sessions)
    assert value['trend_7_sessions']['bytes_per_session'] == 1000
    assert value['trend_30_sessions']['bytes_per_session'] == 1000
    assert value['projected_180_sessions_bytes'] == rows[-1]['pages_bytes']+180000
    assert metrics(rows[-7:], sessions)['trend_7_sessions']['complete'] is False
    rows[-2]['format'] = 'legacy'
    value = metrics(rows, sessions)
    assert value['previous_delta_bytes'] is None
    assert value['trend_7_sessions']['complete'] is False
    assert value['format_transition_delta_bytes'] == 1000


def test_flat_negative_and_over_limit():
    assert assess(1000, 0)['sessions_to_limit'] is None
    assert assess(1000, -10)['projected_180_sessions_bytes'] == 1000
    assert assess(LIMIT_BYTES+1, 1)['remaining_bytes'] == 0
