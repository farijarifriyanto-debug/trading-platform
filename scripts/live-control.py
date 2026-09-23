#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from trading_platform.live import LiveStateStore, LiveTradingConfig


CONFIRMATION = "ENABLE_REAL_MONEY_ORDER_WINDOW"


def config_fingerprint(config: LiveTradingConfig) -> str:
    payload = {
        "enabled": config.enabled,
        "exchange": config.exchange_id,
        "market_type": config.market_type,
        "allowed_symbols": list(config.allowed_symbols),
        "max_order_notional": config.max_order_notional,
        "max_position_notional": config.max_position_notional,
        "max_daily_notional": config.max_daily_notional,
        "one_shot_arm": config.one_shot_arm,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def require_fresh_preflight(data_root: Path, config: LiveTradingConfig) -> dict:
    path = data_root / "live-preflight.json"
    if not path.is_file():
        raise SystemExit("REFUSED: fresh private read-only preflight attestation is required")
    try:
        attestation = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("REFUSED: live preflight attestation is unreadable") from exc
    max_age = int(os.getenv("TRADING_LIVE_PREFLIGHT_MAX_AGE_SECONDS", "300"))
    if max_age < 30 or max_age > 900:
        raise SystemExit("REFUSED: TRADING_LIVE_PREFLIGHT_MAX_AGE_SECONDS must be 30..900")
    age = int(time.time()) - int(attestation.get("verified_at_epoch", 0))
    if age < 0 or age > max_age:
        raise SystemExit("REFUSED: live preflight attestation is stale")
    if attestation.get("config_fingerprint") != config_fingerprint(config):
        raise SystemExit("REFUSED: live configuration changed after preflight")
    if attestation.get("position_mode") != "one_way":
        raise SystemExit("REFUSED: preflight did not verify one-way position mode")
    return attestation


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
    data_root = Path(args.data_root)
    state = LiveStateStore(data_root / "live.sqlite3")

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
    require_fresh_preflight(data_root, config)
    ttl = args.ttl if args.ttl is not None else config.arm_ttl_seconds
    if ttl < 30 or ttl > min(3600, config.arm_ttl_seconds):
        raise SystemExit(
            f"REFUSED: ttl must be 30..{min(3600, config.arm_ttl_seconds)} seconds"
        )
    print(json.dumps(state.arm(ttl)))


if __name__ == "__main__":
    main()
