"""Lossless continuity transport. Phase 1 does not change public writers.

A configured seed is mandatory: only a gzip 404 permits legacy lookup. Compressed
seeds require a manifest; corrupt or ambiguous generations never fall back.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import warnings
import zlib
from collections import Counter
from datetime import date

import requests


class StateError(RuntimeError):
    pass


_COMMON = {
    'signals': ('signal_id',), 'history': ('signal_id', 'date'),
    'controls': ('control_group_id', 'control_code'),
    'control_history': ('control_group_id', 'control_code', 'date'),
}
RESEARCH_KEYS = {
    'hypotheses': ('hypothesis_version',), 'families': ('family_id', 'family_version'),
    'research_events': ('research_event_id',),
    'validation_subjects': ('validation_subject_id',),
    'research_control_members': ('control_group_id', 'control_code'),
    'research_control_history': ('control_group_id', 'control_code', 'date'),
    'research_subject_history': ('validation_subject_id', 'date'),
    'research_checkpoint_evaluations': ('hypothesis_version', 'checkpoint_name'),
    'research_results': ('hypothesis_version',),
    'research_family_results': ('family_id', 'family_version'),
    'research_telemetry': ('run_id',),
}


def keys_for(kind):
    if kind == 'research':
        return RESEARCH_KEYS
    if kind not in {'validation', 'experimental'}:
        raise StateError('unknown state kind')
    return {table: tuple('experimental_signal_id' if kind == 'experimental' and key == 'signal_id'
                         else key for key in keys) for table, keys in _COMMON.items()}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def compress(raw):
    """Fixed level/mtime and no filename. Preserve input bytes, including whitespace."""
    out = io.BytesIO()
    with gzip.GzipFile(filename='', fileobj=out, mode='wb', compresslevel=9, mtime=0) as stream:
        stream.write(raw)
    return out.getvalue()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StateError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise StateError('nonfinite number')
    return result


def parse(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object, parse_float=_finite_float,
                          parse_constant=lambda value: (_ for _ in ()).throw(StateError('nonfinite JSON')))
    except (ValueError, UnicodeError) as exc:
        raise StateError('invalid JSON') from exc


def identities(rows, keys):
    result = set()
    for row in rows:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] for k in keys):
            raise StateError(f'missing/invalid identity: {keys}')
        identity = tuple(row[k] for k in keys)
        if identity in result:
            raise StateError(f'duplicate record: {keys}')
        result.add(identity)
    return result


def describe(raw, kind):
    value = parse(raw)
    keys = keys_for(kind)
    required = set(keys) | ({'research_logic_version'} if kind == 'research' else set())
    if not isinstance(value, dict) or set(value) != required:
        raise StateError(f'{kind}: missing or unsupported state sections')
    if kind == 'research':
        from .research.registry import RESEARCH_LOGIC_VERSION
        if value['research_logic_version'] != RESEARCH_LOGIC_VERSION:
            raise StateError('unsupported research version')
    tables = {}
    dates = []
    versions = set()
    for table, identity_keys in keys.items():
        rows = value[table]
        if not isinstance(rows, list):
            raise StateError(f'{table}: expected array')
        ids = identities(rows, identity_keys)
        required_fields = {
            'signals': {'code', 'signal_date', 'close', 'schema_version',
                        'experimental_version' if kind == 'experimental' else 'strategy_version'},
            'history': {'session_offset', 'close', 'return_abs'},
            'controls': {'signal_date', 'initial_close', 'control_type',
                         'experimental_signal_id' if kind == 'experimental' else 'signal_id'},
            'control_history': {'session_offset', 'close', 'return_abs'},
            'research_events': {'event_date', 'data_origin', 'evaluation_phase', 'research_version'},
            'validation_subjects': {'anchor_date', 'anchor_price', 'data_origin', 'evaluation_phase', 'research_version'},
            'research_control_members': {'validation_subject_id', 'anchor_date', 'anchor_price', 'feature_hash'},
            'research_control_history': {'validation_subject_id', 'session_offset', 'close', 'return_abs'},
            'research_subject_history': {'session_offset', 'close', 'return_abs'},
            'hypotheses': {'payload', 'definition_hash', 'freeze_status', 'frozen_at'},
            'families': {'payload', 'definition_hash', 'frozen_at'},
            'research_telemetry': {'run_date', 'payload', 'payload_json'},
            'research_checkpoint_evaluations': {'payload_json', 'result'},
            'research_results': {'payload_json', 'final_decision'},
            'research_family_results': {'payload_json', 'family_status'},
        }[table]
        if any(not required_fields <= set(row) for row in rows):
            raise StateError(f'{table}: incomplete row schema')
        for row in rows:
            for key in ('date', 'signal_date', 'event_date', 'anchor_date'):
                if row.get(key):
                    try:
                        day = date.fromisoformat(row[key])
                    except (ValueError, TypeError) as exc:
                        raise StateError(f'{table}: invalid date') from exc
                    dates.append(day.isoformat())
            versions.update(f'{k}={v}' for k, v in row.items() if 'version' in k and isinstance(v, str))
            if table in {'hypotheses', 'families'}:
                payload = row.get('payload')
                if not isinstance(payload, dict) or any(payload.get(k) != row[k] for k in identity_keys):
                    raise StateError(f'{table}: registry payload mismatch')
        tables[table] = {
            'count': len(rows),
            'ids_sha256': sha256(json.dumps(sorted(ids), separators=(',', ':')).encode()),
            'data_origin': dict(Counter(r.get('data_origin') for r in rows if r.get('data_origin'))),
            'evaluation_phase': dict(Counter(r.get('evaluation_phase') for r in rows if r.get('evaluation_phase'))),
        }
    # Detect orphan histories/members before Database's INSERT OR IGNORE can hide a loss.
    if kind != 'research':
        signal_key = 'experimental_signal_id' if kind == 'experimental' else 'signal_id'
        signals = {r[signal_key] for r in value['signals']}
        members = identities(value['controls'], keys['controls'])
        if any(r.get(signal_key) not in signals for t in ('history', 'controls') for r in value[t]):
            raise StateError('orphan signal reference')
        if any((r['control_group_id'], r['control_code']) not in members for r in value['control_history']):
            raise StateError('orphan control history')
    else:
        subjects = {r['validation_subject_id'] for r in value['validation_subjects']}
        members = identities(value['research_control_members'], keys['research_control_members'])
        if any(r.get('validation_subject_id') not in subjects for t in (
                'research_subject_history', 'research_control_members', 'research_control_history') for r in value[t]):
            raise StateError('orphan research subject')
        if any((r['control_group_id'], r['control_code']) not in members for r in value['research_control_history']):
            raise StateError('orphan research control')
    return {'kind': kind, 'bytes': len(raw), 'sha256': sha256(raw),
            'latest_record_date': max(dates, default=None), 'versions': sorted(versions), 'tables': tables}


def decode(raw, kind, *, compressed=False, expected=None):
    try:
        plain = gzip.decompress(raw) if compressed else raw
    except (OSError, EOFError, zlib.error) as exc:
        raise StateError('invalid gzip; no legacy fallback permitted') from exc
    actual = describe(plain, kind)
    if expected is not None and actual != expected:
        raise StateError('state checksum/schema/count/ID/generation mismatch')
    return parse(plain)


def _get(url, *, optional=False):
    try:
        response = requests.get(url, timeout=(15, 180))
        if optional and response.status_code == 404:
            return None
        response.raise_for_status()
        # requests detects incomplete Content-Length/chunked responses while reading content.
        return response.content
    except requests.RequestException as exc:
        raise StateError(f'seed transport failed: {url}') from exc


def load_remote(base_url, kind):
    base = base_url.rstrip('/')
    packed = _get(base + '/state.json.gz', optional=True)
    manifest_raw = _get(base + '/state.manifest.json', optional=True)
    expected = parse(manifest_raw) if manifest_raw is not None else None
    if packed is not None:
        if expected is None:
            raise StateError('compressed seed requires state.manifest.json')
        value = decode(packed, kind, compressed=True, expected=expected)
        if not value['hypotheses' if kind == 'research' else 'signals']:
            raise StateError('configured seed is empty; initialization requires no seed URL')
        legacy = _get(base + '/state.json', optional=True)
        if legacy is not None:
            if describe(legacy, kind) != expected:
                raise StateError('mixed state generations; no legacy fallback permitted')
            warnings.warn('identical legacy and compressed states coexist', RuntimeWarning)
        return value
    if expected is not None:
        raise StateError('manifest exists but compressed seed is missing')
    index_before = _get(base + '/index.json')
    legacy = _get(base + '/state.json')
    index_after = _get(base + '/index.json')
    if index_before != index_after:
        raise StateError('seed generation changed during download; retry entire snapshot')
    value = decode(legacy, kind)
    index = parse(index_before)
    count_keys = ({'event_count': 'research_events', 'validation_subject_count': 'validation_subjects',
                   'hypothesis_count': 'hypotheses'} if kind == 'research' else
                  {'signal_count': 'signals', 'history_count': 'history', 'control_count': 'controls'})
    if not isinstance(index, dict) or any(index.get(key) != len(value[table]) for key, table in count_keys.items()):
        raise StateError('legacy index/count mismatch or incomplete state')
    if not value['hypotheses' if kind == 'research' else 'signals']:
        raise StateError('configured seed is empty; initialization requires no seed URL')
    return value


def guard_cached_state(db, value, kind):
    """Legacy has no signed generation authority. Cache provides a conservative floor.

    A cache miss cannot prove freshness; pin a verified manifest in Phase 2.
    """
    from .research.storage import ResearchStore
    if kind == 'validation':
        s, h = db.validation_rows()
        c, ch = db.control_validation_rows()
        old = dict(zip(_COMMON, (s, h, c, ch)))
    elif kind == 'experimental':
        old = dict(zip(_COMMON, db.experimental_rows()))
    else:
        store = ResearchStore(db)
        old = {key: store.rows({'hypotheses': 'research_hypotheses',
                                'families': 'research_families'}.get(key, key)) for key in RESEARCH_KEYS}
    for table, keys in keys_for(kind).items():
        if not identities(old[table], keys) <= identities(value[table], keys):
            raise StateError(f'{kind}/{table}: missing cached IDs or rollback; explicit recovery required')


def import_seed(db, value, kind):
    """Import the verified generation atomically, including all continuity tables."""
    from contextlib import contextmanager
    from .database import Database
    from .research.storage import ResearchStore

    # Schema initialization uses executescript (which commits); do it before BEGIN.
    if kind == 'research':
        ResearchStore(db)
    guard_cached_state(db, value, kind)
    with db.connect() as connection:
        class SeedDatabase(Database):
            def __init__(self):
                self.path = db.path

            @contextmanager
            def connect(self):
                factory = connection.row_factory
                try:
                    yield connection
                finally:
                    connection.row_factory = factory

        target = SeedDatabase()
        connection.execute('BEGIN IMMEDIATE')
        try:
            if kind == 'research':
                # Avoid running ResearchStore.__init__ inside the transaction.
                store = ResearchStore.__new__(ResearchStore)
                store.db = target
                store.import_state(value)
            elif kind == 'validation':
                target.import_validation_rows(*(value[k] for k in _COMMON))
            else:
                target.import_experimental_rows(*(value[k] for k in _COMMON))
        except Exception:
            connection.rollback()
            raise
