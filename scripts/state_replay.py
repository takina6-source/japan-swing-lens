"""Same-input baseline/compressed replay. Uses a pinned local Pages snapshot only.

Run this script against both the unchanged base checkout and the proposed checkout.
The output hashes retain full numeric precision; only generated/created timestamps
are removed. No network calls or publishing code runs.
"""
from __future__ import annotations

import argparse
import gc
import gzip
import hashlib
import json
import statistics
import tempfile
from pathlib import Path
from unittest.mock import patch

from engine.config import load_config
from engine.database import Database
from engine.validation import export_validation, seed_validation
from engine.experimental.validation import export_experimental, seed_experimental
from engine.research.pipeline import export_research, seed_research


def normalize(value):
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items() if k not in {'created_at', 'generated_at'}}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(normalize(value), ensure_ascii=False,
                                     sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pages', type=Path, required=True)
    parser.add_argument('--compressed', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = {'engine_module_sha256': hashlib.sha256(Path(__import__('engine.validation', fromlist=['x']).__file__).read_bytes()).hexdigest(), 'mode': 'gzip' if args.compressed else 'legacy', 'artifacts': {}, 'segment': {}}
    cfg = load_config()
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        db = Database(output / 'replay.db')
        for kind, seed in [('validation', seed_validation), ('experimental', seed_experimental),
                           ('research', seed_research)]:
            raw = (args.pages / kind / 'state.json').read_bytes()
            value = json.loads(raw)
            packed = gzip.compress(raw, mtime=0) if args.compressed else None
            if args.compressed:
                from engine.state_io import compress, describe
                packed = compress(raw)
                expected = json.dumps(describe(raw, kind)).encode()
            class Response:
                def __init__(self, content=None):
                    self.status_code = 404 if content is None else 200
                    self.content = content
                def raise_for_status(self):
                    if self.status_code != 200: raise RuntimeError('unexpected missing fixture')
                def json(self): return json.loads(self.content)
            def get(url, **kwargs):
                name = url.rsplit('/', 1)[-1]
                if name == 'state.json.gz': return Response(packed)
                if name == 'state.manifest.json': return Response(expected if args.compressed else None)
                if name == 'state.json': return Response(None if args.compressed else raw)
                if name == 'index.json': return Response((args.pages / kind / 'index.json').read_bytes())
                raise AssertionError(url)
            with patch('requests.get', get):
                assert seed(db, 'https://fixture/' + kind)
            del value, raw, packed
            gc.collect()
        # Export through unchanged production calculations after both import routes.
        export_validation(db, output / 'validation', cfg)
        export_experimental(db, output / 'experimental', cfg)
        export_research(db, output / 'research', cfg)
        for kind in ('validation', 'experimental', 'research'):
            for path in sorted((output / kind).glob('*.json')):
                value = json.loads(path.read_bytes())
                if path.name == 'state.json':
                    # Row ordering is not meaningful for DB tables. Keep every field/value.
                    value = {k: sorted(v, key=lambda r: json.dumps(normalize(r), sort_keys=True))
                             if isinstance(v, list) else v for k, v in value.items()}
                report['artifacts'][f'{kind}/{path.name}'] = digest(value)
        performance = json.loads((output / 'validation/performance.json').read_bytes())
        for state in ('ALL', 'BREAKOUT WATCH', 'BREAKOUT'):
            rows = [r for r in performance if r.get('initial_aligned_count') == 5
                    and (r.get('momentum_percentile') or 0) >= 95
                    and r.get('return_5d_pct') is not None
                    and r.get('strategy_version') == cfg['strategy_version']
                    and r.get('threshold_version') == cfg['threshold_version']
                    and r.get('schema_version') == cfg['schema_version']
                    and (state == 'ALL' or r['initial_consensus_state'] == state)]
            matched = [r['excess_vs_matched_5d_pct'] for r in rows if r.get('excess_vs_matched_5d_pct') is not None]
            report['segment'][state] = {'n': len(rows), 't5_mean_pct': statistics.fmean(r['return_5d_pct'] for r in rows) if rows else None,
                                        'matched_n': len(matched), 'matched_excess_pct': statistics.fmean(matched) if matched else None,
                                        'ids_sha256': digest(sorted(r['signal_id'] for r in rows))}
        research = json.loads((output / 'research/state.json').read_bytes())
        report['research'] = {
            'hypotheses': {r['hypothesis_version']: r['definition_hash'] for r in research['hypotheses']},
            'live_forward_events': sum(r.get('data_origin') == 'LIVE_FORWARD' for r in research['research_events']),
            'formal_holdout_events': sum(r.get('data_origin') == 'LIVE_FORWARD' and r.get('evaluation_phase') == 'HOLDOUT' for r in research['research_events']),
            'live_forward_subjects': sum(r.get('data_origin') == 'LIVE_FORWARD' for r in research['validation_subjects']),
            'formal_holdout_subjects': sum(r.get('data_origin') == 'LIVE_FORWARD' and r.get('evaluation_phase') == 'HOLDOUT' for r in research['validation_subjects']),
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
