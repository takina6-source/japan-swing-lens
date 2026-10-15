"""Private capture/replay of fixed provider inputs; no publishing or cache saves."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k,v in value.items() if k not in {'created_at','generated_at'}}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def exporter():
    # The same wrapper calls either the old engine or proposed engine on sys.path.
    spec = importlib.util.spec_from_file_location('fixed_input_exporter', Path(__file__).with_name('export_web.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture(args):
    from engine.database import Database
    from engine.data.yahoo import YahooProvider
    from engine.performance_repair import action_candidates
    from engine.state_generation import json_bytes
    module = exporter()
    db = Database(args.db)
    names,frames,fundamentals,sources,meta = module.cached_scan(db,'主要500+Growth')
    if not frames:
        raise RuntimeError('fixed-run price cache is missing; cannot substitute demo inputs')
    yahoo = YahooProvider()
    candidates = action_candidates(db,frames)
    actions = {}
    if candidates:
        fresh,errors = yahoo.histories(sorted(candidates),period='2y')
        if errors or set(fresh) != set(candidates):
            raise RuntimeError('split-basis capture failed; cannot silently substitute')
        frames.update(fresh)
        for code in sorted(candidates):
            actions[code] = [[str(day),float(ratio)] for day,ratio in yahoo.stock_splits(code)]
    benchmark = yahoo.history('TOPIX')  # Fail on transport error; no demo fallback.
    if benchmark is None or benchmark.empty:
        raise RuntimeError('benchmark capture failed')
    def frame_value(frame):
        return {'index':[str(d) for d in frame.index], 'columns':list(frame.columns),
                'data': [[float(v) if math.isfinite(float(v)) else None for v in row]
                         for row in frame.itertuples(index=False,name=None)]}
    value = {'names':names,'frames':{k:frame_value(v) for k,v in frames.items()},
             'fundamentals':fundamentals,'sources':sources,'meta':meta,
             'benchmark':frame_value(benchmark),'actions':actions,
             'benchmark_source':'1306 TOPIX連動ETF（Yahoo Finance）'}
    raw = json_bytes(value)
    args.output.write_bytes(gzip.compress(raw,mtime=0))
    args.output.with_suffix('.sha256').write_text(hashlib.sha256(args.output.read_bytes()).hexdigest())
    print(json.dumps({'captured_stocks':len(frames),'input_sha256':hashlib.sha256(raw).hexdigest(),
                      'latest_prices':max(str(f.index[-1].date()) for f in frames.values()),
                      'benchmark_latest':str(benchmark.index[-1].date()),'corporate_action_candidates':len(candidates)}))


def inputs(path):
    import pandas as pd
    value = json.loads(gzip.decompress(path.read_bytes()))
    def frame(item):
        return pd.DataFrame(item['data'],columns=item['columns'],index=pd.to_datetime(item['index']))
    value['frames'] = {k:frame(v) for k,v in value['frames'].items()}
    value['benchmark'] = frame(value['benchmark'])
    return value


def worker(args):
    from engine.config import load_config
    from engine.database import Database
    from engine.validation import seed_validation
    from engine.experimental.validation import seed_experimental
    from engine.research.pipeline import seed_research
    from scripts.state_replay import digest as result_digest
    module = exporter()
    args.work.mkdir(parents=True,exist_ok=False)
    db_path = args.work/'replay.sqlite'
    shutil.copy2(args.db,db_path)
    fixed = inputs(args.inputs)
    source = args.pages
    ledger = None
    reports = []
    if args.mode == 'gzip':
        from engine.state_generation import finalize
        ledger,_ = finalize(source,args.work/'initial',run_id=100,commit='a'*40,
                             market_date=json.loads((source/'data/snapshot.json').read_bytes())['as_of'])
        source = args.work/'initial'
    for iteration in (1,2):
        db = Database(db_path)
        if args.mode == 'legacy':
            for kind,seed in [('validation',seed_validation),('experimental',seed_experimental),('research',seed_research)]:
                value = json.loads((source/kind/'state.json').read_bytes())
                response = Mock(status_code=200)
                response.json.return_value = value
                with patch('requests.get',return_value=response):
                    if not seed(db,'https://fixed/'+kind):
                        raise RuntimeError('baseline seed failed')
        else:
            from engine.state_generation import restore_generation
            def get(url,**kwargs):
                path = source/url.removeprefix('https://fixed/')
                response = Mock(status_code=200 if path.exists() else 404)
                response.content = path.read_bytes() if path.exists() else None
                response.raise_for_status.side_effect = None if path.exists() else __import__('requests').HTTPError('404')
                return response
            with patch('requests.get',get):
                restore_generation(db,'https://fixed',{'kind':'compressed','generation':ledger},args.work/f'history-{iteration}.json')
        output = args.work/f'cycle-{iteration}'
        # Preserve the same static/history inputs for the display adapters.
        shutil.copytree(args.pages,output)
        # Candidate output starts as legacy; remove prior transport files copied from a template.
        for kind in ('validation','experimental','research'):
            for name in ('state.json.gz','state.manifest.json'):
                (output/kind/name).unlink(missing_ok=True)
        def no_network(*unused,**kwargs):
            raise AssertionError('network access during fixed-input replay')
        with patch('requests.sessions.Session.request',no_network), patch('socket.create_connection',no_network):
            module.export(False,'主要500+Growth',db_path=db_path,dashboard=output,captured_input=fixed)
        # Telemetry includes process times/SQLite size and is not a research result.
        excluded = {'research/storage_metrics.json','research/performance_metrics.json',
                    'research/state.json'}
        artifacts = {}
        for directory in ('data','validation','experimental','research'):
            for path in sorted((output/directory).rglob('*.json')):
                name = str(path.relative_to(output))
                if name in excluded or name.endswith('/state.manifest.json'):
                    continue
                value = json.loads(path.read_bytes())
                if name.endswith('/state.json'):
                    value = {k:sorted(v,key=lambda r:json.dumps(canonical(r),sort_keys=True))
                             if isinstance(v,list) else v for k,v in value.items()}
                artifacts[name] = result_digest(value)
        research = json.loads((output/'research/state.json').read_bytes())
        research.pop('research_telemetry')
        artifacts['research/state-except-telemetry'] = digest({k: sorted(canonical(v),key=lambda r:json.dumps(r,sort_keys=True))
                                                            if isinstance(v,list) else v for k,v in research.items()})
        performance = json.loads((output/'validation/performance.json').read_bytes())
        cfg=load_config()
        rows=[r for r in performance if r.get('initial_aligned_count')==5 and (r.get('momentum_percentile') or 0)>=95
              and r.get('return_5d_pct') is not None and all(r.get(k)==cfg[k] for k in ('strategy_version','threshold_version','schema_version'))]
        reports.append({'iteration':iteration,'artifacts':artifacts,'segment':canonical(rows)})
        if args.mode == 'gzip':
            from engine.state_generation import finalize
            history=json.loads((source/'storage/history.json').read_bytes())
            ledger,physical=finalize(output,args.work/f'compressed-{iteration}',run_id=100+iteration,commit='a'*40,
                                     market_date=json.loads((output/'data/snapshot.json').read_bytes())['as_of'],
                                     previous=ledger,history=history,
                                     sessions=json.loads((output/'data/market-sessions.json').read_bytes()))
            source=args.work/f'compressed-{iteration}'
            reports[-1]['physical']=physical
        else:
            source=output
    args.report.write_text(json.dumps({'mode':args.mode,'cycles':reports},ensure_ascii=False,indent=2))
    print('Two fixed-input full pipeline continuations completed:',args.mode)


def run(args):
    args.work.mkdir(parents=True,exist_ok=False)
    script=Path(__file__).resolve()
    results={}
    for mode,engine_root in [('legacy',args.baseline),('gzip',script.parents[1])]:
        report=args.work/f'{mode}.json'
        env={**os.environ,'PYTHONPATH':str(engine_root.resolve())}
        for key in ('STATE_AUTHORITY_FILE','PHASE2A_INPUT_DIR','VALIDATION_SEED_URL','EXPERIMENTAL_SEED_URL','RESEARCH_SEED_URL'):
            env.pop(key,None)
        subprocess.run([sys.executable,str(script),'worker','--mode',mode,'--db',str(args.db.resolve()),
                        '--inputs',str(args.inputs.resolve()),'--pages',str(args.pages.resolve()),
                        '--work',str((args.work/mode).resolve()),'--report',str(report.resolve())],env=env,check=True)
        results[mode]=json.loads(report.read_text())
        # Free the ~GB of working outputs before running the second mode on a hosted runner.
        shutil.rmtree(args.work/mode)
    for before,after in zip(results['legacy']['cycles'],results['gzip']['cycles']):
        if before['artifacts'] != after['artifacts'] or before['segment'] != after['segment']:
            raise RuntimeError('same-input research or display output mismatch; production cutover must stop')
    args.report.write_text(json.dumps({'passed':True,'cycles':2,
                           'artifact_counts':[len(c['artifacts']) for c in results['gzip']['cycles']],
                           'input_archive_sha256':hashlib.sha256(args.inputs.read_bytes()).hexdigest(),
                           'excluded_volatile':['created_at','generated_at','research operational telemetry'],
                           'results':results},ensure_ascii=False,indent=2))
    print('Baseline vs compressed: both full pipeline continuations match without numeric rounding')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capture');c.add_argument('--db',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
    for name in ('worker','run'):
        p=sub.add_parser(name)
        for arg in ('db','inputs','pages','work','report'):
            p.add_argument('--'+arg,type=Path,required=True)
        if name=='worker': p.add_argument('--mode',choices=['legacy','gzip'],required=True)
        else: p.add_argument('--baseline',type=Path,required=True)
    args=parser.parse_args()
    {'capture':capture,'worker':worker,'run':run}[args.command](args)


if __name__=='__main__': main()
