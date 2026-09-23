import json

import pytest

from trading_platform.datasets import (
    DatasetIntegrityError,
    HistoricalDataService,
    HistoricalDatasetStore,
)
from trading_platform.market import Candle
from trading_platform.registry import default_strategy_registry
from trading_platform.sweep import run_sma_parameter_sweep


class CountingMarket:
    exchange_id = "fake"

    def __init__(self, closes):
        self.closes = closes
        self.calls = 0

    def quote(self, symbol):
        raise AssertionError("quote not used")

    def candles(self, symbol, timeframe="1h", limit=200):
        self.calls += 1
        return [
            Candle(
                timestamp=index,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=1.0,
            )
            for index, price in enumerate(self.closes[-limit:], start=1)
        ]


def test_dataset_id_is_content_addressed_and_stable(tmp_path):
    store = HistoricalDatasetStore(tmp_path)
    candles = [
        Candle(1, 1.0, 1.1, 0.9, 1.0, 10.0),
        Candle(2, 2.0, 2.1, 1.9, 2.0, 11.0),
    ]

    first = store.save("fake", "BTC/USD", "1h", candles)
    second = store.save("fake", "BTC/USD", "1h", candles)

    assert first.dataset_id == second.dataset_id
    assert len(first.dataset_id) == 64
    assert first.count == 2
    assert store.load(first.dataset_id).candles == tuple(candles)


def test_dataset_integrity_detects_tampering(tmp_path):
    store = HistoricalDatasetStore(tmp_path)
    dataset = store.save(
        "fake",
        "BTC/USD",
        "1h",
        [Candle(1, 1.0, 1.0, 1.0, 1.0, 1.0)],
    )
    path = tmp_path / "datasets" / f"{dataset.dataset_id}.json"
    record = json.loads(path.read_text())
    record["payload"]["candles"][0]["close"] = 999.0
    path.write_text(json.dumps(record))

    with pytest.raises(DatasetIntegrityError, match="hash mismatch"):
        store.load(dataset.dataset_id)


def test_historical_service_uses_cache_until_refresh(tmp_path):
    store = HistoricalDatasetStore(tmp_path)
    service = HistoricalDataService(store)
    market = CountingMarket([1, 2, 3, 4, 5])

    first, first_hit = service.snapshot(market, "BTC/USD", "1h", 5)
    second, second_hit = service.snapshot(market, "BTC/USD", "1h", 5)
    third, third_hit = service.snapshot(market, "BTC/USD", "1h", 5, refresh=True)

    assert first_hit is False
    assert second_hit is True
    assert third_hit is False
    assert market.calls == 2
    assert first.dataset_id == second.dataset_id == third.dataset_id


def test_strategy_registry_exposes_and_evaluates_sma():
    registry = default_strategy_registry()

    catalog = registry.catalog()
    signal = registry.evaluate(
        "sma_trend",
        [1, 1, 1, 2, 3],
        {"fast": 2, "slow": 5},
    )

    assert catalog[0]["name"] == "sma_trend"
    assert signal.value == "buy"


def test_sma_parameter_sweep_is_deterministic_and_skips_invalid_pairs():
    prices = [10, 10, 9, 8, 7, 8, 9, 10, 11, 12]

    first = run_sma_parameter_sweep(
        prices,
        fast_values=[2, 3, 3],
        slow_values=[4, 8, 2, 99],
        quantity=1,
    )
    second = run_sma_parameter_sweep(
        prices,
        fast_values=[2, 3, 3],
        slow_values=[4, 8, 2, 99],
        quantity=1,
    )

    assert first == second
    assert first.evaluated == 4
    assert first.skipped == 4
    assert len(first.results) == 4
    assert list(first.results) == sorted(
        first.results,
        key=lambda item: (-item.end_equity, item.fast, item.slow),
    )


def test_invalid_dataset_id_cannot_escape_store(tmp_path):
    store = HistoricalDatasetStore(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        store.load("../../etc/passwd")
