import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .market import Candle, MarketData

_DATASET_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class DatasetIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalDataset:
    dataset_id: str
    exchange: str
    symbol: str
    timeframe: str
    candles: tuple[Candle, ...]

    @property
    def count(self) -> int:
        return len(self.candles)

    @property
    def first_timestamp(self) -> int | None:
        return self.candles[0].timestamp if self.candles else None

    @property
    def last_timestamp(self) -> int | None:
        return self.candles[-1].timestamp if self.candles else None

    def metadata(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "count": self.count,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
        }


def _canonical_payload(
    exchange: str,
    symbol: str,
    timeframe: str,
    candles: list[Candle] | tuple[Candle, ...],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [asdict(candle) for candle in candles],
    }


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class HistoricalDatasetStore:
    """Content-addressed immutable market-data snapshots with cache refs."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.datasets_dir = self.root / "datasets"
        self.refs_dir = self.root / "refs"

    def save(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        candles: list[Candle] | tuple[Candle, ...],
    ) -> HistoricalDataset:
        payload = _canonical_payload(exchange, symbol, timeframe, candles)
        dataset_id = _digest(payload)
        record = {"dataset_id": dataset_id, "payload": payload}
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        path = self.datasets_dir / f"{dataset_id}.json"
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_text(_canonical_json(record) + "\n", encoding="utf-8")
            tmp.replace(path)
        return self.load(dataset_id)

    def load(self, dataset_id: str) -> HistoricalDataset:
        if not _DATASET_ID_RE.fullmatch(dataset_id):
            raise ValueError("dataset_id must be a 64-character lowercase SHA-256")
        path = self.datasets_dir / f"{dataset_id}.json"
        if not path.exists():
            raise FileNotFoundError(dataset_id)
        record = json.loads(path.read_text(encoding="utf-8"))
        payload = record.get("payload")
        if not isinstance(payload, dict) or record.get("dataset_id") != dataset_id:
            raise DatasetIntegrityError("invalid dataset record")
        if _digest(payload) != dataset_id:
            raise DatasetIntegrityError("dataset content hash mismatch")
        if payload.get("schema_version") != 1:
            raise DatasetIntegrityError("unsupported dataset schema")
        candles = tuple(Candle(**item) for item in payload.get("candles", []))
        return HistoricalDataset(
            dataset_id=dataset_id,
            exchange=str(payload["exchange"]),
            symbol=str(payload["symbol"]),
            timeframe=str(payload["timeframe"]),
            candles=candles,
        )

    def list_metadata(self) -> list[dict[str, Any]]:
        if not self.datasets_dir.exists():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(self.datasets_dir.glob("*.json")):
            try:
                items.append(self.load(path.stem).metadata())
            except (ValueError, FileNotFoundError, DatasetIntegrityError, KeyError, TypeError):
                continue
        return items

    @staticmethod
    def request_key(exchange: str, symbol: str, timeframe: str, limit: int) -> str:
        request = {
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": int(limit),
        }
        return hashlib.sha256(_canonical_json(request).encode("utf-8")).hexdigest()

    def set_cache_ref(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int,
        dataset_id: str,
    ) -> None:
        self.load(dataset_id)
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        key = self.request_key(exchange, symbol, timeframe, limit)
        path = self.refs_dir / f"{key}.json"
        path.write_text(
            _canonical_json(
                {
                    "exchange": exchange,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "limit": int(limit),
                    "dataset_id": dataset_id,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def get_cache_ref(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> HistoricalDataset | None:
        key = self.request_key(exchange, symbol, timeframe, limit)
        path = self.refs_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            ref = json.loads(path.read_text(encoding="utf-8"))
            return self.load(str(ref["dataset_id"]))
        except (ValueError, FileNotFoundError, DatasetIntegrityError, KeyError, TypeError):
            return None


class HistoricalDataService:
    def __init__(self, store: HistoricalDatasetStore):
        self.store = store

    def snapshot(
        self,
        market: MarketData,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 200,
        refresh: bool = False,
    ) -> tuple[HistoricalDataset, bool]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not refresh:
            cached = self.store.get_cache_ref(market.exchange_id, symbol, timeframe, limit)
            if cached is not None:
                return cached, True

        candles = market.candles(symbol, timeframe=timeframe, limit=limit)
        dataset = self.store.save(market.exchange_id, symbol, timeframe, candles)
        self.store.set_cache_ref(market.exchange_id, symbol, timeframe, limit, dataset.dataset_id)
        return dataset, False
