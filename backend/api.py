"""Read-only HTTP API for the verified daily market data catalog."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
import logging
import os
from pathlib import Path as FilePath
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
import psycopg
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .analysis import AnalysisRequest, AnalysisResponse, AnalysisUnavailable, analyze
from .analysis_provider import InterpretationProvider, ProviderError, SimulatedProvider
from .macro_data.api import router as macro_router
from .market_data.api_config import (
    MarketApiConfig, MarketApiConfigError, connect_read_only_dev, is_dev_target, resolve_market_api,
)
from .market_data.models import DateRange
from .market_data.repository import MAX_LATEST_DAILY, AvailableAsset, PostgresPriceRepository
from .news.embeddings import E5Small
from .news.retrieval import Search, search


_LOGGER = logging.getLogger(__name__)


def _iso_date(value: object) -> object:
    if isinstance(value, str) and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("expected a calendar date in YYYY-MM-DD format")
    return value


QueryDate = Annotated[date, BeforeValidator(_iso_date)]
MappingId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class HistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: QueryDate | None = None
    end_exclusive: QueryDate | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "HistoryQuery":
        self.window()
        return self

    def window(self) -> DateRange | None:
        if self.start is None and self.end_exclusive is None:
            return None
        if self.start is None or self.end_exclusive is None:
            raise ValueError("start and end_exclusive must be provided together")
        return DateRange(self.start, self.end_exclusive)


class ComparisonQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first: int = Field(ge=1, le=2**63 - 1, description="First asset_source_id")
    second: int = Field(ge=1, le=2**63 - 1, description="Second asset_source_id")
    start: QueryDate
    end_exclusive: QueryDate

    @model_validator(mode="after")
    def validate_comparison(self) -> "ComparisonQuery":
        if self.first == self.second:
            raise ValueError("comparison requires two distinct asset_source_id values")
        self.window()
        return self

    def window(self) -> DateRange:
        return DateRange(self.start, self.end_exclusive)


class LatestQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=30, ge=1, le=MAX_LATEST_DAILY)


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    detail: str


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_source_id: int
    asset_id: str
    name: str
    source_id: str
    provider_name: str
    symbol: str
    economic_unit: str
    calendar_code: str
    first_date: date | None
    last_date: date | None
    bar_count: int


class Observation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_source_id: int
    asset_id: str
    source_id: str
    symbol: str
    bar_date: date
    close: Decimal
    adjusted_close: Decimal | None
    adjusted_close_basis: Literal["provider_reported"] | None
    retrieved_at: datetime


class DailyBar(Observation):
    interval: Literal["1d"]
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    volume: Decimal | None
    ohlc_basis: Literal["provider_unadjusted"]
    session_status: Literal["closed", "provisional", "unknown"]
    quality_status: Literal["complete", "partial"]
    source_convention: str
    provider: str
    adapter_version: str
    provider_version: str | None
    volume_unit: str | None
    exchange_trade_date: date | None
    settlement_time: datetime | None
    availability_time: datetime | None
    rolling_method: str | None


class HistoryResponse(BaseModel):
    asset: AssetResponse
    bars: list[DailyBar]


class ComparisonPointResponse(Observation):
    economic_unit: str
    ohlc_basis: Literal["provider_unadjusted"]
    session_status: Literal["closed", "provisional", "unknown"]
    source_convention: str
    rolling_method: str | None


class ComparisonResponse(BaseModel):
    first: AssetResponse
    second: AssetResponse
    points: list[ComparisonPointResponse]


def _read_only_connection(dsn: str) -> psycopg.Connection:
    connection = psycopg.connect(dsn)
    if not connection.pgconn.ssl_in_use:
        connection.close()
        raise psycopg.OperationalError("Market API requires PostgreSQL TLS")
    if is_dev_target(connection.info.host, connection.info.user):
        connection.close()
        raise MarketApiConfigError("explicit_dev_route_requires_pinning")
    connection.read_only = True
    return connection


def _market_config(request: Request = None) -> MarketApiConfig:
    if _MODE == "test":
        raise MarketApiConfigError("test_requires_repository_fixture")
    return resolve_market_api(request.scope if request is not None else None)


MarketConfig = Annotated[MarketApiConfig, Depends(_market_config)]


def get_repository(config: MarketConfig) -> PostgresPriceRepository:
    connect = connect_read_only_dev if config.dev else _read_only_connection
    return PostgresPriceRepository(config.dsn, connect=connect)


Repository = Annotated[PostgresPriceRepository, Depends(get_repository)]


def _asset(catalog: tuple[AvailableAsset, ...], mapping_id: int) -> AvailableAsset:
    for asset in catalog:
        if asset.asset_source_id == mapping_id:
            return asset
    raise HTTPException(status_code=404, detail="Asset mapping not found")


app = FastAPI(title="MarketPulse AI Market Data API", version="0.1.0")
_UNAVAILABLE = {503: {"model": ErrorResponse, "description": "Market data unavailable"}}
_ASSET_ERRORS = {404: {"model": ErrorResponse, "description": "Asset mapping not found"}, **_UNAVAILABLE}


@app.exception_handler(psycopg.Error)
async def _database_error(_request: Request, _error: psycopg.Error) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Market data unavailable"})


@app.exception_handler(MarketApiConfigError)
async def _configuration_error(_request: Request, error: MarketApiConfigError) -> JSONResponse:
    _LOGGER.warning("Market API configuration rejected (%s)", error)
    return JSONResponse(status_code=503, content={"detail": "Market data unavailable"})


@app.get("/health")
def health() -> Health:
    """Check process liveness."""
    return Health()


@app.get("/assets", response_model=list[AssetResponse], responses=_UNAVAILABLE)
def assets(repository: Repository):
    """List verified mappings and their stored daily coverage."""
    return repository.list_available_assets()


@app.get("/assets/{asset_source_id}/history", response_model=HistoryResponse, responses=_ASSET_ERRORS)
def history(asset_source_id: MappingId, query: Annotated[HistoryQuery, Query()], repository: Repository):
    """Read daily bars in ascending order, optionally within [start, end_exclusive)."""
    asset = _asset(repository.list_available_assets(), asset_source_id)
    return {"asset": asset, "bars": repository.read_daily(asset_source_id, query.window())}


@app.get("/assets/{asset_source_id}/latest", response_model=HistoryResponse, responses=_ASSET_ERRORS)
def latest(asset_source_id: MappingId, query: Annotated[LatestQuery, Query()], repository: Repository):
    """Read up to limit stored observations in ascending date order."""
    asset = _asset(repository.list_available_assets(), asset_source_id)
    return {"asset": asset, "bars": repository.read_latest_daily(asset_source_id, query.limit)}


@app.get("/compare", response_model=ComparisonResponse, responses=_ASSET_ERRORS)
def compare(query: Annotated[ComparisonQuery, Query()], repository: Repository):
    """Compare observed closes in their original units, without filling missing dates."""
    catalog = repository.list_available_assets()
    first = _asset(catalog, query.first)
    second = _asset(catalog, query.second)
    points = repository.compare_daily(query.first, query.second, query.window())
    return {"first": first, "second": second, "points": points}


class _BoundedAnalysisRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            received = 0

            async def receive():
                nonlocal received
                message = await request.receive()
                received += len(message.get("body", b""))
                if received > 8192:
                    raise HTTPException(status_code=413, detail="Analysis request too large")
                return message

            try:
                return await handler(Request(request.scope, receive))
            except RequestValidationError:
                raise HTTPException(status_code=422, detail="Invalid analysis request") from None

        return bounded


def _analysis_connection(dsn: str) -> psycopg.Connection:
    connection = _read_only_connection(dsn)
    try:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '2s'")
    except psycopg.Error:
        connection.close()
        raise
    return connection


def get_analysis_repository(config: MarketConfig) -> PostgresPriceRepository:
    connect = connect_read_only_dev if config.dev else _analysis_connection
    return PostgresPriceRepository(config.dsn, connect=connect)


@lru_cache(maxsize=1)
def _local_embedding(cache: str) -> E5Small:
    return E5Small(FilePath(cache))


def get_analysis_retriever(config: MarketConfig) -> Callable[[Search], dict]:
    connect = connect_read_only_dev if config.dev else _analysis_connection

    def retrieve(request: Search) -> dict:
        cache = os.environ.get("MARKET_ANALYSIS_EMBEDDING_CACHE")
        try:
            model = _local_embedding(cache) if cache else None
            with connect(config.dsn) as connection:
                return search(connection, request, model)
        except MarketApiConfigError:
            raise
        except (ImportError, RuntimeError, ValueError, OSError):
            raise HTTPException(status_code=503, detail="Analysis evidence unavailable") from None

    return retrieve


def get_analysis_provider() -> InterpretationProvider:
    return SimulatedProvider()


_analysis_router = APIRouter(route_class=_BoundedAnalysisRoute)


@_analysis_router.post("/analysis", response_model=AnalysisResponse, responses={
    413: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
    502: {"model": ErrorResponse}, **_UNAVAILABLE,
})
def financial_analysis(
    request: AnalysisRequest,
    repository: Annotated[PostgresPriceRepository, Depends(get_analysis_repository)],
    retrieve: Annotated[Callable[[Search], dict], Depends(get_analysis_retriever)],
    provider: Annotated[InterpretationProvider, Depends(get_analysis_provider)],
) -> AnalysisResponse:
    """Analyze historical closes and source references with the local simulated provider."""
    try:
        return analyze(request, repository, retrieve, provider)
    except AnalysisUnavailable:
        raise HTTPException(status_code=503, detail="Market data unavailable") from None
    except ProviderError:
        raise HTTPException(status_code=502, detail="Analysis provider unavailable") from None


app.include_router(_analysis_router)
app.include_router(macro_router)

_MODE = os.environ.get("MARKETPULSE_MODE", "local")
if _MODE == "public_demo":
    from .public_demo import create_public_app

    app = create_public_app()
elif _MODE == "public_online":
    from .public_online import create_online_app

    app = create_online_app()
elif _MODE not in {"local", "test"}:
    raise RuntimeError("Invalid MarketPulse mode")
else:
    from .macro_ai.api import router as macro_ai_router

    app.include_router(macro_ai_router)
