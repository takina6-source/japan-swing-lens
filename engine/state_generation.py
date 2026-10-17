"""Three-stream generation verification and staging, with no deployment capability."""
from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

from .state_io import StateError, _get, compress, decode, describe, identities, import_seed, keys_for, parse, sha256
from .state_backup import KINDS
from .storage_metrics import metrics, tree_bytes

FORMAT = 'jsl-state-generation-v1'


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read_plain(root: Path, kind: str):
    path = root / kind
    packed, legacy = path / 'state.json.gz', path / 'state.json'
    if packed.exists():
        expected = parse((path / 'state.manifest.json').read_bytes())
        raw = gzip.decompress(packed.read_bytes())
        decode(raw, kind, expected=expected)
        if legacy.exists() and legacy.read_bytes() != raw:
            raise StateError('mixed state generations')
        return raw
    if (path / 'state.manifest.json').exists():
        raise StateError('manifest without compressed state')
    raw = legacy.read_bytes()
    describe(raw, kind)
    return raw


def validate_generation(value):
    if not isinstance(value, dict) or value.get('format') != FORMAT:
        raise StateError('unsupported generation ledger')
    if set(value.get('streams', {})) != set(KINDS):
        raise StateError('generation must include exactly three streams')
    if not isinstance(value.get('run_id'), int) or value['run_id'] <= 0:
        raise StateError('invalid producer run')
    if value.get('generation_id') != f"gh-{value['run_id']}":
        raise StateError('invalid generation ID')
    date.fromisoformat(value['market_date'])
    if len(value.get('producer_commit', '')) != 40:
        raise StateError('invalid producer commit')
    if len(value.get('storage_history_sha256', '')) != 64:
        raise StateError('missing storage history checksum')
    return value



# These primary keys are append-only under the existing research implementation.
# Observation history is intentionally omitted: existing matured-history compaction
# is not changed by this transport migration.
IMMUTABLE = {
    'validation': ('signals', 'controls'),
    'experimental': ('signals', 'controls'),
    'research': ('hypotheses', 'families', 'research_events', 'validation_subjects',
                 'research_control_members', 'research_checkpoint_evaluations',
                 'research_results', 'research_family_results'),
}


def continuity_floor(values):
    result = {}
    for kind in KINDS:
        value = values[kind]
        tables = {table: sorted(identities(value[table], keys_for(kind)[table]))
                  for table in IMMUTABLE[kind]}
        result[kind] = {'ids': tables}
        if kind == 'research':
            result[kind]['registry'] = {table: {json.dumps([row[k] for k in keys_for(kind)[table]]): row['payload']
                                                for row in value[table]} for table in ('hypotheses', 'families')}
            result[kind]['phases'] = {table: {json.dumps([row[k] for k in keys_for(kind)[table]]):
                                               {k: row.get(k) for k in ('data_origin','evaluation_phase','research_version')}
                                             for row in value[table]}
                                      for table in ('research_events','validation_subjects')}
    return result


def check_continuity_floor(value, kind, floor):
    for table, expected in floor[kind]['ids'].items():
        if not {tuple(row) for row in expected} <= identities(value[table], keys_for(kind)[table]):
            raise StateError(f'{kind}/{table}: immutable IDs disappeared during update')
    if kind == 'research':
        for table, expected in floor[kind]['registry'].items():
            actual = {json.dumps([row[k] for k in keys_for(kind)[table]]): row['payload'] for row in value[table]}
            if any(actual.get(key) != payload for key,payload in expected.items()):
                raise StateError('frozen research definition changed during update')
        for table,expected in floor[kind]['phases'].items():
            actual = {json.dumps([row[k] for k in keys_for(kind)[table]]):
                      {k: row.get(k) for k in ('data_origin','evaluation_phase','research_version')}
                      for row in value[table]}
            if any(actual.get(key) != labels for key,labels in expected.items()):
                raise StateError('LIVE_FORWARD/HOLDOUT or version labels changed during update')


def restore_generation(db, root_url: str, authority: dict, history_path: Path):
    """An independent Release/approved bootstrap fixes all three streams before import."""
    if authority.get('kind') == 'bootstrap':
        expected = authority['streams']
        actual_ledger = _get(root_url.rstrip('/') + '/state-generation.json', optional=True)
        if actual_ledger is not None:
            raise StateError('bootstrap cannot replace an existing generation')
        values = {}
        for kind in KINDS:
            # Legacy is permitted only against the independently verified raw checksum.
            if _get(f'{root_url}/{kind}/state.json.gz', optional=True) is not None:
                raise StateError('unexpected compressed bootstrap state')
            raw = _get(f'{root_url}/{kind}/state.json')
            values[kind] = decode(raw, kind, expected=expected[kind])
        history = []
    else:
        expected_ledger = validate_generation(authority['generation'])
        actual = _get(root_url.rstrip('/') + '/state-generation.json')
        if parse(actual) != expected_ledger:
            raise StateError('Pages differs from latest independent generation; rollback or pending deployment')
        values = {}
        for kind, info in expected_ledger['streams'].items():
            packed = _get(f'{root_url}/{kind}/state.json.gz')
            manifest = parse(_get(f'{root_url}/{kind}/state.manifest.json'))
            if manifest != info or sha256(packed) != expected_ledger['compressed_sha256'][kind]:
                raise StateError('mixed generation or compressed checksum mismatch')
            values[kind] = decode(packed, kind, compressed=True, expected=info)
            old = _get(f'{root_url}/{kind}/state.json', optional=True)
            if old is not None:
                raise StateError('legacy State must not coexist in compressed publication')
        history_raw = _get(root_url.rstrip('/') + '/storage/history.json')
        if sha256(history_raw) != expected_ledger['storage_history_sha256']:
            raise StateError('storage history checksum mismatch')
        history = parse(history_raw)
        # Re-read the ledger to detect a deployment during the three downloads.
        if parse(_get(root_url.rstrip('/') + '/state-generation.json')) != expected_ledger:
            raise StateError('generation changed during restore')
    # No stream is imported until all three transports and checksums succeed.
    for kind in KINDS:
        import_seed(db, values[kind], kind)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_bytes(json_bytes(history))
    history_path.with_name('continuity-floor.json').write_bytes(json_bytes(continuity_floor(values)))


def finalize(source: Path, target: Path, *, run_id: int, commit: str, market_date: str,
             previous: dict | None = None, history: list | None = None,
             sessions: list[str] | None = None, floor: dict | None = None):
    """Build and verify a complete replacement tree. Never touch the source tree."""
    if target.exists() or source.resolve() == target.resolve() or source.resolve() in target.resolve().parents:
        raise StateError('staging target must be new and separate from the source')
    date.fromisoformat(market_date)
    if previous:
        validate_generation(previous)
        if run_id <= previous['run_id'] or market_date < previous['market_date']:
            raise StateError('generation rollback')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staged = Path(temp) / 'site'
        if any(p.is_symlink() for p in source.rglob('*')):
            raise StateError('symlinks are not permitted in the staged publication')
        shutil.copytree(source, staged)
        from .bulk_downloads import stage_downloads
        bulk_manifest_sha = stage_downloads(staged)
        ledger = {'format': FORMAT, 'generation_id': f'gh-{run_id}', 'run_id': run_id,
                  'producer_commit': commit, 'market_date': market_date,
                  'parent_sha256': sha256(json_bytes(previous)) if previous else None,
                  'bulk_downloads_sha256': bulk_manifest_sha,
                  'display_files': [name for name in ('briefing/latest.json','briefing/what-changed.json','committee/index.json',
                                                     'validation/signal_history.json','experimental/history.json')
                                    if (source/name).is_file()],
                  'streams': {}, 'compressed_sha256': {}, 'storage_history_sha256': '0' * 64}
        for kind in KINDS:
            raw = read_plain(source, kind)
            if floor is not None:
                check_continuity_floor(parse(raw), kind, floor)
            packed = compress(raw)
            info = describe(raw, kind)
            if info['latest_record_date'] and info['latest_record_date'] > market_date:
                raise StateError('State observations are newer than the claimed market date')
            decode(packed, kind, compressed=True, expected=info)
            if gzip.decompress(packed) != raw:
                raise StateError('lossless compression failed')
            directory = staged / kind
            (directory / 'state.json.gz').write_bytes(packed)
            (directory / 'state.manifest.json').write_bytes(json_bytes(info))
            # Only remove the verified duplicate from the unpublished staging directory.
            (directory / 'state.json').unlink(missing_ok=True)
            ledger['streams'][kind] = info
            ledger['compressed_sha256'][kind] = sha256(packed)
        validate_generation(ledger)
        point = {'market_date': market_date, 'generation_id': ledger['generation_id'],
                 'format': 'gzip-bulk-v1', 'pages_bytes': 0,
                 'logical_state_bytes': sum(i['bytes'] for i in ledger['streams'].values()),
                 'compressed_state_bytes': sum((staged/k/'state.json.gz').stat().st_size for k in KINDS)}
        prior = list(history or [])
        if prior and (prior[-1]['market_date'] > market_date):
            raise StateError('storage history would roll back')
        # Same-market-day reruns replace the point, never pretend to be a new trading day.
        prior = [p for p in prior if p['market_date'] != market_date]
        points = prior + [point]
        calendar = sorted(set(sessions or [p['market_date'] for p in points]))
        (staged/'storage').mkdir(exist_ok=True)
        legacy_metrics = parse((source/'research/storage_metrics.json').read_bytes())
        # Exact accounting includes history, ledger and the metrics file's own size.
        for _ in range(30):
            history_raw = json_bytes(points)
            ledger['storage_history_sha256'] = sha256(history_raw)
            (staged/'storage/history.json').write_bytes(history_raw)
            (staged/'state-generation.json').write_bytes(json_bytes(ledger))
            page_metrics = metrics(points, calendar)
            previous_run = history[-1] if history else None
            page_metrics['previous_run_delta_bytes'] = (
                point['pages_bytes'] - previous_run['pages_bytes']
                if previous_run and previous_run['format'] == point['format'] else None)
            metric_bytes = json_bytes({
                **legacy_metrics, 'research_budget_status': legacy_metrics.get('research_budget_status', legacy_metrics.get('budget_status')),
                'budget_status': page_metrics['budget_status'], 'pages': page_metrics})
            # Reserve a fixed 16 KiB for the self-reporting metrics file. JSON
            # permits trailing whitespace; size no longer oscillates with float
            # representations or projected runway while counting its own bytes.
            if len(metric_bytes) > 16384:
                raise StateError('storage metrics exceeded reserved accounting slot')
            (staged/'research/storage_metrics.json').write_bytes(metric_bytes + b' ' * (16384-len(metric_bytes)))
            size = tree_bytes(staged)
            if size == point['pages_bytes']:
                break
            point['pages_bytes'] = size
        else:
            raise StateError('physical byte accounting did not converge')
        if point['pages_bytes'] >= page_metrics['limit_bytes']:
            raise StateError('publication exceeds Pages capacity; retain source and backups')
        # No partial publication directory is exposed on any preceding error.
        os.rename(staged, target)
    return ledger, page_metrics
