"""Capture reviewed official data and load its sanitized snapshot into DEMO.

Validation is offline. Database writes require a separate explicit --apply
invocation and never read provider endpoints or development configuration.
"""

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.multirange import DateMultirange
from psycopg.types.range import DateRange
from pydantic import BaseModel, ConfigDict, Field

from backend.macro_ai.models import EVENT_WINDOWS
from backend.public_demo import DemoConfigurationError, demo_target, tls_dsn

from .api import MacroObservation, _observation
from .contracts import CATALOG, Indicator
from .ingest import MAX_RANGE_DAYS, PROVIDERS
from .public import ProjectionError, stamp_projection, verify_projection_integrity
from .transport import MAX_BYTES, Response, fetch

_FIRST_EVENT = min(start for start, _ in EVENT_WINDOWS.values())
_LAST_EVENT = max(end for _, end in EVENT_WINDOWS.values())
_BERLIN = ZoneInfo("Europe/Berlin")
_GAPS = {"daily": 14, "monthly": 95, "quarterly": 185}
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class CaptureEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    captured_at: datetime
    url: str = Field(max_length=512)
    content_type: str = Field(max_length=64)
    sha256: str = Field(pattern=_DIGEST)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date
    captures: dict[str, CaptureEntry]


@dataclass(frozen=True)
class Publication:
    item: Indicator
    entry: CaptureEntry
    rows: tuple[MacroObservation, ...]
    coverage: DateMultirange | None


def _body(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("DEMO capture exceeds the file limit")
    return data


def _validate_window(start: date, end: date) -> None:
    if (not date(1900, 1, 1) <= start <= _FIRST_EVENT <= _LAST_EVENT <= end
            or (end - start).days > MAX_RANGE_DAYS
            or end > datetime.now(_BERLIN).date()):
        raise ValueError("DEMO capture must include all public research event windows")


def _public_rows(item: Indicator, entry: CaptureEntry, body: bytes, start: date, end: date) -> Publication:
    adapter = PROVIDERS[item.provider]
    if (entry.url != adapter.request_url(item, start, end) or sha256(body).hexdigest() != entry.sha256
            or entry.captured_at.utcoffset() is None or entry.captured_at > datetime.now(timezone.utc)
            or end > entry.captured_at.astimezone(_BERLIN).date()):
        raise ValueError("DEMO capture provenance is invalid")
    parsed, _ = adapter.parse(item, Response(body, 200, entry.content_type, 1))
    rows = []
    seen = set()
    for source in parsed:
        period = source.period
        if period.start in seen or period.end < start or period.start > end:
            raise ValueError("Invalid or duplicate DEMO observation period")
        seen.add(period.start)
        row = _observation(item, {
            "period_raw": period.raw, "period_start": period.start, "period_end": period.end,
            "effective_date": period.effective_date, "value_decimal": source.value, "status_kind": source.status_kind,
            "status_raw": source.status_raw, "confidentiality_raw": source.confidentiality_raw,
        })
        if row.status_kind == "withheld" and source.value is not None:
            raise ValueError("DEMO capture contains a restricted observation")
        if row.status_kind == "forecast" or (
            row.value is None and period.end > entry.captured_at.astimezone(_BERLIN).date()
        ):
            continue
        if period.end > entry.captured_at.astimezone(_BERLIN).date():
            raise ValueError("DEMO observation is dated after capture")
        if row.status_kind == "unknown":
            row = row.model_copy(update={"value": None})
        rows.append(row)
    rows.sort(key=lambda row: row.period_start)
    publishable = [row for row in rows if row.value is not None]
    if not publishable or publishable[0].period_start > _FIRST_EVENT:
        raise ValueError("DEMO series does not cover the earliest research event")
    coverage = None
    if item.frequency == "effective_date":
        if len(rows) != len(publishable):
            raise ValueError("Deposit coverage contains a non-public or unavailable rate")
        # A verified daily-rate regime starts at the first actual effective-date
        # event in the capture, never at an unobserved requested date.
        coverage = DateMultirange([DateRange(publishable[0].period_start, end, "[]")])
    else:
        gap = _GAPS[item.frequency]
        if ((publishable[0].period_start - start).days > gap or (end - publishable[-1].period_end).days > gap
                or any((right.period_start - left.period_end).days > gap
                       for left, right in zip(publishable, publishable[1:]))):
            raise ValueError("DEMO observation coverage has a gap outside the approved tolerance")
    return Publication(item, entry, tuple(rows), coverage)


def capture_bundle(folder: Path, start: date, end: date, *, transport=fetch) -> None:
    _validate_window(start, end)
    folder.mkdir()
    captures = {}
    for item in CATALOG.values():
        adapter = PROVIDERS[item.provider]
        url = adapter.request_url(item, start, end)
        response = transport(url, adapter.ACCEPT)
        if response.status != 200 or len(response.body) > MAX_BYTES:
            raise ValueError("Official capture response is unavailable")
        entry = CaptureEntry(captured_at=datetime.now(timezone.utc), url=url, content_type=response.content_type,
                             sha256=sha256(response.body).hexdigest())
        _public_rows(item, entry, response.body, start, end)
        (folder / f"{item.id}.response").write_bytes(response.body)
        captures[item.id] = entry
    manifest = Manifest(start=start, end=end, captures=captures)
    (folder / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")


def read_bundle(folder: Path) -> tuple[Manifest, str, tuple[Publication, ...]]:
    raw = _body(folder / "manifest.json", 16_384)
    manifest = Manifest.model_validate_json(raw)
    _validate_window(manifest.start, manifest.end)
    if manifest.captures.keys() != CATALOG.keys():
        raise ValueError("Expected all five reviewed DEMO captures")
    publications = tuple(_public_rows(
        item, manifest.captures[item.id], _body(folder / f"{item.id}.response", MAX_BYTES),
        manifest.start, manifest.end,
    ) for item in CATALOG.values())
    return manifest, sha256(raw).hexdigest(), publications


def _verify_admin(connection: psycopg.Connection) -> None:
    guard = connection.execute("""
        SELECT current_database() = 'postgres' AND current_user = 'postgres'
            AND to_regnamespace('macro_data') IS NULL AND to_regnamespace('market_data') IS NULL
            AND to_regnamespace('news') IS NULL
            AND to_regclass('marketpulse_demo.synthetic_bars') IS NULL
            AND to_regclass('marketpulse_demo.macro_catalog') IS NOT NULL
            AND to_regclass('marketpulse_demo.macro_observations') IS NOT NULL
    """).fetchone()
    if guard != (True,):
        raise ValueError("Target is not a verified isolated DEMO schema")


def apply_bundle(connection: psycopg.Connection, manifest: Manifest, digest: str,
                 publications: tuple[Publication, ...], *, restore_from: str | None = None) -> bool:
    if not connection.pgconn.ssl_in_use:
        raise ValueError("DEMO import requires TLS")
    with connection.transaction():
        connection.execute("SET LOCAL search_path = pg_catalog")
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL transaction_timeout = '60s'")
        _verify_admin(connection)
        connection.execute("""
            LOCK TABLE marketpulse_demo.macro_catalog, marketpulse_demo.macro_observations
            IN SHARE ROW EXCLUSIVE MODE
        """)
        existing = {
            key: (stamp, fingerprint) for key, stamp, fingerprint in connection.execute("""
                SELECT indicator_id, captured_at, bundle_sha256 FROM ONLY marketpulse_demo.macro_catalog
            """)
        }
        same_snapshot = len(existing) == len(CATALOG) and all(
            existing.get(row.item.id) == (row.entry.captured_at, digest) for row in publications
        )
        if restore_from is not None and (
            len(existing) != len(CATALOG) or {fingerprint for _, fingerprint in existing.values()} != {restore_from}
        ):
            raise ValueError("DEMO snapshot changed before rollback")
        if same_snapshot:
            try:
                verify_projection_integrity(connection)
            except ProjectionError:
                # The exact approved bundle can repair drift under the writer lock.
                pass
            else:
                return False
        elif restore_from is None and any(
            row.item.id in existing and row.entry.captured_at <= existing[row.item.id][0] for row in publications
        ):
            raise ValueError("Refusing to replace DEMO data with a stale or conflicting capture")
        connection.execute("DELETE FROM ONLY marketpulse_demo.macro_observations")
        connection.execute("DELETE FROM ONLY marketpulse_demo.macro_catalog")
        with connection.cursor() as cursor, cursor.copy("""
            COPY marketpulse_demo.macro_catalog (
                indicator_id, series_key, captured_at, requested_start, requested_end,
                response_sha256, bundle_sha256, deposit_coverage, projected_rows, projection_sha256
            ) FROM STDIN
        """) as copy:
            for row in publications:
                copy.write_row((row.item.id, row.item.series_key, row.entry.captured_at, manifest.start,
                                manifest.end, row.entry.sha256, digest, row.coverage, len(row.rows), "0" * 64))
        with connection.cursor() as cursor, cursor.copy("""
            COPY marketpulse_demo.macro_observations (
                indicator_id, period_raw, period_start, period_end, effective_date, value_decimal, status_kind, status_raw
            ) FROM STDIN
        """) as copy:
            for publication in publications:
                for row in publication.rows:
                    copy.write_row((publication.item.id, row.period_raw, row.period_start, row.period_end,
                                    row.effective_date, row.value, row.status_kind, row.status_raw))
        stamp_projection(connection)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Fetch only the approved ECB/Eurostat routes")
    capture.add_argument("folder", type=Path)
    capture.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    capture.add_argument("--end", type=date.fromisoformat, default=datetime.now(_BERLIN).date())
    load = commands.add_parser("import", help="Validate a captured bundle offline by default")
    load.add_argument("folder", type=Path)
    load.add_argument("--apply", action="store_true")
    load.add_argument("--project-ref")
    load.add_argument("--restore-from", help="Restore a reviewed bundle if DEMO currently has this exact SHA-256")
    args = parser.parse_args()
    if args.command == "import" and args.restore_from and (not args.apply or not _DIGEST.fullmatch(args.restore_from)):
        parser.error("--restore-from requires --apply and the SHA-256 of the current snapshot")
    try:
        if args.command == "capture":
            capture_bundle(args.folder, args.start, args.end)
        manifest, digest, publications = read_bundle(args.folder)
        if args.command == "import" and args.apply:
            ref = os.environ.get("MARKETPULSE_DEMO_PROJECT_REF", "")
            if not ref or args.project_ref != ref or any(key.upper().startswith("PG") for key in os.environ):
                raise ValueError("Verify the explicit DEMO project and clean libpq environment")
            options = demo_target(os.environ.get("MARKETPULSE_DEMO_ADMIN_DSN", ""), ref, "postgres")
            dsn = tls_dsn(options, os.environ.get("MARKETPULSE_DEMO_SSL_ROOT_CERT", ""))
            with psycopg.connect(dsn) as connection:
                applied = apply_bundle(connection, manifest, digest, publications, restore_from=args.restore_from)
            print("DEMO snapshot applied" if applied else "DEMO snapshot already current")
        else:
            print("Validated offline. Database untouched.")
    except (DemoConfigurationError, psycopg.Error, OSError, RuntimeError, ValueError, TypeError, KeyError,
            AttributeError, OverflowError, csv.Error):
        print("DEMO load failed. Check capture provenance, coverage, TLS, and the independently verified target.")
        return 1
    print(f"SHA-256 {digest}. Window {manifest.start} to {manifest.end}.")
    for row in publications:
        print(f"{row.item.id}: {len(row.rows)} rows, {row.item.unit}, multiplier {row.item.multiplier}, "
              f"captured {row.entry.captured_at.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
