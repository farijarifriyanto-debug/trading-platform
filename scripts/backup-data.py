#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

from trading_platform.backup import create_backup, restore_backup, verify_backup


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("archive")
    create.add_argument("--data-root", default=os.getenv("TRADING_DATA_DIR", "data"))

    verify = sub.add_parser("verify")
    verify.add_argument("archive")

    restore = sub.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("destination")

    args = parser.parse_args()
    if args.command == "create":
        path = create_backup(args.data_root, args.archive)
        print(json.dumps({"created": str(path), **verify_backup(path)}))
    elif args.command == "verify":
        print(json.dumps(verify_backup(args.archive)))
    else:
        path = restore_backup(args.archive, args.destination)
        print(json.dumps({"restored": str(path), **verify_backup(args.archive)}))


if __name__ == "__main__":
    main()
