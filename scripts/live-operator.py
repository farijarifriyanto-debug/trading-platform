#!/usr/bin/env python3
"""Host-local operator CLI for the fail-closed live trading boundary.

This tool never schedules or autonomously chooses trades. A real-money order
requires an explicit command plus the exact confirmation phrase.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from trading_platform.domain import Side
from trading_platform.live import CCXTLiveBroker, LiveStateStore, LiveTradingConfig

REAL_MONEY_CONFIRMATION = "EXECUTE_REAL_MONEY"
ARM_CONFIRMATION = "ENABLE_REAL_MONEY_ORDER_WINDOW"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _api_url(path: str) -> str:
    base = os.getenv("TRADING_LIVE_API_URL", "http://127.0.0.1:48070").rstrip("/")
    return f"{base}{path}"


def _api_key() -> str:
    key = os.getenv("TRADING_API_KEY", "")
    if len(key) < 32:
        raise SystemExit("REFUSED: TRADING_API_KEY is missing or too short")
    return key


def api_json(path: str, method: str = "GET", payload: dict | None = None) -> dict | list:
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        _api_url(path),
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"API {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"API unavailable: {exc.reason}") from exc


def run_python_script(name: str, *args: str) -> None:
    command = [sys.executable, str(_repo_root() / "scripts" / name), *args]
    subprocess.run(command, check=True)


def broker_and_symbol(symbol: str | None) -> tuple[LiveTradingConfig, CCXTLiveBroker, str]:
    config = LiveTradingConfig.from_env()
    if not config.enabled:
        raise SystemExit("REFUSED: live capability is disabled")
    selected = symbol or config.allowed_symbols[0]
    if selected not in config.allowed_symbols:
        raise SystemExit("REFUSED: symbol is not allowlisted")
    broker = CCXTLiveBroker(config.exchange_id, config.market_type)
    return config, broker, selected


def preview(side: Side, quantity: float, symbol: str | None) -> dict:
    config, broker, selected = broker_and_symbol(symbol)
    if broker.position_mode_hedged():
        raise SystemExit("REFUSED: one-way position mode is required")
    normalized = broker.normalize_quantity(selected, quantity)
    quote = broker.quote(selected)
    price = quote.executable_price(side)
    notional = price * normalized
    minimum_cost = broker.minimum_cost(selected)
    position = broker.position_quantity(selected)
    result = {
        "mutation": False,
        "exchange": config.exchange_id,
        "market_type": config.market_type,
        "symbol": selected,
        "side": side.value,
        "requested_quantity": quantity,
        "normalized_quantity": normalized,
        "reference_price": price,
        "estimated_notional": notional,
        "exchange_minimum_cost": minimum_cost,
        "current_position": position,
        "max_order_notional": config.max_order_notional,
        "max_position_notional": config.max_position_notional,
        "max_daily_notional": config.max_daily_notional,
    }
    if side == Side.BUY:
        result["available_quote_balance"] = broker.available_quote_balance(selected)
    else:
        result["available_base_balance"] = broker.available_base_balance(selected)
        if broker.is_contract(selected):
            result["reduce_only"] = True
    return result


def command_status(_: argparse.Namespace) -> None:
    print(json.dumps(api_json("/live/status"), indent=2, sort_keys=True))


def command_preflight(_: argparse.Namespace) -> None:
    run_python_script("live-preflight.py", "--private-read-check")


def command_preview(args: argparse.Namespace) -> None:
    print(json.dumps(preview(Side(args.side), args.quantity, args.symbol), indent=2, sort_keys=True))


def command_disarm(_: argparse.Namespace) -> None:
    run_python_script("live-control.py", "disarm")
    try:
        print(json.dumps(api_json("/live/status"), indent=2, sort_keys=True))
    except SystemExit:
        pass


def command_reconcile(_: argparse.Namespace) -> None:
    print(json.dumps(api_json("/live/reconcile", method="POST", payload={}), indent=2, sort_keys=True))


def command_emergency_stop(_: argparse.Namespace) -> None:
    print(json.dumps(api_json("/live/emergency-stop", method="POST", payload={}), indent=2, sort_keys=True))


def command_order(args: argparse.Namespace) -> None:
    if args.confirm != REAL_MONEY_CONFIRMATION:
        raise SystemExit(
            f"REFUSED: --confirm must equal {REAL_MONEY_CONFIRMATION}"
        )
    side = Side(args.side)
    details = preview(side, args.quantity, args.symbol)
    if details["estimated_notional"] > details["max_order_notional"]:
        raise SystemExit("REFUSED: preview exceeds configured max order notional")
    minimum_cost = details["exchange_minimum_cost"]
    if minimum_cost is not None and details["estimated_notional"] < minimum_cost:
        raise SystemExit("REFUSED: preview is below exchange minimum cost")

    print(json.dumps({"real_money_preview": details}, indent=2, sort_keys=True))
    run_python_script("live-preflight.py", "--private-read-check")
    run_python_script(
        "live-control.py",
        "arm",
        "--ttl",
        str(args.ttl),
        "--confirm",
        ARM_CONFIRMATION,
    )

    request_id = args.request_id or f"operator-{side.value}-{int(time.time())}"
    payload = {
        "request_id": request_id,
        "symbol": details["symbol"],
        "side": side.value,
        "quantity": args.quantity,
    }
    try:
        result = api_json("/live/orders", method="POST", payload=payload)
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        # Fail closed even when validation/network/exchange errors occur. If an
        # exchange outcome is ambiguous, the durable request id must be
        # reconciled rather than retried.
        run_python_script("live-control.py", "disarm")
    print(f"REQUEST_ID={request_id}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fail-closed live trading operator CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status").set_defaults(func=command_status)
    sub.add_parser("preflight").set_defaults(func=command_preflight)
    sub.add_parser("disarm").set_defaults(func=command_disarm)
    sub.add_parser("reconcile").set_defaults(func=command_reconcile)
    sub.add_parser("emergency-stop").set_defaults(func=command_emergency_stop)

    for name, func in (("preview", command_preview), ("order", command_order)):
        q = sub.add_parser(name)
        q.add_argument("--side", choices=("buy", "sell"), required=True)
        q.add_argument("--quantity", type=float, required=True)
        q.add_argument("--symbol", default=None)
        if name == "order":
            q.add_argument("--ttl", type=int, default=60)
            q.add_argument("--request-id", default=None)
            q.add_argument("--confirm", required=True)
        q.set_defaults(func=func)
    return p


def main() -> None:
    args = parser().parse_args()
    if getattr(args, "quantity", 1) <= 0:
        raise SystemExit("REFUSED: quantity must be positive")
    args.func(args)


if __name__ == "__main__":
    main()
