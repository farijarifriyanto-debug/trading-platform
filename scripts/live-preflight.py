#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

from trading_platform.live import CCXTLiveBroker, LiveStateStore, LiveTradingConfig
from trading_platform.security import SecurityConfig


def main():
    parser = argparse.ArgumentParser(
        description="Read-only Phase 8 live execution preflight. This script never creates orders."
    )
    parser.add_argument("--private-read-check", action="store_true")
    args = parser.parse_args()

    config = LiveTradingConfig.from_env()
    security = SecurityConfig.from_env()
    state = LiveStateStore(Path(os.getenv("TRADING_DATA_DIR", "data")) / "live.sqlite3")
    control = state.status()

    checks = {
        "capability_enabled": config.enabled,
        "auth_required": security.require_auth,
        "auth_key_minimum_length": len(os.getenv("TRADING_API_KEY", "")) >= 32,
        "exchange_credentials_present": bool(
            os.getenv("TRADING_LIVE_EXCHANGE_API_KEY")
            and os.getenv("TRADING_LIVE_EXCHANGE_SECRET")
        ),
        "one_shot_arm": config.one_shot_arm,
        "disarmed": not control["armed"] and control["kill_switch"],
        "live_state_ok": state.health()["ok"],
        "private_read_check": "not_requested",
    }

    if args.private_read_check:
        if not config.enabled:
            raise SystemExit("REFUSED: enable live capability before private credential preflight")
        broker = CCXTLiveBroker(config.exchange_id)
        broker.exchange.load_markets()
        balance = broker.exchange.fetch_balance()
        broker.reconciliation_orders()
        checks["private_read_check"] = "pass"
        checks["balance_currency_count"] = len(balance.get("total", {}))

    required = (
        checks["capability_enabled"],
        checks["auth_required"],
        checks["auth_key_minimum_length"],
        checks["exchange_credentials_present"],
        checks["one_shot_arm"],
        checks["disarmed"],
        checks["live_state_ok"],
    )
    checks["ready_for_manual_arm"] = all(required)
    print(json.dumps(checks, sort_keys=True))

    if not checks["ready_for_manual_arm"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
