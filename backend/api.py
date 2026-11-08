"""Read-only HTTP API for the verified daily market data catalog."""

from datetime import date, datetime
from decimal import Decimal
import os
import re
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
import psycopg
from psycopg.conninfo import make_conninfo
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .market_data.models import DateRange
from .market_data.repository import MAX_LATEST_DAILY, AvailableAsset, PostgresPriceRepository


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
    connection.read_only = True
    return connection


def get_repository() -> PostgresPriceRepository:
    dsn = os.environ.get("MARKET_API_DSN")
    if not dsn:
        raise HTTPException(status_code=503, detail="Market data unavailable")
    return PostgresPriceRepository(
        make_conninfo(dsn, sslmode="verify-full", gssencmode="disable", connect_timeout=5),
        connect=_read_only_connection,
    )


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
