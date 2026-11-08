"""Download and inspect local Yahoo daily history without opening a database."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import file_digest
import json
import os
from pathlib import Path
import sys
import tempfile
from time import sleep
from zoneinfo import ZoneInfo

from .catalog import ASSETS, ASSET_SOURCES, is_yahoo_us_equity_open_date, matches_archived_mapping
from .models import AssetSource, DateRange, PriceAdapter, RawBatch, RawRow
from .normalizer import financial_rejection, normalize
from .session_calendar import US_EQUITY_FULL_CLOSURES, US_EQUITY_VERIFIED_DATES
from .yahoo_adapter import YahooAdapter, YahooAdapterError

DAY = timedelta(days=1)
DEFAULT_START = date(2019, 11, 8)
DEFAULT_END = date(2026, 11, 8)
DEFAULT_OUTPUT = Path("data/backfills")
FORMAT_VERSION = 1
NORMALIZED_FORMAT_VERSION = 1
RETRY_DELAYS = (2, 8)
WINDOW_ERRORS = {"empty_indeterminate", "incompatible_structure", "unexpected_response"}


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_raw(path: Path, batch: RawBatch) -> None:
    """Store a complete Yahoo RawBatch, including null and absent field differences."""
    payload = {
        "format_version": FORMAT_VERSION,
        "asset_id": batch.asset_id,
        "mapping": asdict(batch.mapping),
        "provider": batch.provider,
        "ticker": batch.ticker,
        "requested_start": batch.requested_range.start.isoformat(),
        "requested_end_exclusive": batch.requested_range.end_exclusive.isoformat(),
        "interval": batch.interval,
        "retrieved_at": batch.retrieved_at.isoformat(),
        "adapter_version": batch.adapter_version,
        "provider_version": batch.provider_version,
        "temporal_metadata": dict(batch.temporal_metadata),
        "warnings": batch.warnings,
        "rows": [[row.original_label, dict(row.fields)] for row in batch.rows],
    }
    if any(type(row.original_label) is not int for row in batch.rows):
        raise ValueError("Yahoo backfill requires raw Unix timestamp labels")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _write_atomic(path, gzip.compress(encoded, compresslevel=9, mtime=0))


def read_raw(path: Path) -> RawBatch:
    """Load an archived Yahoo batch and verify the gzip checksum."""
    with gzip.open(path, "rt", encoding="utf-8") as source:
        payload = json.load(source)
    if payload["format_version"] != FORMAT_VERSION:
        raise ValueError("unsupported raw cache format")
    raw_rows = payload["rows"]
    if not raw_rows or any(type(label) is not int for label, _ in raw_rows):
        raise ValueError("raw cache has missing or invalid Unix timestamps")
    metadata = payload["temporal_metadata"]
    metadata["out_of_range_timestamps"] = tuple(metadata["out_of_range_timestamps"])
    return RawBatch(
        asset_id=payload["asset_id"],
        mapping=AssetSource(**payload["mapping"]),
        provider=payload["provider"],
        ticker=payload["ticker"],
        requested_range=DateRange(
            date.fromisoformat(payload["requested_start"]),
            date.fromisoformat(payload["requested_end_exclusive"]),
        ),
        interval=payload["interval"],
        retrieved_at=datetime.fromisoformat(payload["retrieved_at"]),
        adapter_version=payload["adapter_version"],
        rows=tuple(RawRow(label, fields) for label, fields in raw_rows),
        temporal_metadata=metadata,
        provider_version=payload["provider_version"],
        warnings=tuple(payload["warnings"]),
    )


def _normalized_json_value(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported normalized JSON value: {type(value).__name__}")


def _export_path(output: Path, mapping: AssetSource, window: DateRange) -> Path:
    return output / "normalized" / f"{window.start}_{window.end_exclusive}" / f"{mapping.asset_id}.json.gz"


def export_normalized(result: AssetBackfill, output: Path, window: DateRange) -> Path:
    """Write validated bars and diagnostic records with verifiable raw references."""
    asset = ASSETS[result.mapping.asset_id]
    batches = []
    for path, batch in sorted(result.batches.items()):
        normalized = normalize(batch, asset, result.mapping, batch.requested_range)
        rejected = {item.position: item.code for item in normalized.rejected}
        accepted_bars = iter(normalized.bars)
        accepted_records = []
        rejected_records = []
        for position, (row, label) in enumerate(zip(batch.rows, _local_labels(batch), strict=True)):
            origin = {"raw_position": position, "original_label": row.original_label}
            if position in rejected:
                rejected_records.append({
                    **origin, "provider_local_date": label.date(), "code": rejected[position],
                })
            else:
                accepted_records.append({**origin, "bar": asdict(next(accepted_bars))})
        with path.open("rb") as source:
            raw_sha256 = file_digest(source, "sha256").hexdigest()
        batches.append({
            "raw_file": path.relative_to(output).as_posix(),
            "raw_sha256": raw_sha256,
            "provenance": dict(normalized.provenance),
            "warnings": normalized.warnings,
            "accepted": accepted_records,
            "rejected": rejected_records,
        })
    payload = {
        "format_version": NORMALIZED_FORMAT_VERSION,
        "mapping": asdict(result.mapping),
        "requested_start": window.start,
        "requested_end_exclusive": window.end_exclusive,
        "batches": batches,
    }
    encoded = json.dumps(
        payload, default=_normalized_json_value, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    path = _export_path(output, result.mapping, window)
    _write_atomic(path, gzip.compress(encoded, compresslevel=9, mtime=0))
    return path


def _raw_path(output: Path, mapping: AssetSource, window: DateRange) -> Path:
    return output / mapping.asset_id / f"{window.start}_{window.end_exclusive}.json.gz"


def _local_labels(batch: RawBatch) -> Iterator[datetime]:
    """Describe raw labels in Yahoo's reported zone, without assigning session dates."""
    zone = ZoneInfo(batch.temporal_metadata["index_timezone"])
    for row in batch.rows:
        yield datetime.fromtimestamp(row.original_label, timezone.utc).astimezone(zone)


def _missing_windows(window: DateRange, days: set[date], daily: bool) -> list[DateRange]:
    """Find daily gaps for BTC or stretches with at least five absent weekdays."""
    missing: list[DateRange] = []
    gap_start = None
    expected_days = 0
    current = window.start
    while current <= window.end_exclusive:
        if current == window.end_exclusive or current in days:
            if gap_start is not None and expected_days >= (1 if daily else 5):
                missing.append(DateRange(gap_start, current))
            gap_start = None
            expected_days = 0
        elif daily or current.weekday() < 5:
            if gap_start is None:
                gap_start = current
            expected_days += 1
        if current == window.end_exclusive:
            break
        current += DAY
    return missing


def _bounded_windows(window: DateRange) -> Iterator[DateRange]:
    current = window.start
    while current < window.end_exclusive:
        end = current + min(window.end_exclusive - current, timedelta(days=366))
        yield DateRange(current, end)
        current = end


@dataclass
class AssetBackfill:
    mapping: AssetSource
    batches: dict[Path, RawBatch] = field(default_factory=dict)
    failures: dict[DateRange, str] = field(default_factory=dict)
    cache_hits: int = 0
    requests: int = 0


def _verify_identity(batch: RawBatch, mapping: AssetSource, window: DateRange, path: Path) -> None:
    if (
        not matches_archived_mapping(batch.mapping, mapping)
        or batch.asset_id != mapping.asset_id
        or batch.provider != mapping.source_id
        or batch.ticker != mapping.symbol
        or batch.interval != "1d"
        or batch.requested_range != window
    ):
        raise ValueError(f"raw batch identity mismatch for {path}")


def _acquire(
    result: AssetBackfill, adapter: PriceAdapter | None, output: Path, window: DateRange,
    pause: Callable[[int], None],
) -> RawBatch | None:
    path = _raw_path(output, result.mapping, window)
    if path.is_file():
        batch = read_raw(path)
        _verify_identity(batch, result.mapping, window, path)
        result.batches[path] = batch
        result.cache_hits += 1
        return batch
    if adapter is None:
        result.failures[window] = "cache_missing"
        return None

    for attempt in range(len(RETRY_DELAYS) + 1):
        result.requests += 1
        try:
            batch = adapter.fetch_daily(result.mapping, window)
        except YahooAdapterError as error:
            if error.code == "dependency_unavailable":
                raise
            if attempt < len(RETRY_DELAYS):
                pause(RETRY_DELAYS[attempt] * (4 if error.code == "rate_limited" else 1))
                continue
            result.failures[window] = error.code
            return None
        _verify_identity(batch, result.mapping, window, path)
        write_raw(path, batch)
        result.batches[path] = read_raw(path)
        return result.batches[path]
    raise RuntimeError("unreachable retry state")


def backfill_asset(
    adapter: PriceAdapter | None, output: Path, mapping: AssetSource, window: DateRange, *,
    pause: Callable[[int], None] = sleep,
) -> AssetBackfill:
    result = AssetBackfill(mapping)
    daily = mapping.symbol == "BTC-USD"
    visited: set[DateRange] = set()

    def visit(current: DateRange) -> None:
        if current in visited:
            return
        visited.add(current)
        children = [child for child in _bounded_windows(current) if child != current]
        if not _raw_path(output, mapping, current).is_file() and any(
            _raw_path(output, mapping, child).is_file() for child in children
        ):
            # Resume a previous fallback without retrying the parent range.
            for child in children:
                visit(child)
            return
        batch = _acquire(result, adapter, output, current, pause)
        if batch is None:
            if result.failures[current] in WINDOW_ERRORS:
                for child in children:
                    visit(child)
            return
        dates = {
            label.date() for label in _local_labels(batch)
            if batch.temporal_metadata["requested_period1"] <= label.timestamp()
            < batch.temporal_metadata["requested_period2"]
        }
        for gap in _missing_windows(current, dates, daily):
            for child in _bounded_windows(gap):
                if child != current:
                    visit(child)

    visit(window)
    return result


def _allocated_bytes(path: Path) -> int:
    """Read allocated bytes, including filesystem block rounding."""
    if os.name != "nt":
        return path.stat().st_blocks * 512

    import ctypes
    from ctypes import wintypes
    import msvcrt

    class FileStandardInfo(ctypes.Structure):
        _fields_ = [
            ("allocation_size", ctypes.c_longlong),
            ("end_of_file", ctypes.c_longlong),
            ("number_of_links", wintypes.DWORD),
            ("delete_pending", ctypes.c_ubyte),
            ("directory", ctypes.c_ubyte),
        ]

    info = FileStandardInfo()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    query.restype = wintypes.BOOL
    with path.open("rb") as source:
        if not query(msvcrt.get_osfhandle(source.fileno()), 1, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    return info.allocation_size


@dataclass
class AssetSummary:
    dates: set[date] = field(default_factory=set)
    labels: Counter[int] = field(default_factory=Counter)
    clocks: Counter[str] = field(default_factory=Counter)
    clock_offsets: Counter[str] = field(default_factory=Counter)
    morning_labels: set[date] = field(default_factory=set)
    rejected: Counter[str] = field(default_factory=Counter)
    previous_rejected: Counter[str] = field(default_factory=Counter)
    previous_accepted: int = 0
    financial_rejected: Counter[str] = field(default_factory=Counter)
    accepted_dates: Counter[date] = field(default_factory=Counter)
    quality: Counter[str] = field(default_factory=Counter)
    warnings: set[str] = field(default_factory=set)
    zones: set[str] = field(default_factory=set)
    conventions: set[str] = field(default_factory=set)
    archived_conventions: set[str] = field(default_factory=set)
    outside: int = 0
    logical_bytes: int = 0
    allocated_bytes: int = 0
    close_present: set[date] = field(default_factory=set)


def summarize(result: AssetBackfill, window: DateRange) -> AssetSummary:
    summary = AssetSummary()
    asset = ASSETS[result.mapping.asset_id]
    for path, batch in result.batches.items():
        normalized = normalize(batch, asset, result.mapping, batch.requested_range)
        previous = normalized
        if batch.mapping != result.mapping:
            previous = normalize(batch, asset, batch.mapping, batch.requested_range)
        summary.previous_accepted += len(previous.bars)
        summary.previous_rejected.update(item.code for item in previous.rejected)
        summary.archived_conventions.add(batch.mapping.source_convention)
        summary.accepted_dates.update(bar.bar_date for bar in normalized.bars)
        summary.quality.update(bar.quality_status for bar in normalized.bars)
        summary.rejected.update(item.code for item in normalized.rejected)
        summary.warnings.update(normalized.warnings)
        summary.conventions.add(normalized.provenance["temporal_convention_status"])
        summary.zones.add(batch.temporal_metadata["index_timezone"])
        summary.logical_bytes += path.stat().st_size
        summary.allocated_bytes += _allocated_bytes(path)
        start = batch.temporal_metadata["requested_period1"]
        end = batch.temporal_metadata["requested_period2"]
        for row, label in zip(batch.rows, _local_labels(batch), strict=True):
            if code := financial_rejection(row.fields, asset):
                summary.financial_rejected[code] += 1
            summary.labels[row.original_label] += 1
            if not start <= row.original_label < end or not window.start <= label.date() < window.end_exclusive:
                summary.outside += 1
                continue
            summary.dates.add(label.date())
            summary.clocks[label.time().isoformat()] += 1
            summary.clock_offsets[label.strftime("%H:%M:%S %z")] += 1
            if label.hour == 9 and label.minute == 30:
                summary.morning_labels.add(label.date())
            if row.fields.get("Close") is not None:
                summary.close_present.add(label.date())
    return summary


def _counts(values: Counter[str]) -> str:
    return ", ".join(f"{key} ×{count}" for key, count in values.most_common()) or "—"


def _span(dates: set[date]) -> str:
    return f"{min(dates)} a {max(dates)}" if dates else "—"


def render_report(
    results: list[AssetBackfill], window: DateRange, test_summary: str, *, offline: bool = False,
) -> tuple[str, bool]:
    summaries = [summarize(result, window) for result in results]
    lines = [
        "# MarketPulse AI — Histórico diario de Yahoo Finance" + (" · Etapa 1C" if offline else ""),
        "",
        f"Rango solicitado: `[{window.start}, {window.end_exclusive})` "
        f"({(window.end_exclusive - window.start).days:,} días naturales).",
        "",
        "Las fechas de esta tabla corresponden a las observaciones aceptadas. Para los futuros son "
        "las fechas diarias publicadas por Yahoo. "
        "Fuera de rango cuenta filas fuera de su solicitud o del rango general; se conservan en bruto.",
        "",
        "| Símbolo | Primera válida | Última válida | Filas crudas | Aceptadas | Rechazadas (motivos) | "
        "Duplicadas / fuera de rango | Convención | Bytes de archivo / asignados | Advertencias |",
        "| --- | --- | --- | ---: | ---: | --- | ---: | --- | ---: | --- |",
    ]
    attention = False
    for result, summary in zip(results, summaries, strict=True):
        dates = summary.dates
        daily = result.mapping.symbol == "BTC-USD"
        gaps = _missing_windows(window, dates, daily)
        duplicate_dates = summary.accepted_dates.total() - len(summary.accepted_dates)
        status = ", ".join(sorted(summary.conventions)) or "sin datos"
        warnings = []
        if gaps:
            warnings.append(f"{len(gaps)} tramo(s) sin cobertura")
        if duplicate_dates:
            warnings.append(f"{duplicate_dates} fechas normalizadas repetidas")
        missing_close = summary.dates - summary.close_present
        if missing_close:
            warnings.append(f"{len(missing_close)} fechas sin Close")
        if summary.warnings:
            warnings.append("ver detalle")
        if result.failures:
            warnings.append(f"{len(result.failures)} solicitud(es) fallidas")
        attention |= bool(
            gaps or duplicate_dates or not dates
            or (daily and len(summary.accepted_dates) != len(dates))
            or "cache_missing" in result.failures.values()
        )
        valid = summary.accepted_dates
        lines.append(
            f"| {result.mapping.symbol} | {min(valid) if valid else '—'} | {max(valid) if valid else '—'} "
            f"| {summary.labels.total()} | {summary.accepted_dates.total()} "
            f"| {summary.rejected.total()} ({_counts(summary.rejected)}) "
            f"| {summary.labels.total() - len(summary.labels)} / {summary.outside} "
            f"| {result.mapping.source_convention} · {status} "
            f"| {summary.logical_bytes:,} / {summary.allocated_bytes:,} "
            f"| {', '.join(warnings) or '—'} |"
        )

    downloaded = sum(item.labels.total() for item in summaries)
    logical = sum(item.logical_bytes for item in summaries)
    allocated = sum(item.allocated_bytes for item in summaries)
    accepted = sum(summary.accepted_dates.total() for summary in summaries)
    ready = sum(len(summary.accepted_dates) for summary in summaries)
    recovered = accepted - sum(summary.previous_accepted for summary in summaries)
    futures_recovered = sum(
        summary.accepted_dates.total() - summary.previous_accepted
        for result, summary in zip(results, summaries, strict=True)
        if result.mapping.instrument_type == "future"
    )
    hypothetical = sum(len(item.close_present) for item in summaries)
    lines.extend([
        "",
        f"**Total:** {downloaded:,} filas crudas, {logical:,} bytes de archivos "
        f"({logical / 1_000_000:.3f} MB) y {allocated:,} bytes asignados "
        f"({allocated / 1_000_000:.3f} MB). Los tamaños incluyen solo los archivos crudos de esta ejecución.",
        f"El normalizador aceptó {accepted:,} filas, {recovered:,} más que con los mapeos "
        f"archivados, y rechazó {downloaded - accepted:,}.",
        f"De esa recuperación, {futures_recovered:,} corresponden a GC=F y CL=F.",
        "",
        "## Diagnóstico temporal y financiero",
        "",
        "La validación temporal precede a la financiera. La última columna vuelve a aplicar "
        "las mismas reglas financieras a toda fila cruda, de forma independiente. Sus cifras "
        "pueden coincidir con rechazos temporales y no se suman al total de rechazos.",
        "",
        "| Símbolo | Aceptadas con mapeo archivado | Rechazos con mapeo archivado | Problemas financieros independientes |",
        "| --- | ---: | --- | --- |",
        *[
            f"| {result.mapping.symbol} | {summary.previous_accepted} | {_counts(summary.previous_rejected)} "
            f"| {_counts(summary.financial_rejected)} |"
            for result, summary in zip(results, summaries, strict=True)
        ],
        "",
        "## Cobertura y procedencia por activo",
        "",
        "Para BTC-USD se espera una etiqueta cada día. Para el resto, la detección de "
        "huecos de adquisición marca al menos cinco días laborables consecutivos sin etiquetas. "
        "Las acciones se contrastan además con el calendario oficial acotado. "
        "Los duplicados de la tabla cuentan repeticiones del epoch crudo.",
    ])
    for result, summary in zip(results, summaries, strict=True):
        lines.extend([
            "",
            f"### {result.mapping.symbol}",
            "",
            f"- Etiquetas de fecha distintas dentro de rango: {len(summary.dates):,} "
            f"({_span(summary.dates)}); {len(summary.close_present):,} con Close presente.",
            f"- Horas locales observadas en las etiquetas: {_counts(summary.clocks)}.",
            f"- Zona declarada: {', '.join(sorted(summary.zones)) or '—'}. "
            f"Convención del archivo: {', '.join(sorted(summary.archived_conventions)) or '—'}.",
            f"- Lotes reutilizados: {result.cache_hits}; intentos de YahooAdapter: {result.requests}.",
        ])
        if result.mapping.symbol == "BTC-USD":
            expected = (window.end_exclusive - window.start).days
            lines.append(
                f"- Cobertura diaria: {len(summary.dates):,}/{expected:,} crudas, "
                f"{len(summary.accepted_dates):,}/{expected:,} normalizadas."
            )
        if is_yahoo_us_equity_open_date(result.mapping):
            first = max(window.start, US_EQUITY_VERIFIED_DATES.start)
            end = min(window.end_exclusive, US_EQUITY_VERIFIED_DATES.end_exclusive)
            expected_dates = {first + i * DAY for i in range((end - first).days)}
            expected_dates = {day for day in expected_dates if day.weekday() < 5} - US_EQUITY_FULL_CLOSURES
            missing = expected_dates - summary.accepted_dates.keys()
            extra = summary.accepted_dates.keys() - expected_dates
            attention |= bool(missing or extra or first != window.start or end != window.end_exclusive)
            lines.append(
                f"- Calendario verificado: {len(expected_dates):,} sesiones esperadas, "
                f"{len(missing)} fechas válidas faltantes y {len(extra)} extra."
            )
        if summary.quality:
            lines.append(f"- Calidad de las observaciones aceptadas: {_counts(summary.quality)}.")
        if result.mapping.instrument_type == "future":
            lines.append(f"- Horas de Nueva York por desplazamiento UTC: {_counts(summary.clock_offsets)}.")
            weekends = sum(day.weekday() >= 5 for day in summary.dates)
            lines.append(f"- Etiquetas de sábado o domingo: {weekends}.")
            if summary.morning_labels:
                lines.append(f"- Etiquetas de las 09:30: {', '.join(map(str, sorted(summary.morning_labels)))}.")
        missing_close = summary.dates - summary.close_present
        if missing_close:
            lines.append(f"- Fechas sin Close: {', '.join(str(day) for day in sorted(missing_close))}.")
        for path, batch in result.batches.items():
            lines.append(
                f"- Archivo `{path.parent.name}/{path.name}`, recuperado {batch.retrieved_at.isoformat()} "
                f"(UTC; adaptador {batch.adapter_version}, yfinance {batch.provider_version})."
            )
        for gap in _missing_windows(window, summary.dates, result.mapping.symbol == "BTC-USD"):
            lines.append(f"- Tramo pendiente: `[{gap.start}, {gap.end_exclusive})`.")
        for span, code in result.failures.items():
            lines.append(f"- Fallo en `[{span.start}, {span.end_exclusive})`: `{code}`.")
        for warning in sorted(summary.warnings):
            lines.append(f"- {warning}")

    lines.extend([
        "",
        "## Convenciones y restricciones",
        "",
        "- BTC-USD usa etiquetas diarias UTC a medianoche. El corte diario y el estado de sesión siguen sin verificar.",
        "- GC=F y CL=F usan la fecha diaria publicada por Yahoo, en días laborables y con etiquetas a las "
        "00:00 o 09:30 de Nueva York. La fecha oficial de negociación, la liquidación, la disponibilidad y "
        "el roll siguen sin verificar. "
        "[Oro](https://www.cmegroup.com/market-regulation/files/gold-futures-and-options-fact-card.pdf) · "
        "[WTI](https://www.cmegroup.com/education/courses/event-contracts-underlying-markets/wti-overview)",
        "- Para ambos futuros se exportan `exchange_trade_date`, `settlement_time` y "
        "`availability_time` como NULL y `rolling_method` como `unknown`.",
        "- El 2025-07-04 ambos futuros presentan Close pese a que CME no publicó liquidación oficial. "
        "La atribución de las barras de Yahoo a una fecha oficial de negociación sigue abierta. "
        "[CME](https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/us-independence-day-settlement-times-2025.pdf) · "
        "[OCC](https://infomemo.theocc.com/infomemos?number=56777)",
        "- Las etiquetas especiales de GC=F del 2019-11-29 y 2019-12-24 corresponden a "
        "las fechas y Close publicados (1465,60 y 1499,10). "
        "[Yahoo 2019-11-29](https://finance.yahoo.com/quote/GC%3DF/history/?filter=history&frequency=1d&includeAdjustedClose=true&interval=1d&period1=1492732800&period2=1650499200) · "
        "[Yahoo 2019-12-24](https://finance.yahoo.com/quote/GC%3DF/history/?int=&period1=1506297600&period2=1664064000)",
        "- El 2025-11-28 ambos futuros tienen etiqueta a las 09:30 y coinciden en fecha y Close "
        "con Yahoo (4254,90 en GC=F y 58,55 en CL=F). Los Close de ambos en las transiciones DST "
        "del 2026-03-06/09 y 2026-10-30/11-02 también coinciden. "
        "[Yahoo GC](https://finance.yahoo.com/quote/GC%3DF/history/?p=GC%3DF) · "
        "[Yahoo CL](https://finance.yahoo.com/quote/CL%3DF/history/?p=CL%3DF)",
        "- ^GSPC, VT y VWO usan etiquetas de apertura a las 09:30 en America/New_York. "
        "Se contrastaron con cierres publicados por Yahoo y con el calendario del período verificado. "
        "El corte de la barra y su hora de disponibilidad siguen pendientes. "
        "[S&P 500](https://finance.yahoo.com/quote/%5EGSPC/history/?p=%5EGSPC) · "
        "[VT](https://finance.yahoo.com/quote/VT/history/?p=VT) · "
        "[VWO](https://finance.yahoo.com/quote/VWO/history/?p=VWO) · "
        "[NYSE](https://www.nyse.com/trade/hours-calendars)",
        "- Los precios y ajustes reflejan lo que Yahoo entregó en la fecha de adquisición. Una investigación "
        "histórica necesita evidencia adicional antes de tratar los ajustes como conocidos en la fecha de la barra.",
        "- Los derechos de almacenamiento en la nube y redistribución requieren revisión. "
        "[Condiciones de Yahoo](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) · "
        "[Restricciones de datos por instrumento](https://help.yahoo.com/kb/finance/certain-amounts-sln2311.html)",
        "",
        "## Estimación de PostgreSQL",
        "",
        "Estimación de 300 a 500 bytes por fila para el heap de `market_data.price_bars`, su clave "
        "primaria y dos índices B-tree. Supone filas con valores NUMERIC típicos, cabecera de tupla, "
        "mapa de nulos, alineación y espacio libre en páginas. El tamaño excluye WAL, tablas de catálogo, "
        "ejecuciones, copias, crecimiento por actualizaciones y tamaño mínimo de relaciones.",
        f"- {ready:,} fechas únicas normalizadas de los mapeos habilitados en Python: "
        f"{ready * 300 / 1_000_000:.2f}–"
        f"{ready * 500 / 1_000_000:.2f} MB.",
        f"- Escenario hipotético con {hypothetical:,} fechas únicas crudas con Close presente "
        f"de los seis activos: {hypothetical * 300 / 1_000_000:.2f}–"
        f"{hypothetical * 500 / 1_000_000:.2f} MB.",
        "- [Páginas de PostgreSQL](https://www.postgresql.org/docs/current/storage-page-layout.html) · "
        "[Almacenamiento de NUMERIC](https://www.postgresql.org/docs/current/datatype-numeric.html)",
        "",
        "## Reproducción y recomendación",
        "",
        f"- `python -m backend.market_data.backfill --offline --start {window.start} --end-exclusive {window.end_exclusive}`",
        "- Cada `.json.gz` contiene un lote Yahoo en formato JSON v1 con metadata, rango exclusivo, "
        "epoch originales y campos del adaptador. `read_raw` verifica gzip y reconstruye el lote para normalizarlo.",
        "- `python -m pytest -q` usa datos sintéticos y las pruebas de integración opcionales siguen su configuración.",
        f"- Resultado de pruebas proporcionado al generar el informe: {test_summary}",
        "- Archivos de código y documentación de la Etapa 1C: `catalog.py`, `models.py`, "
        "`normalizer.py`, `backfill.py`, `test_normalizer.py`, `test_backfill.py`, "
        "`docs/market-data-yahoo.md` y `docs/market-data-foundation.md`.",
        "- [Evidencia por símbolo, calendario y alcance de las reglas](../../docs/market-data-yahoo.md).",
        "- Los seis símbolos pueden normalizarse localmente. El repositorio PostgreSQL vigente "
        "solo habilita BTC-USD. Una fase posterior debe revisar el catálogo persistido y la verificación "
        "del repositorio antes de cargar los otros cinco símbolos.",
        "",
    ])
    return "\n".join(lines), attention


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end-exclusive", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--offline", action="store_true", help="Reprocess only local archives and export normalized data")
    parser.add_argument("--test-summary", default="no registrado")
    args = parser.parse_args(argv)
    try:
        window = DateRange(args.start, args.end_exclusive)
    except ValueError as error:
        parser.error(str(error))

    adapter = None if args.offline else YahooAdapter()
    results = []
    try:
        for mapping in ASSET_SOURCES.values():
            result = backfill_asset(adapter, args.output, mapping, window)
            results.append(result)
            print(f"{mapping.symbol}: {len(result.batches)} lotes, {result.cache_hits} en caché, "
                  f"{result.requests} intentos, {len(result.failures)} fallos", flush=True)
        report, attention = render_report(results, window, args.test_summary, offline=args.offline)
        if args.offline:
            report += "\n## Archivos normalizados locales\n\n"
            if attention:
                report += "El reproceso detectó problemas de cobertura o duplicados. La exportación no se ejecutó.\n"
            else:
                for result in results:
                    export = export_normalized(result, args.output, window)
                    relative = export.relative_to(args.output).as_posix()
                    report += f"- {result.mapping.symbol} [{relative}]({relative}) · {export.stat().st_size:,} bytes.\n"
            report += (
                "\nLa exportación distingue barras aceptadas y rechazos con índice de fila (base cero), "
                "epoch original, versión, procedencia y SHA-256 del gzip crudo asociado. "
                "Los importes Decimal se escriben como cadenas exactas.\n"
            )
        report_path = args.output / ("report-1c.md" if args.offline else "report.md")
        _write_atomic(report_path, report.encode("utf-8"))
    except YahooAdapterError as error:
        print(f"Backfill failed: {error.code}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Backfill failed: {error}", file=sys.stderr)
        return 2
    print(report_path)
    return int(attention)


if __name__ == "__main__":
    raise SystemExit(main())
