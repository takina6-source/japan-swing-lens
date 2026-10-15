"""Offline backup/verification. Never publishes or modifies production state."""
import argparse
import json
from pathlib import Path

from engine.state_backup import create_backup, verify_backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('--pages', type=Path, required=True)
    create.add_argument('--archive', type=Path, required=True)
    create.add_argument('--generation', required=True)
    create.add_argument('--market-date', required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('--archive', type=Path, required=True)
    restore = sub.add_parser('restore')
    restore.add_argument('--archive', type=Path, required=True)
    restore.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'create':
        result = create_backup(args.pages, args.archive, generation=args.generation, market_date=args.market_date)
    else:
        result = verify_backup(args.archive, getattr(args, 'target', None))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
