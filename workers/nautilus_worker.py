#!/usr/bin/env python3
import argparse
import json
from decimal import Decimal
from pathlib import Path


def _timeframe(value: str) -> str:
    import re
    match = re.fullmatch(r"(\d+)([mhd])", value)
    if not match:
        raise ValueError(f"unsupported timeframe: {value}")
    step, unit = match.groups()
    names = {"m": "MINUTE", "h": "HOUR", "d": "DAY"}
    return f"{int(step)}-{names[unit]}"


def run(job: dict) -> dict:
    import nautilus_trader
    from nautilus_trader.backtest import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig
    from nautilus_trader.model import (
        AccountType, Bar, BarType, BookType, Currency, CurrencyPair,
        InstrumentId, Money, OmsType, OrderSide, Price, Quantity,
        Symbol, TraderId, Venue,
    )
    from nautilus_trader.trading import Strategy

    candles = job["candles"]
    if not candles:
        raise ValueError("dataset has no candles")
    symbol = job["symbol"]
    if "/" not in symbol:
        raise ValueError("Nautilus worker currently requires BASE/QUOTE symbols")
    base, quote = symbol.split("/", 1)
    venue = Venue("SIM")
    quote_currency = Currency.from_str(quote)
    instrument = CurrencyPair(
        instrument_id=InstrumentId(Symbol(symbol), venue),
        raw_symbol=Symbol(symbol),
        base_currency=Currency.from_str(base),
        quote_currency=quote_currency,
        price_precision=8,
        size_precision=8,
        price_increment=Price.from_str("0.00000001"),
        size_increment=Quantity.from_str("0.00000001"),
        ts_event=0,
        ts_init=0,
        max_quantity=Quantity.from_str("100000000.00000000"),
        min_quantity=Quantity.from_str("0.00000001"),
        min_notional=Money(0.01, quote_currency),
        max_price=Price.from_str("1000000000000"),
        min_price=Price.from_str("0.00000001"),
        margin_init=Decimal("0.10"),
        margin_maint=Decimal("0.05"),
    )
    bar_type = BarType.from_str(
        f"{symbol}.SIM-{_timeframe(job['timeframe'])}-LAST-EXTERNAL"
    )
    fast = int(job["parameters"]["fast"])
    slow = int(job["parameters"]["slow"])
    if fast <= 0 or slow <= fast:
        raise ValueError("require 0 < fast < slow")
    initial_cash = float(job.get("initial_cash", 100_000.0))
    quantity = job.get("quantity")
    if quantity is None:
        quantity = max(0.00000001, initial_cash * 0.01 / float(candles[0]["close"]))
    trade_size = Decimal(str(quantity))

    class LongOnlySMA(Strategy):
        def __new__(cls, *_args, **_kwargs):
            return super().__new__(cls)

        def __init__(self):
            super().__init__()
            self.prices = []

        def on_start(self):
            self.subscribe_bars(bar_type)

        def on_bar(self, bar):
            self.prices.append(float(str(bar.close)))
            if len(self.prices) < slow:
                return
            fast_avg = sum(self.prices[-fast:]) / fast
            slow_avg = sum(self.prices[-slow:]) / slow
            if fast_avg > slow_avg and self.portfolio.is_net_flat(instrument.id):
                self.submit_order(
                    self.order_factory.market(
                        instrument.id,
                        OrderSide.BUY,
                        instrument.make_qty(trade_size),
                    )
                )
            elif fast_avg < slow_avg and self.portfolio.is_net_long(instrument.id):
                self.close_all_positions(instrument.id)

        def on_stop(self):
            if self.portfolio.is_net_long(instrument.id):
                self.close_all_positions(instrument.id)

    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId.from_str("BACKTESTER-001"),
            run_analysis=False,
        )
    )
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type=BookType.L1_MBP,
        base_currency=quote_currency,
        starting_balances=[Money(initial_cash, quote_currency)],
        bar_execution=True,
    )
    engine.add_instrument(instrument)

    bars = []
    for candle in candles:
        timestamp_ns = int(candle["timestamp"]) * 1_000_000
        bars.append(
            Bar(
                bar_type=bar_type,
                open=instrument.make_price(Decimal(str(candle["open"]))),
                high=instrument.make_price(Decimal(str(candle["high"]))),
                low=instrument.make_price(Decimal(str(candle["low"]))),
                close=instrument.make_price(Decimal(str(candle["close"]))),
                volume=instrument.make_qty(
                    max(Decimal("0.00000001"), Decimal(str(candle["volume"])))
                ),
                ts_event=timestamp_ns,
                ts_init=timestamp_ns,
            )
        )

    engine.add_data(bars)
    engine.add_strategy(LongOnlySMA())
    engine.run()
    fills = engine.generate_order_fills_report()
    account = engine.generate_account_report(venue)
    end_equity = initial_cash
    if not account.empty and "total" in account:
        end_equity = float(str(account.iloc[-1]["total"]))
    result = {
        "engine": "nautilus",
        "engine_version": nautilus_trader.__version__,
        "dataset_id": job["dataset_id"],
        "symbol": symbol,
        "timeframe": job["timeframe"],
        "strategy": "sma_trend",
        "parameters": {"fast": fast, "slow": slow},
        "quantity": float(trade_size),
        "start_cash": initial_cash,
        "end_equity": end_equity,
        "return_fraction": (end_equity - initial_cash) / initial_cash,
        "fills": int(len(fills)),
        "closed_positions": int(engine.cache.positions_closed_count()),
        "open_positions": int(engine.cache.positions_open_count()),
        "live_mode": False,
    }
    engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    result = run(job)
    Path(args.result).write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
