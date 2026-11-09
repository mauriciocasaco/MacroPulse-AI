"""Build a bounded, local financial research response from market and news records."""

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal
import re
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from .analysis_provider import (
    MAX_CITATIONS, Coverage, EconomicMechanism, EvidenceRef, InterpretationProvider, Relevance, validate_interpretation,
)
from .market_data.catalog import ASSETS, ASSET_SOURCES
from .market_data.models import DateRange, PriceBar1d
from .market_data.repository import AvailableAsset, PostgresPriceRepository
from .news.assets import normalize_terms
from .news.research_judgments import ReviewedSource, reviewed_sources
from .news.retrieval import Search


Event = Literal["covid", "ukraine", "genai"]
_EVENT_QUERY = {
    "covid": "covid pandemic coronavirus pandemia",
    "ukraine": "ukraine russian invasion guerra ucrania",
    "genai": "generative artificial intelligence genai inteligencia artificial generativa",
}
_ASSET_IDS = {source.symbol.upper(): asset_id for asset_id, source in ASSET_SOURCES.items()}
_MAX_WINDOW_DAYS = 1096
_RETRIEVAL_LIMIT = 5


def _calendar_date(value: object) -> object:
    if type(value) is date or isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return value
    raise ValueError("expected a calendar date in YYYY-MM-DD format")


CalendarDate = Annotated[date, BeforeValidator(_calendar_date)]


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    asset: str = Field(min_length=1, max_length=32)
    event: Event
    start: CalendarDate
    end_exclusive: CalendarDate
    question: str = Field(min_length=1, max_length=1024)
    as_of_exclusive: CalendarDate | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "AnalysisRequest":
        if self.asset_id not in ASSETS:
            raise ValueError("unsupported asset")
        DateRange(self.start, self.end_exclusive)
        if (self.end_exclusive - self.start).days > _MAX_WINDOW_DAYS:
            raise ValueError("analysis window exceeds 1096 days")
        if self.as_of_exclusive is not None and self.as_of_exclusive <= self.start:
            raise ValueError("cutoff must be after start")
        if not 1 <= len(normalize_terms(self.question).split()) <= 48:
            raise ValueError("question must contain between 1 and 48 normalized terms")
        return self

    @property
    def asset_id(self) -> str:
        return _ASSET_IDS.get(self.asset.upper(), self.asset.lower())


class ObservedClose(BaseModel):
    date: date
    close: Decimal
    retrieved_at: AwareDatetime


class PriceSummary(BaseModel):
    source_id: str
    asset_source_id: int
    economic_unit: str
    price_basis: Literal["provider_unadjusted"] = "provider_unadjusted"
    source_convention: str
    rolling_method: str | None
    observations: int
    first: ObservedClose | None = None
    last: ObservedClose | None = None
    minimum_close: ObservedClose | None = None
    maximum_close: ObservedClose | None = None
    close_change: Decimal | None = None
    close_change_pct: Decimal | None = None


class Citation(BaseModel):
    evidence_id: str = Field(pattern=r"^news-record:[1-9][0-9]{0,18}$")
    source_id: str = Field(min_length=1, max_length=128)
    source_name: str = Field(min_length=1, max_length=256)
    original_url: str = Field(min_length=1, max_length=8192)
    published_at: AwareDatetime | None = None
    published_on: date | None = None
    provider_observed_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime
    relevance: Relevance


class AnalysisResponse(BaseModel):
    asset: str
    asset_id: str
    event: Event
    start: date
    end_exclusive: date
    as_of_exclusive: date | None
    prices: PriceSummary
    coverage: Coverage
    citations: list[Citation]
    economic_mechanisms: list[EconomicMechanism]
    economic_interpretation: str
    limitations: list[str]
    provider: Literal["simulated"] = "simulated"


class AnalysisUnavailable(RuntimeError):
    """The verified market mapping is not available."""


def _observation(bar: PriceBar1d) -> ObservedClose:
    return ObservedClose(date=bar.bar_date, close=bar.close, retrieved_at=bar.retrieved_at)


def _price_summary(asset: AvailableAsset, bars: tuple[PriceBar1d, ...], limitations: list[str]) -> PriceSummary:
    mapping = ASSET_SOURCES[asset.asset_id]
    summary = PriceSummary(
        source_id=asset.source_id, asset_source_id=asset.asset_source_id, economic_unit=asset.economic_unit,
        source_convention=mapping.source_convention, rolling_method=mapping.rolling_method,
        observations=len(bars),
    )
    if not bars:
        limitations.append("No eligible daily closes were available in this window.")
        return summary
    first, last = bars[0], bars[-1]
    summary.first, summary.last = _observation(first), _observation(last)
    summary.minimum_close = _observation(min(bars, key=lambda bar: bar.close))
    summary.maximum_close = _observation(max(bars, key=lambda bar: bar.close))
    if len(bars) < 2:
        limitations.append("At least two observed closes are required to measure price change.")
        return summary
    summary.close_change = last.close - first.close
    if any(bar.close <= 0 or (bar.low is not None and bar.low <= 0) for bar in bars):
        limitations.append("Nonpositive observed prices make percentage change unsuitable in this window.")
    else:
        summary.close_change_pct = (summary.close_change / first.close) * 100
    return summary


def _citation(
    item: dict,
    request: AnalysisRequest,
    reviewed: dict[tuple[str, str], ReviewedSource],
    cutoff: datetime | None,
    end: date,
) -> tuple[Citation, EvidenceRef] | None:
    if item.get("is_synthetic") or item.get("evidence_kind") not in {"excerpt", "full"} or not item.get("passage"):
        return None
    source = reviewed.get((item.get("source_id"), item.get("original_url")))
    if source is None or item.get("canonical_url") != source.url:
        return None
    if not any(link.get("asset_id") == request.asset_id for link in item.get("assets", ())):
        return None
    try:
        citation = Citation(
            evidence_id=item.get("evidence_id"), source_id=item.get("source_id"),
            source_name=item.get("source_name"), original_url=item.get("original_url"),
            published_at=item.get("published_at"), published_on=item.get("published_on"),
            provider_observed_at=item.get("provider_observed_at"), retrieved_at=item.get("retrieved_at"),
            relevance=source.relevance,
        )
    except ValidationError:
        return None
    published_day = citation.published_at.astimezone(UTC).date() if citation.published_at else citation.published_on
    if published_day != source.published_on or not request.start <= source.published_on < end:
        return None
    if cutoff is not None and any(
        timestamp is None or timestamp >= cutoff
        for timestamp in (citation.published_at, citation.provider_observed_at, citation.retrieved_at)
    ):
        return None
    return citation, EvidenceRef(citation.evidence_id, citation.relevance, source.mechanism)


_INTERPRETATION = {
    "sufficient": "The cited records directly address the event and asset. Other contemporaneous factors may also have influenced prices.",
    "partial": "The cited records provide economic context. The available evidence cannot isolate the event's effect on the asset.",
    "insufficient": "Available evidence is insufficient to explain the observed price behavior in relation to the event.",
}


def analyze(
    request: AnalysisRequest,
    repository: PostgresPriceRepository,
    retrieve: Callable[[Search], dict],
    provider: InterpretationProvider,
) -> AnalysisResponse:
    mapping = ASSET_SOURCES[request.asset_id]
    asset = next((asset for asset in repository.list_available_assets()
                  if (asset.asset_id, asset.source_id, asset.symbol) == (mapping.asset_id, mapping.source_id, mapping.symbol)), None)
    if asset is None:
        raise AnalysisUnavailable("Verified asset mapping unavailable")
    end = min(request.end_exclusive, request.as_of_exclusive or request.end_exclusive)
    cutoff = datetime.combine(request.as_of_exclusive, time.min, UTC) if request.as_of_exclusive else None
    window = DateRange(request.start, end)
    bars = tuple(
        bar for bar in repository.read_daily(asset.asset_source_id, window)
        if window.start <= bar.bar_date < window.end_exclusive and bar.session_status != "provisional"
        and (cutoff is None or (
            bar.session_status == "closed" and bar.retrieved_at < cutoff
            and (bar.availability_time is None or bar.availability_time < cutoff)
        ))
    )
    retrieval = retrieve(Search(
        question=f"{_EVENT_QUERY[request.event]} {request.question}", assets=(request.asset_id,),
        start=datetime.combine(window.start, time.min, UTC), end=datetime.combine(window.end_exclusive, time.min, UTC),
        window_basis="publication_day", as_of=cutoff, as_of_mode="system", limit=_RETRIEVAL_LIMIT,
        sources=("eia", "fed"),
        required_theme=f"marketpulse:event:{request.event}",
    ))
    reviewed = {(source.source_id, source.url): source for source in reviewed_sources(request.event, mapping.symbol)}
    citations = {}
    evidence = []
    cited_urls = set()
    for item in retrieval["evidence"][:_RETRIEVAL_LIMIT]:
        match = _citation(item, request, reviewed, cutoff, end)
        if match is None:
            continue
        citation, reference = match
        if citation.evidence_id in citations or citation.original_url in cited_urls:
            continue
        citations[citation.evidence_id] = citation
        evidence.append(reference)
        cited_urls.add(citation.original_url)
        if len(citations) == MAX_CITATIONS:
            break
    packet = tuple(evidence)
    interpretation = validate_interpretation(provider.generate(packet), packet)
    limitations = [
        "Observed provider closes are descriptive and do not identify a causal effect or measure total returns.",
        "The simulated provider assembles reviewed source mechanisms for the event and asset. Unjudged search results and corpus passages are excluded from the response.",
        *retrieval["limitations"],
    ]
    if cutoff is not None:
        limitations.append("The cutoff requires closed bars retrieved earlier. The current store cannot recover superseded price revisions.")
    if mapping.instrument_type == "future":
        limitations.append("The futures series uses provider dates and an unverified contract rolling method.")
    prices = _price_summary(asset, bars, limitations)
    return AnalysisResponse(
        asset=mapping.symbol, asset_id=mapping.asset_id, event=request.event, start=window.start,
        end_exclusive=window.end_exclusive, as_of_exclusive=request.as_of_exclusive, prices=prices,
        coverage=interpretation.coverage, citations=[citations[id_] for id_ in interpretation.citation_ids],
        economic_mechanisms=interpretation.mechanisms,
        economic_interpretation=_INTERPRETATION[interpretation.coverage], limitations=limitations,
    )
