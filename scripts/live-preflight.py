#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from trading_platform.live import CCXTLiveBroker, LiveStateStore, LiveTradingConfig
from trading_platform.security import SecurityConfig


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


def main():
    parser = argparse.ArgumentParser(
        description="Read-only Phase 8 live execution preflight. This script never creates orders."
    )
    parser.add_argument("--private-read-check", action="store_true")
    args = parser.parse_args()

    config = LiveTradingConfig.from_env()
    security = SecurityConfig.from_env()
    data_root = Path(os.getenv("TRADING_DATA_DIR", "data"))
    state = LiveStateStore(data_root / "live.sqlite3")
    control = state.status()

    checks = {
        "capability_enabled": config.enabled,
        "exchange": config.exchange_id,
        "market_type": config.market_type,
        "allowed_symbols": list(config.allowed_symbols),
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
        "position_mode": "not_checked",
        "security_posture": "not_checked",
        "symbols_verified": [],
        "symbols_with_positions": [],
    }

    if args.private_read_check:
        if not config.enabled:
            raise SystemExit("REFUSED: enable live capability before private credential preflight")
        broker = CCXTLiveBroker(config.exchange_id, config.market_type)
        broker.exchange.load_markets()
        balance = broker.exchange.fetch_balance()
        hedged = broker.position_mode_hedged()
        checks["position_mode"] = "hedged" if hedged else "one_way"
        posture = broker.private_security_posture()
        if posture is not None:
            checks["security_posture"] = posture
        if hedged:
            raise SystemExit("REFUSED: hedged position mode is not supported")
        for symbol in config.allowed_symbols:
            broker.exchange.market(symbol)
            broker.quote(symbol)
            position = broker.position_quantity(symbol)
            if abs(position) > 1e-12:
                checks["symbols_with_positions"].append(symbol)
            broker.available_quote_balance(symbol)
            broker.reconciliation_orders(symbol)
            checks["symbols_verified"].append(symbol)
        checks["private_read_check"] = "pass"
        checks["balance_currency_count"] = len(balance.get("total", {}))

    required = [
        checks["capability_enabled"],
        checks["auth_required"],
        checks["auth_key_minimum_length"],
        checks["exchange_credentials_present"],
        checks["one_shot_arm"],
        checks["disarmed"],
        checks["live_state_ok"],
    ]
    if args.private_read_check:
        required.extend(
            [
                checks["private_read_check"] == "pass",
                checks["position_mode"] == "one_way",
                set(checks["symbols_verified"]) == set(config.allowed_symbols),
            ]
        )
        if config.exchange_id == "binance":
            posture = checks["security_posture"]
            required.extend(
                [
                    isinstance(posture, dict),
                    bool(posture.get("reading")) if isinstance(posture, dict) else False,
                    not bool(posture.get("withdrawals")) if isinstance(posture, dict) else False,
                    bool(posture.get("ip_restricted")) if isinstance(posture, dict) else False,
                    (
                        bool(posture.get("futures"))
                        if isinstance(posture, dict) and config.market_type == "future"
                        else True
                    ),
                ]
            )
    checks["ready_for_manual_arm"] = all(required)

    if args.private_read_check and checks["ready_for_manual_arm"]:
        data_root.mkdir(parents=True, exist_ok=True)
        attestation = {
            "verified_at_epoch": int(time.time()),
            "config_fingerprint": config_fingerprint(config),
            "exchange": config.exchange_id,
            "market_type": config.market_type,
            "allowed_symbols": list(config.allowed_symbols),
            "position_mode": checks["position_mode"],
        }
        path = data_root / "live-preflight.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(attestation, sort_keys=True, separators=(",", ":")) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        checks["attestation_written"] = True

    print(json.dumps(checks, sort_keys=True))
    if not checks["ready_for_manual_arm"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
