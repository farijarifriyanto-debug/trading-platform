import os
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .audit import JSONLAuditLog
from .backtest import run_sma_backtest
from .domain import OrderIntent, Side
from .execution import PaperBroker
from .market import CCXTMarketData
from .paper import PaperTradingService
from .research import VectorBTResearch, VectorBTUnavailable
from .risk import RiskRejected

app = FastAPI(title="Trading Platform", version="0.2.0")
paper = PaperBroker()
audit = JSONLAuditLog(os.getenv("TRADING_AUDIT_PATH", "data/paper-audit.jsonl"))
paper_service = PaperTradingService(paper, audit)


class PaperOrder(BaseModel):
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)


class MarketPaperOrder(BaseModel):
    exchange: str = "kraken"
    symbol: str = "BTC/USD"
    side: Side
    quantity: float = Field(gt=0)


class StrategyStepRequest(BaseModel):
    exchange: str = "kraken"
    symbol: str = "BTC/USD"
    quantity: float = Field(default=0.001, gt=0)
    timeframe: str = "1h"
    limit: int = Field(default=200, ge=20, le=1000)
    fast: int = Field(default=5, gt=0)
    slow: int = Field(default=20, gt=1)


class BacktestRequest(BaseModel):
    prices: list[float]
    symbol: str = "TEST/USD"
    quantity: float = Field(default=1.0, gt=0)
    fast: int = Field(default=5, gt=0)
    slow: int = Field(default=20, gt=1)


class VectorBTRequest(BaseModel):
    prices: list[float]
    fast: int = Field(default=10, gt=0)
    slow: int = Field(default=50, gt=1)
    initial_cash: float = Field(default=100_000.0, gt=0)
    fees: float = Field(default=0.0005, ge=0)


def _market(exchange: str) -> CCXTMarketData:
    try:
        return CCXTMarketData(exchange)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.exception_handler(RiskRejected)
async def risk_rejected_handler(_, exc: RiskRejected):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok", "mode": "paper", "live_trading": False, "version": "0.2.0"}


@app.get("/market/{exchange}/ticker")
def market_ticker(exchange: str, symbol: str = Query(...)):
    try:
        return asdict(_market(exchange).quote(symbol))
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=502, detail=f"market data unavailable: {exc}") from exc


@app.get("/market/{exchange}/ohlcv")
def market_ohlcv(
    exchange: str,
    symbol: str = Query(...),
    timeframe: str = "1h",
    limit: int = Query(200, ge=1, le=1000),
):
    try:
        return [asdict(candle) for candle in _market(exchange).candles(symbol, timeframe, limit)]
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=502, detail=f"market data unavailable: {exc}") from exc


@app.post("/paper/orders")
def paper_order(req: PaperOrder):
    fill = paper.submit(OrderIntent(req.symbol, req.side, req.quantity, req.price))
    audit.record(
        "paper_fill",
        {
            "exchange": None,
            "source": "manual_price",
            "fill": asdict(fill),
        },
    )
    return {"fill": asdict(fill), "cash": paper.portfolio.cash, "positions": paper.portfolio.positions}


@app.post("/paper/market-orders")
def paper_market_order(req: MarketPaperOrder):
    try:
        fill = paper_service.market_order(_market(req.exchange), req.symbol, req.side, req.quantity)
        return {"fill": asdict(fill), "cash": paper.portfolio.cash, "positions": paper.portfolio.positions}
    except Exception as exc:
        if isinstance(exc, (HTTPException, RiskRejected)):
            raise
        raise HTTPException(status_code=502, detail=f"paper market order failed: {exc}") from exc


@app.post("/paper/strategy/sma/step")
def paper_sma_step(req: StrategyStepRequest):
    try:
        return asdict(
            paper_service.sma_step(
                _market(req.exchange),
                req.symbol,
                req.quantity,
                req.timeframe,
                req.limit,
                req.fast,
                req.slow,
            )
        )
    except Exception as exc:
        if isinstance(exc, (HTTPException, RiskRejected)):
            raise
        raise HTTPException(status_code=502, detail=f"strategy step failed: {exc}") from exc


@app.get("/paper/portfolio")
def paper_portfolio():
    return {"cash": paper.portfolio.cash, "positions": paper.portfolio.positions}


@app.get("/paper/audit")
def paper_audit(limit: int = Query(100, ge=1, le=1000)):
    return audit.tail(limit)


@app.post("/backtests/sma")
def backtest(req: BacktestRequest):
    return asdict(run_sma_backtest(req.prices, req.symbol, req.quantity, req.fast, req.slow))


@app.post("/research/vectorbt/sma")
def vectorbt_sma(req: VectorBTRequest):
    try:
        result = VectorBTResearch().sma_cross(req.prices, req.fast, req.slow, req.initial_cash, req.fees)
        return asdict(result)
    except VectorBTUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
