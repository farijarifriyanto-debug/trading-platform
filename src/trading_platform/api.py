from fastapi import FastAPI
from pydantic import BaseModel, Field

from .backtest import run_sma_backtest
from .domain import OrderIntent, Side
from .execution import PaperBroker

app = FastAPI(title="Trading Platform", version="0.1.0")
paper = PaperBroker()


class PaperOrder(BaseModel):
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)


class BacktestRequest(BaseModel):
    prices: list[float]
    symbol: str = "TEST/USD"
    quantity: float = Field(default=1.0, gt=0)
    fast: int = Field(default=5, gt=0)
    slow: int = Field(default=20, gt=1)


@app.get("/health")
def health():
    return {"status": "ok", "mode": "paper", "live_trading": False}


@app.post("/paper/orders")
def paper_order(req: PaperOrder):
    fill = paper.submit(OrderIntent(req.symbol, req.side, req.quantity, req.price))
    return {"fill": fill.__dict__, "cash": paper.portfolio.cash, "positions": paper.portfolio.positions}


@app.post("/backtests/sma")
def backtest(req: BacktestRequest):
    return run_sma_backtest(req.prices, req.symbol, req.quantity, req.fast, req.slow).__dict__
