#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

from trading_platform.live import LiveStateStore, LiveTradingConfig


CONFIRMATION = "ENABLE_REAL_MONEY_ORDER_WINDOW"


def main():
    parser = argparse.ArgumentParser(
        description="Host-local live execution arm/disarm control. There is intentionally no HTTP arm endpoint."
    )
    parser.add_argument(
        "--data-root",
        default=os.getenv("TRADING_DATA_DIR", "data"),
        help="Trading data directory containing live.sqlite3",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("disarm")
    arm = sub.add_parser("arm")
    arm.add_argument("--ttl", type=int, default=None)
    arm.add_argument("--confirm", required=True)

    args = parser.parse_args()
    config = LiveTradingConfig.from_env()
    state = LiveStateStore(Path(args.data_root) / "live.sqlite3")

    if args.command == "status":
        print(json.dumps({"capability_enabled": config.enabled, **state.status()}))
        return

    if args.command == "disarm":
        print(json.dumps(state.disarm()))
        return

    if not config.enabled:
        raise SystemExit("REFUSED: TRADING_LIVE_ENABLED is not enabled")
    if args.confirm != CONFIRMATION:
        raise SystemExit(f"REFUSED: --confirm must equal {CONFIRMATION}")
    ttl = args.ttl if args.ttl is not None else config.arm_ttl_seconds
    if ttl < 30 or ttl > min(3600, config.arm_ttl_seconds):
        raise SystemExit(
            f"REFUSED: ttl must be 30..{min(3600, config.arm_ttl_seconds)} seconds"
        )
    print(json.dumps(state.arm(ttl)))


if __name__ == "__main__":
    main()
