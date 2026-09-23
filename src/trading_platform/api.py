import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .ai_research import AICandidateSpec, AICandidateStore, AIResearchWorkerService, RobustnessPolicy
from .audit import JSONLAuditLog
from .backtest import run_sma_backtest
from .datasets import (
    DatasetIntegrityError,
    HistoricalDataService,
    HistoricalDatasetStore,
)
from .dashboard import dashboard_response
from .domain import OrderIntent, Side
from .execution import PaperBroker
from .experiments import ExperimentSpec, ExperimentStore
from .finrlx import FinRLXResearchService
from .lean_export import LeanExporter
from .market import CCXTMarketData
from .metrics import collect_metrics
from .paper import PaperTradingService
from .registry import default_strategy_registry
from .research import VectorBTResearch, VectorBTUnavailable
from .risk import RiskRejected
from .simulation import SimulationPlanStore, SimulationSpec, default_simulation_registry
from .sweep import run_sma_parameter_sweep
from .workers import SimulationWorkerService, WorkerFailed, WorkerUnavailable

app = FastAPI(title="Trading Platform", version="0.6.0")

data_root = Path(os.getenv("TRADING_DATA_DIR", "data"))
paper = PaperBroker()
audit = JSONLAuditLog(os.getenv("TRADING_AUDIT_PATH", str(data_root / "paper-audit.jsonl")))
strategies = default_strategy_registry()
paper_service = PaperTradingService(paper, audit, strategies)
dataset_store = HistoricalDatasetStore(data_root / "historical")
historical_data = HistoricalDataService(dataset_store)
simulations = default_simulation_registry()
simulation_plans = SimulationPlanStore(data_root / "simulations")
experiment_store = ExperimentStore(data_root / "experiments")
worker_service = SimulationWorkerService(experiment_store)
lean_exporter = LeanExporter(data_root / "lean-exports")
ai_candidate_store = AICandidateStore(data_root / "ai-candidates")
ai_worker = AIResearchWorkerService(ai_candidate_store, data_root / "models")
finrlx_service = FinRLXResearchService()


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
    parameters: dict[str, Any] = Field(default_factory=dict)


class SMAFastSlowRequest(StrategyStepRequest):
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


class DatasetCaptureRequest(BaseModel):
    exchange: str = "kraken"
    symbol: str = "BTC/USD"
    timeframe: str = "1h"
    limit: int = Field(default=200, ge=1, le=1000)
    refresh: bool = False


class SMASweepRequest(BaseModel):
    dataset_id: str | None = None
    prices: list[float] | None = None
    symbol: str = "TEST/USD"
    quantity: float = Field(default=1.0, gt=0)
    fast_values: list[int] = Field(default_factory=lambda: [3, 5, 10], min_length=1)
    slow_values: list[int] = Field(default_factory=lambda: [20, 50], min_length=1)


class SimulationPlanRequest(BaseModel):
    dataset_id: str
    strategy: str = "sma_trend"
    parameters: dict[str, Any] = Field(default_factory=lambda: {"fast": 5, "slow": 20})
    initial_cash: float = Field(default=100_000.0, gt=0)
    base_currency: str = Field(default="USD", min_length=3, max_length=12)


class ExperimentRequest(BaseModel):
    dataset_id: str
    engine: str = "native"
    strategy: str = "sma_trend"
    parameters: dict[str, Any] = Field(default_factory=lambda: {"fast": 5, "slow": 20})
    initial_cash: float = Field(default=100_000.0, gt=0)
    quantity: float | None = Field(default=None, gt=0)


class LeanExportRequest(BaseModel):
    dataset_id: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"fast": 5, "slow": 20})
    initial_cash: float = Field(default=100_000.0, gt=0)


class RobustnessRequest(BaseModel):
    min_samples: int = Field(default=100, ge=30)
    min_folds: int = Field(default=3, ge=2)
    min_mean_accuracy: float = Field(default=0.52, ge=0, le=1)
    min_accuracy_uplift: float = Field(default=0.0, ge=-1, le=1)
    max_accuracy_std: float = Field(default=0.15, ge=0, le=1)


class AICandidateRequest(BaseModel):
    dataset_id: str
    model_family: str = "random_forest_direction"
    seed: int = 42
    folds: int = Field(default=3, ge=2, le=20)
    initial_train_fraction: float = Field(default=0.5, gt=0.1, lt=0.9)
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    robustness: RobustnessRequest = Field(default_factory=RobustnessRequest)


def _market(exchange: str) -> CCXTMarketData:
    try:
        return CCXTMarketData(exchange)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _load_dataset(dataset_id: str):
    try:
        return dataset_store.load(dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="dataset not found") from exc
    except DatasetIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.exception_handler(RiskRejected)
async def risk_rejected_handler(_, exc: RiskRejected):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": "paper",
        "live_trading": False,
        "version": "0.6.0",
    }


@app.get("/strategies")
def strategy_catalog():
    return strategies.catalog()


@app.get("/", include_in_schema=False)
def dashboard():
    return dashboard_response()


@app.get("/metrics")
def platform_metrics():
    return asdict(collect_metrics(dataset_store, paper.portfolio, audit, experiment_store, ai_candidate_store))


@app.get("/simulations/engines")
def simulation_engines():
    return simulations.catalog()


@app.get("/workers/health")
def workers_health():
    return worker_service.health()


@app.get("/ai/health")
def ai_research_health():
    return ai_worker.health()


@app.get("/ai/finrlx/health")
def finrlx_health():
    return finrlx_service.health()


@app.post("/ai/candidates/{candidate_id}/finrlx")
def run_finrlx_candidate(candidate_id: str):
    try:
        record = ai_candidate_store.require(candidate_id)
        dataset = _load_dataset(record.spec.dataset_id)
        finrlx_result = finrlx_service.run(record, dataset)
        merged = dict(record.result or {})
        merged["finrlx_backtest"] = finrlx_result
        updated = ai_candidate_store.update(
            record.candidate_id,
            record.status,
            gate_status=record.gate_status,
            gate_reasons=record.gate_reasons,
            result=merged,
            error=record.error,
        )
        return asdict(updated)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="AI candidate not found") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



@app.get("/ai/candidates")
def list_ai_candidates():
    return [asdict(record) for record in ai_candidate_store.records()]


@app.post("/ai/candidates")
def create_ai_candidate(req: AICandidateRequest):
    _load_dataset(req.dataset_id)
    policy = RobustnessPolicy(**req.robustness.model_dump())
    record = ai_candidate_store.create(
        AICandidateSpec(
            dataset_id=req.dataset_id,
            model_family=req.model_family,
            seed=req.seed,
            folds=req.folds,
            initial_train_fraction=req.initial_train_fraction,
            hyperparameters=req.hyperparameters,
            robustness=policy,
        )
    )
    return asdict(record)


@app.get("/ai/candidates/{candidate_id}")
def get_ai_candidate(candidate_id: str):
    try:
        return asdict(ai_candidate_store.require(candidate_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="AI candidate not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/ai/candidates/{candidate_id}/run")
def run_ai_candidate(candidate_id: str):
    try:
        record = ai_candidate_store.require(candidate_id)
        dataset = _load_dataset(record.spec.dataset_id)
        return asdict(ai_worker.run(record, dataset))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="AI candidate not found") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc



@app.get("/experiments")
def list_experiments():
    return [asdict(record) for record in experiment_store.records()]


@app.get("/experiments/compare")
def compare_experiments(dataset_id: str | None = None):
    return experiment_store.compare(dataset_id)


@app.post("/experiments")
def create_experiment(req: ExperimentRequest):
    _load_dataset(req.dataset_id)
    if req.engine not in {"native", "nautilus", "lean", "vectorbt"}:
        raise HTTPException(status_code=422, detail="unsupported experiment engine")
    record = experiment_store.create(
        ExperimentSpec(
            dataset_id=req.dataset_id,
            engine=req.engine,
            strategy=req.strategy,
            parameters=req.parameters,
            initial_cash=req.initial_cash,
            quantity=req.quantity,
        )
    )
    return asdict(record)


@app.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str):
    try:
        record = experiment_store.require(experiment_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return asdict(record)


@app.post("/experiments/{experiment_id}/run")
def run_experiment(experiment_id: str):
    try:
        record = experiment_store.require(experiment_id)
        dataset = _load_dataset(record.spec.dataset_id)
        completed = worker_service.run(record, dataset)
        return asdict(completed)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="experiment not found") from exc
    except (WorkerUnavailable, WorkerFailed, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/lean/exports")
def create_lean_export(req: LeanExportRequest):
    dataset = _load_dataset(req.dataset_id)
    try:
        export = lean_exporter.export_sma(
            dataset,
            req.parameters,
            req.initial_cash,
        )
        return asdict(export)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc



@app.get("/simulations/plans")
def list_simulation_plans():
    return simulation_plans.list()


@app.get("/simulations/plans/{plan_id}")
def get_simulation_plan(plan_id: str):
    try:
        return asdict(simulation_plans.load(plan_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="simulation plan not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/simulations/{engine}/plans")
def create_simulation_plan(engine: str, req: SimulationPlanRequest):
    dataset = _load_dataset(req.dataset_id)
    try:
        adapter = simulations.get(engine)
        plan = adapter.plan(
            dataset,
            SimulationSpec(
                engine=engine,
                dataset_id=dataset.dataset_id,
                symbol=dataset.symbol,
                strategy=req.strategy,
                parameters=req.parameters,
                initial_cash=req.initial_cash,
                base_currency=req.base_currency.upper(),
            ),
        )
        simulation_plans.save(plan)
        return asdict(plan)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc



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


@app.post("/datasets/ccxt")
def capture_dataset(req: DatasetCaptureRequest):
    try:
        dataset, cache_hit = historical_data.snapshot(
            _market(req.exchange),
            req.symbol,
            req.timeframe,
            req.limit,
            req.refresh,
        )
        return {**dataset.metadata(), "cache_hit": cache_hit}
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=502, detail=f"dataset capture failed: {exc}") from exc


@app.get("/datasets")
def list_datasets():
    return dataset_store.list_metadata()


@app.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str):
    dataset = _load_dataset(dataset_id)
    return {
        **dataset.metadata(),
        "candles": [asdict(candle) for candle in dataset.candles],
    }


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
    return {
        "fill": asdict(fill),
        "cash": paper.portfolio.cash,
        "positions": paper.portfolio.positions,
    }


@app.post("/paper/market-orders")
def paper_market_order(req: MarketPaperOrder):
    try:
        fill = paper_service.market_order(_market(req.exchange), req.symbol, req.side, req.quantity)
        return {
            "fill": asdict(fill),
            "cash": paper.portfolio.cash,
            "positions": paper.portfolio.positions,
        }
    except Exception as exc:
        if isinstance(exc, (HTTPException, RiskRejected)):
            raise
        raise HTTPException(status_code=502, detail=f"paper market order failed: {exc}") from exc


@app.post("/paper/strategies/{strategy_name}/step")
def paper_strategy_step(strategy_name: str, req: StrategyStepRequest):
    try:
        return asdict(
            paper_service.strategy_step(
                _market(req.exchange),
                req.symbol,
                strategy_name,
                req.parameters,
                req.quantity,
                req.timeframe,
                req.limit,
            )
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        if isinstance(exc, (HTTPException, RiskRejected)):
            raise
        raise HTTPException(status_code=502, detail=f"strategy step failed: {exc}") from exc


@app.post("/paper/strategy/sma/step")
def paper_sma_step(req: SMAFastSlowRequest):
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


@app.post("/research/sweeps/sma")
def sma_sweep(req: SMASweepRequest):
    if (req.dataset_id is None) == (req.prices is None):
        raise HTTPException(
            status_code=422,
            detail="provide exactly one of dataset_id or prices",
        )

    dataset_id = req.dataset_id
    symbol = req.symbol
    if dataset_id is not None:
        dataset = _load_dataset(dataset_id)
        prices = [candle.close for candle in dataset.candles]
        symbol = dataset.symbol
    else:
        prices = list(req.prices or [])

    try:
        report = run_sma_parameter_sweep(
            prices=prices,
            fast_values=req.fast_values,
            slow_values=req.slow_values,
            symbol=symbol,
            quantity=req.quantity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "dataset_id": dataset_id,
        "symbol": symbol,
        "evaluated": report.evaluated,
        "skipped": report.skipped,
        "results": [asdict(result) for result in report.results],
    }


@app.post("/research/vectorbt/sma")
def vectorbt_sma(req: VectorBTRequest):
    try:
        result = VectorBTResearch().sma_cross(
            req.prices,
            req.fast,
            req.slow,
            req.initial_cash,
            req.fees,
        )
        return asdict(result)
    except VectorBTUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
