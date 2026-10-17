"""Build a verified compressed publication tree. Does not deploy anything."""
import argparse
import json
from pathlib import Path

from engine.state_generation import finalize, json_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--authority', type=Path, required=True)
    parser.add_argument('--history', type=Path, required=True)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--ledger-output', type=Path, required=True)
    args = parser.parse_args()
    authority = json.loads(args.authority.read_bytes())
    snapshot = json.loads((args.source/'data/snapshot.json').read_bytes())
    sessions = json.loads((args.source/'data/market-sessions.json').read_bytes())
    ledger, metrics = finalize(args.source, args.target, run_id=args.run_id, commit=args.commit,
                               market_date=snapshot['as_of'], previous=authority.get('generation'),
                               history=json.loads(args.history.read_bytes()), sessions=sessions,
                               floor=json.loads(args.history.with_name('continuity-floor.json').read_bytes()))
    args.ledger_output.write_bytes(json_bytes(ledger))
    print(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    main()
