"""Validate Yahoo archives and explicitly opt in to an approved PostgreSQL load."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import gzip
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from .backfill import (
    DEFAULT_END, DEFAULT_START, FORMAT_VERSION, NORMALIZED_FORMAT_VERSION,
    _decode_raw, _normalized_json_value, _normalized_records,
)
from . import dev_import
from .catalog import ASSETS, ASSET_SOURCES
from .dev_import import DEV_PROJECT_REF, DevImportError
from .models import AssetSource, DateRange, NormalizedBatch, PriceBar1d, RejectedObservation, RunSummary
from .normalizer import normalize

if TYPE_CHECKING:
    from .repository import PriceRepository


_MAX_FILE_BYTES = 64 * 1024 * 1024
_NUMERIC_FIELDS = ("open", "high", "low", "close", "adjusted_close", "volume")
_INITIAL_COUNTS = {"GC=F": 1761, "CL=F": 1760, "^GSPC": 1758, "VT": 1758, "VWO": 1758, "BTC-USD": 2557}
_INITIAL_REJECTIONS = {
    ("GC=F", "2025-05-26", "missing_close"),
    ("GC=F", "2025-06-19", "missing_close"),
    ("CL=F", "2023-11-23", "missing_close"),
    ("CL=F", "2025-05-26", "missing_close"),
    ("CL=F", "2025-06-19", "missing_close"),
    ("BTC-USD", "2026-11-08", "outside_requested_range"),
}


class ImportValidationError(ValueError):
    """An input set failed validation before any write."""


class ImportWriteError(RuntimeError):
    """Persistence stopped. Previously committed batches can be replayed."""


@dataclass(frozen=True)
class ArchivedRejection:
    position: int
    label: int
    local_date: date
    code: str


@dataclass(frozen=True)
class PreparedBatch:
    mapping: AssetSource
    window: DateRange
    raw_file: str
    raw_sha256: str
    batch: NormalizedBatch
    rejected: tuple[ArchivedRejection, ...]


@dataclass(frozen=True)
class PreparedExport:
    mapping: AssetSource
    window: DateRange
    batches: tuple[PreparedBatch, ...]


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ImportValidationError("duplicate JSON key")
    return obj


def _read_gzip(path: Path) -> bytes:
    with path.open("rb") as source:
        content = source.read(_MAX_FILE_BYTES + 1)
    if len(content) > _MAX_FILE_BYTES or not content.startswith(b"\x1f\x8b"):
        raise ImportValidationError("invalid or oversized gzip file")
    return content


def _parse_gzip(content: bytes) -> dict:
    with gzip.GzipFile(fileobj=BytesIO(content)) as source:
        decoded = source.read(_MAX_FILE_BYTES + 1)
    if len(decoded) > _MAX_FILE_BYTES:
        raise ImportValidationError("oversized decompressed JSON")
    payload = json.loads(decoded.decode("utf-8"), object_pairs_hook=_json_object)
    if not isinstance(payload, dict):
        raise ImportValidationError("expected a JSON object")
    return payload


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, default=_normalized_json_value, sort_keys=True, allow_nan=False)


def _window(record: dict) -> DateRange:
    return DateRange(date.fromisoformat(record["requested_start"]), date.fromisoformat(record["requested_end_exclusive"]))


def _bar_from_export(record: dict) -> PriceBar1d:
    fields = dict(record)
    for key in _NUMERIC_FIELDS:
        if fields[key] is not None:
            fields[key] = Decimal(fields[key])
    fields["bar_date"] = date.fromisoformat(fields["bar_date"])
    fields["retrieved_at"] = datetime.fromisoformat(fields["retrieved_at"])
    # V1's reviewed Yahoo conventions leave official trade, settlement and availability unset.
    return PriceBar1d(**fields)


def _prepare_batch(record: dict, mapping: AssetSource, outer: DateRange, archive_dir: Path) -> PreparedBatch:
    provenance = record["provenance"]
    window = _window(provenance)
    if window.start < outer.start or window.end_exclusive > outer.end_exclusive:
        raise ImportValidationError("batch window is outside the export window")
    relative = f"{mapping.asset_id}/{window.start}_{window.end_exclusive}.json.gz"
    if record["raw_file"] != relative:
        raise ImportValidationError("unexpected raw archive path")
    path = (archive_dir / relative).resolve()
    if not path.is_relative_to(archive_dir):
        raise ImportValidationError("raw archive path escapes its directory")
    content = _read_gzip(path)
    checksum = sha256(content).hexdigest()
    if record["raw_sha256"] != checksum:
        raise ImportValidationError("raw archive SHA-256 mismatch")
    raw_payload = _parse_gzip(content)
    if type(raw_payload.get("format_version")) is not int or raw_payload["format_version"] != FORMAT_VERSION:
        raise ImportValidationError("unsupported raw format version")
    raw = _decode_raw(raw_payload)
    if not isinstance(raw.adapter_version, str) or not raw.adapter_version.strip():
        raise ImportValidationError("missing adapter version")
    if raw.provider_version is not None and (
        not isinstance(raw.provider_version, str) or not raw.provider_version.strip()
    ):
        raise ImportValidationError("invalid provider version")
    if not isinstance(raw_payload["warnings"], list) or any(not isinstance(warning, str) for warning in raw.warnings):
        raise ImportValidationError("invalid archive warnings")
    checked = normalize(raw, ASSETS[mapping.asset_id], mapping, window)
    if checked.provenance["temporal_convention_status"] == "unverified":
        raise ImportValidationError("unverified archive date convention")
    accepted, rejected = _normalized_records(raw, checked)
    expected = {
        "raw_file": relative, "raw_sha256": checksum, "provenance": dict(checked.provenance),
        "warnings": checked.warnings, "accepted": accepted, "rejected": rejected,
    }
    if _canonical_json(record) != _canonical_json(expected):
        raise ImportValidationError("exported batch differs from the verified raw archive")

    # Reconstruct the persisted models from the normalized export after checking
    # every field and raw position, including the records withheld by normalization.
    bars = tuple(_bar_from_export(item["bar"]) for item in record["accepted"])
    rejected_rows = tuple(
        ArchivedRejection(item["raw_position"], item["original_label"], date.fromisoformat(item["provider_local_date"]), item["code"])
        for item in record["rejected"]
    )
    typed_provenance = {
        **provenance, "requested_start": window.start, "requested_end_exclusive": window.end_exclusive,
        "retrieved_at": datetime.fromisoformat(provenance["retrieved_at"]),
        "adapter_warnings": tuple(provenance["adapter_warnings"]), "raw_file": relative, "raw_sha256": checksum,
    }
    warnings = (
        *record["warnings"], f"archive:{relative}:sha256:{checksum}",
        *(f"archive_rejection:{row.position}:{row.label}:{row.local_date}:{row.code}" for row in rejected_rows),
    )
    batch = NormalizedBatch(
        bars, tuple(RejectedObservation(row.position, row.code) for row in rejected_rows),
        warnings, typed_provenance, len(raw.rows),
    )
    return PreparedBatch(mapping, window, relative, checksum, batch, rejected_rows)


def _prepare_export(path: Path, archive_dir: Path, mapping: AssetSource) -> PreparedExport:
    payload = _parse_gzip(_read_gzip(path))
    if type(payload.get("format_version")) is not int or payload["format_version"] != NORMALIZED_FORMAT_VERSION:
        raise ImportValidationError("unsupported normalized format version")
    if payload.keys() != {"format_version", "mapping", "requested_start", "requested_end_exclusive", "batches"}:
        raise ImportValidationError("invalid normalized export envelope")
    if payload["mapping"] != asdict(mapping):
        raise ImportValidationError("export mapping does not match the catalog")
    window = _window(payload)
    records = payload["batches"]
    if not isinstance(records, list) or not records:
        raise ImportValidationError("export has no batches")
    batches = tuple(_prepare_batch(record, mapping, window, archive_dir) for record in records)
    if len({batch.raw_file for batch in batches}) != len(batches):
        raise ImportValidationError("duplicate raw batch reference")
    seen: dict[date, tuple] = {}
    for prepared in batches:
        for bar in prepared.batch.bars:
            content = (
                bar.open, bar.high, bar.low, bar.close, bar.adjusted_close, bar.adjusted_close_basis,
                bar.volume, bar.source_convention, bar.session_status, bar.quality_status,
            )
            if seen.setdefault(bar.bar_date, content) != content:
                raise ImportValidationError("contradictory duplicate bar date")
    return PreparedExport(mapping, window, batches)


def prepare_import(input_dir: Path, archive_dir: Path, *, initial_dataset: bool = False) -> tuple[PreparedExport, ...]:
    """Read and validate the complete input set without creating a repository."""
    input_dir, archive_dir = input_dir.resolve(), archive_dir.resolve()
    paths = sorted(input_dir.glob("*.json.gz"))
    if not paths:
        raise ImportValidationError("no normalized exports found")
    exports = []
    for path in paths:
        asset_id = path.name.removesuffix(".json.gz")
        mapping = ASSET_SOURCES.get(asset_id)
        if mapping is None or path.resolve().parent != input_dir:
            raise ImportValidationError("unexpected normalized export in input directory")
        try:
            exports.append(_prepare_export(path, archive_dir, mapping))
        except ImportValidationError as error:
            raise ImportValidationError(f"{mapping.symbol}: {error}") from None
        except (OSError, EOFError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
            raise ImportValidationError(f"{mapping.symbol}: unreadable or invalid archive ({type(error).__name__})") from None
    if initial_dataset:
        _check_initial_dataset(exports)
    return tuple(exports)


def _check_initial_dataset(exports: list[PreparedExport]) -> None:
    counts = {}
    rejections = []
    initial_window = DateRange(DEFAULT_START, DEFAULT_END)
    for export in exports:
        if export.window != initial_window:
            raise ImportValidationError("unexpected initial dataset window")
        dates = [bar.bar_date for batch in export.batches for bar in batch.batch.bars]
        if len(dates) != len(set(dates)):
            raise ImportValidationError("duplicate bar date in initial dataset")
        counts[export.mapping.symbol] = len(dates)
        rejections.extend(
            (export.mapping.symbol, row.local_date.isoformat(), row.code)
            for batch in export.batches for row in batch.rejected
        )
    if counts != _INITIAL_COUNTS or sorted(rejections) != sorted(_INITIAL_REJECTIONS):
        raise ImportValidationError("initial dataset counts or rejection inventory differ")


def persist_import(
    exports: tuple[PreparedExport, ...], repository: PriceRepository, *, write: bool = False,
) -> Iterator[RunSummary]:
    """Write one transaction per archived batch using an explicitly supplied repository."""
    if not write or not exports:
        raise ImportWriteError("explicit write authorization and a prepared input set are required")
    try:
        mapping_ids = {item.mapping: repository.resolve_mapping_id(item.mapping) for item in exports}
    except Exception:
        raise ImportWriteError("destination mapping preflight failed") from None
    for export in exports:
        for prepared in export.batches:
            run_id = None
            try:
                run_id = repository.start_run(mapping_ids[export.mapping], prepared.window)
                summary = repository.save_batch(run_id, prepared.batch)
            except KeyboardInterrupt:
                if run_id is not None:
                    try:
                        repository.fail_run(run_id, "import_interrupted")
                    except Exception:
                        # The run may already be finalized or the connection may be unavailable.
                        pass
                raise
            except Exception as error:
                raise ImportWriteError(
                    f"{prepared.raw_file}: persistence interrupted (run={run_id}, {type(error).__name__})"
                ) from None
            yield summary
            classified = sum((
                summary.inserted_count, summary.unchanged_count, summary.corrected_count,
                summary.stale_count, summary.rejected_count,
            ))
            if (
                summary.status not in ("succeeded", "partial")
                or classified != summary.received_count
                or classified != prepared.batch.received_count
                or summary.rejected_count != len(prepared.rejected)
            ):
                raise ImportWriteError(f"{prepared.raw_file}: unexpected reconciliation (run={run_id})")


def _local_dsn(dsn: str) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    try:
        params = conninfo_to_dict(dsn)
    except Exception:
        raise ImportWriteError("invalid local PostgreSQL configuration") from None
    required = {"host", "port", "dbname", "user"}
    if (
        not required <= params.keys() or params.keys() - required - {"password"}
        or params["host"] != "127.0.0.1" or len(params["port"]) > 5
        or not params["port"].isascii() or not params["port"].isdigit()
        or not 1 <= int(params["port"]) <= 65535 or not params["dbname"] or not params["user"]
        or os.environ.get("PGSERVICE")
    ):
        raise ImportWriteError("local writes require a single explicit 127.0.0.1 host, port, database and user")
    return make_conninfo(**params, hostaddr="127.0.0.1", connect_timeout=5, sslmode="disable", gssencmode="disable")


def _report(exports: tuple[PreparedExport, ...]) -> None:
    total, rejected_total = 0, 0
    for export in exports:
        dates = [bar.bar_date for batch in export.batches for bar in batch.batch.bars]
        rejected = sum(len(batch.rejected) for batch in export.batches)
        coverage = f"{min(dates)} to {max(dates)}" if dates else "empty"
        print(f"{export.mapping.symbol}  {len(dates)} accepted / {rejected} rejected  {coverage}")
        print(f"  {export.mapping.source_id}  {export.mapping.source_convention}"
              f"  [{export.window.start}, {export.window.end_exclusive})")
        for batch in export.batches:
            provenance = batch.batch.provenance
            print(f"  {batch.raw_file}  SHA-256 {batch.raw_sha256}")
            print(f"  [{batch.window.start}, {batch.window.end_exclusive})  retrieved {provenance['retrieved_at'].isoformat()}")
            for row in batch.rejected:
                print(f"  rejected raw position {row.position}  label {row.label}  {row.local_date}  {row.code}")
        total += len(dates)
        rejected_total += rejected
    print(f"READY  {len(exports)} exports  {total} accepted  {rejected_total} rejected")


def _write_exports(exports: tuple[PreparedExport, ...], repository: PriceRepository) -> None:
    for run in persist_import(exports, repository, write=True):
        print(f"run={run.run_id} status={run.status} received={run.received_count} inserted={run.inserted_count}"
              f" unchanged={run.unchanged_count} corrected={run.corrected_count}"
              f" stale={run.stale_count} rejected={run.rejected_count}")


def _report_dev(dsn: str, exports: tuple[PreparedExport, ...], *, require_complete: bool = False) -> None:
    result = dev_import.preflight_dev(dsn, exports, require_complete=require_complete)
    print("DEV PREFLIGHT PASS  verified TLS  migrations " + ", ".join(result.versions))
    for symbol, count in result.bars_by_symbol.items():
        print(f"  {symbol}  {count} stored bars matching the archives")
    print(f"  {sum(result.runs_by_status.values())} ingestion runs", result.runs_by_status)
    for run_id, status, symbol, error_code in result.unresolved_runs:
        print(f"  inspect run={run_id} status={status} symbol={symbol} error={error_code or '-'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True, help="directory of normalized .json.gz exports")
    parser.add_argument("--archive-dir", type=Path, required=True, help="backfill root containing raw archives")
    parser.add_argument("--initial-dataset", action="store_true", help="verify the reviewed six-asset 2019–2026 dataset")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate without connecting (default)")
    mode.add_argument("--apply-local", action="store_true", help="write to loopback using MARKET_DATA_IMPORT_DSN")
    mode.add_argument("--preflight-dev", action="store_true", help="inspect the authorized Supabase DEV destination read-only")
    mode.add_argument("--apply-dev", action="store_true", help="load the reviewed initial dataset into Supabase DEV")
    mode.add_argument("--migrate-dev-incremental", action="store_true", help="apply only the guarded DEV incremental")
    parser.add_argument("--confirm-dev-write", metavar="PROJECT_REF", help="explicit confirmation for a DEV write mode")
    parser.add_argument("--require-complete", action="store_true", help="require all archive bars in a DEV preflight")
    args = parser.parse_args(argv)
    dev_write = args.apply_dev or args.migrate_dev_incremental
    if dev_write and args.confirm_dev_write != DEV_PROJECT_REF:
        parser.error("DEV writes require --confirm-dev-write with the authorized project reference")
    if not dev_write and args.confirm_dev_write is not None:
        parser.error("--confirm-dev-write requires an explicit DEV write mode")
    if args.require_complete and not args.preflight_dev:
        parser.error("--require-complete requires --preflight-dev")
    try:
        exports = prepare_import(
            args.input_dir, args.archive_dir, initial_dataset=args.initial_dataset or dev_write or args.preflight_dev,
        )
        _report(exports)
        if args.apply_local:
            from .repository import PostgresPriceRepository

            repository = PostgresPriceRepository(_local_dsn(os.environ.get("MARKET_DATA_IMPORT_DSN", "")))
            _write_exports(exports, repository)
        elif dev_write or args.preflight_dev:
            dsn = dev_import.configured_dev_dsn()
            if args.migrate_dev_incremental:
                dev_import.migrate_dev_incremental(dsn, exports)
                _report_dev(dsn, exports)
            elif args.apply_dev:
                from .repository import PostgresPriceRepository

                _report_dev(dsn, exports)
                _write_exports(exports, PostgresPriceRepository(dsn, connect=dev_import.connect_dev))
                _report_dev(dsn, exports, require_complete=True)
            else:
                _report_dev(dsn, exports, require_complete=args.require_complete)
        else:
            print("DRY RUN  no database connection")
    except (ImportValidationError, ImportWriteError, DevImportError) as error:
        print(f"ERROR  {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("INTERRUPTED  rerun with the same verified archives to resume", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
