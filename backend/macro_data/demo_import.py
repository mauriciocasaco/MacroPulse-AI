"""Capture reviewed official data and load its sanitized snapshot into DEMO.

Validation is offline. Database writes require a separate explicit --apply
invocation and never read provider endpoints or development configuration.
"""

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import tuple_row
from psycopg.types.multirange import DateMultirange
from psycopg.types.range import DateRange
from pydantic import BaseModel, ConfigDict, Field

from backend.macro_ai.models import EVENT_WINDOWS
from backend.market_data.dev_import import DEV_PROJECT_REF
from backend.public_demo import DemoConfigurationError, tls_dsn

from .api import MacroObservation, _observation
from .contracts import CATALOG, Indicator
from .ingest import MAX_RANGE_DAYS, PROVIDERS
from .public import ProjectionError, verify_projection_integrity
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
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected a regular DEMO capture file")
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
    if len(rows) > 5500 or any(
        row.value is not None and row.value.as_tuple().exponent < -80 for row in rows
    ):
        raise ValueError("DEMO observations exceed the projection storage limits")
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
    (folder / "manifest.json").write_bytes(manifest.model_dump_json(indent=2).encode("utf-8") + b"\n")


def _unique_manifest_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key in DEMO manifest")
        result[key] = value
    return result


def read_bundle(folder: Path, *, expected_sha256: str | None = None) -> tuple[Manifest, str, tuple[Publication, ...]]:
    raw = _body(folder / "manifest.json", 16_384)
    digest = sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("DEMO manifest differs from the reviewed SHA-256")
    manifest = Manifest.model_validate(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_manifest_keys))
    _validate_window(manifest.start, manifest.end)
    if manifest.captures.keys() != CATALOG.keys():
        raise ValueError("Expected all five reviewed DEMO captures")
    publications = tuple(_public_rows(
        item, manifest.captures[item.id], _body(folder / f"{item.id}.response", MAX_BYTES),
        manifest.start, manifest.end,
    ) for item in CATALOG.values())
    return manifest, digest, publications


def projection_hashes(manifest: Manifest, digest: str, publications: tuple[Publication, ...]) -> dict[str, str]:
    """Compute the Phase 8.1 projection fingerprints offline from validated values."""
    fingerprints = {}
    for publication in publications:
        entry = publication.entry
        captured_at = entry.captured_at.astimezone(timezone.utc).isoformat(timespec="microseconds").removesuffix("+00:00")
        coverage = None if publication.coverage is None else [
            # The validated source ranges are inclusive; PostgreSQL's date
            # multirange stores a half-open upper bound.
            [span.lower.isoformat(), (span.upper + timedelta(days=1)).isoformat()]
            for span in publication.coverage
        ]
        rows = []
        for row in publication.rows:
            value = row.value
            if value is not None and value.is_zero():
                value = value.copy_abs()  # PostgreSQL numeric has no signed zero.
            rows.append([
                row.period_raw, row.period_start.isoformat(), row.period_end.isoformat(),
                row.effective_date.isoformat() if row.effective_date is not None else None,
                format(value, "f") if value is not None else None, row.status_kind, row.status_raw,
            ])
        # The public reader hashes the PostgreSQL jsonb array text. All fields
        # are fixed ASCII metadata or validated dates, statuses and numbers.
        canonical = json.dumps([
            publication.item.id, publication.item.series_key, captured_at,
            manifest.start.isoformat(), manifest.end.isoformat(), entry.sha256, digest, coverage, rows,
        ], ensure_ascii=False)
        fingerprints[publication.item.id] = sha256(canonical.encode("utf-8")).hexdigest()
    return fingerprints


def _shared_admin_dsn(value: str, ref: str, ca: str) -> str:
    if ref != DEV_PROJECT_REF:
        raise ValueError("Expected the approved shared Supabase project")
    options = conninfo_to_dict(value)
    if options.keys() != {"host", "port", "user", "password", "dbname"}:
        raise ValueError("DEMO admin DSN must contain only the required connection fields")
    host, user, password = options["host"], options["user"], options["password"]
    direct = host == f"db.{ref}.supabase.co" and user == "postgres"
    session_pooler = (
        re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", host)
        and user == f"postgres.{ref}"
    )
    if (not (direct or session_pooler) or options["port"] != "5432" or options["dbname"] != "postgres"
            or len(password) < 24 or len(set(password)) < 12
            or re.search(r"example|placeholder|replace|changeme|password|dummy|sample", password, re.I)):
        raise ValueError("Invalid shared DEMO administrative route")
    return tls_dsn(options, ca)


def _verify_admin(connection: psycopg.Connection, expected_target: tuple[str, str] | None) -> None:
    with connection.cursor(row_factory=tuple_row) as cursor:
        guard = cursor.execute("""
            SELECT current_user = session_user AND current_user <> 'marketpulse_demo_ro'
                AND current_setting('session_replication_role') = 'origin'
                AND n.nspowner = current_user::regrole AND p.proowner = n.nspowner
                AND p.prokind = 'f' AND NOT p.prosecdef
                AND p.prorettype = 'void'::regtype AND p.provolatile = 's'
                AND p.proconfig = ARRAY['search_path=pg_catalog'],
                current_user::text, current_database()::text
            FROM pg_namespace n JOIN pg_proc p ON p.pronamespace = n.oid
            WHERE n.nspname = 'marketpulse_demo' AND p.proname = 'assert_isolated' AND p.pronargs = 0
        """).fetchone()
    if guard is None or guard[0] is not True:
        raise ValueError("Expected the owner-controlled shared DEMO verifier and origin trigger mode")
    if expected_target is not None and guard[1:] != expected_target:
        raise ValueError("Unexpected effective DEMO administrative identity or database")


def _verify_stored_snapshot(connection: psycopg.Connection) -> None:
    # The Phase 8.1 fingerprints aggregate rows by catalog indicator. Check
    # independently for orphans even if a privileged session bypassed the FK.
    with connection.cursor(row_factory=tuple_row) as cursor:
        orphaned = cursor.execute("""
            SELECT EXISTS (
                SELECT FROM ONLY marketpulse_demo.macro_observations o
                WHERE NOT EXISTS (
                    SELECT FROM ONLY marketpulse_demo.macro_catalog c WHERE c.indicator_id = o.indicator_id
                )
            )
        """).fetchone()
    if orphaned != (False,):
        raise ProjectionError("DEMO projection contains observations without a catalog indicator")
    verify_projection_integrity(connection)


def apply_bundle(connection: psycopg.Connection, manifest: Manifest, digest: str,
                 publications: tuple[Publication, ...], *, restore_from: str | None = None,
                 expected_target: tuple[str, str] | None = None) -> bool:
    if not connection.pgconn.ssl_in_use or connection.info.get_parameters().get("sslmode") != "verify-full":
        raise ValueError("DEMO import requires TLS verify-full")
    if (not _DIGEST.fullmatch(digest) or (restore_from is not None and not _DIGEST.fullmatch(restore_from))
            or tuple(row.item for row in publications) != tuple(CATALOG.values())
            or any(row.entry != manifest.captures.get(row.item.id) for row in publications)):
        raise ValueError("Invalid validated DEMO snapshot")
    fingerprints = projection_hashes(manifest, digest, publications)
    with connection.transaction():
        connection.execute("SET LOCAL search_path = pg_catalog")
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL transaction_timeout = '60s'")
        _verify_admin(connection, expected_target)
        connection.execute("""
            LOCK TABLE marketpulse_demo.macro_catalog, marketpulse_demo.macro_observations
            IN SHARE ROW EXCLUSIVE MODE
        """)
        _verify_admin(connection, expected_target)
        connection.execute("SELECT marketpulse_demo.assert_isolated()")
        with connection.cursor(row_factory=tuple_row) as cursor:
            catalog_rows = cursor.execute("""
                SELECT indicator_id, captured_at, bundle_sha256, projection_sha256
                FROM ONLY marketpulse_demo.macro_catalog
            """).fetchall()
        existing = {key: (stamp, bundle_hash, projection_hash) for key, stamp, bundle_hash, projection_hash in catalog_rows}
        same_snapshot = len(existing) == len(CATALOG) and all(
            existing.get(row.item.id, ())[:2] == (row.entry.captured_at, digest) for row in publications
        )
        if restore_from is not None and (
            len(existing) != len(CATALOG) or {bundle_hash for _, bundle_hash, _ in existing.values()} != {restore_from}
        ):
            raise ValueError("DEMO snapshot changed before rollback")
        if same_snapshot and {key: row[2] for key, row in existing.items()} == fingerprints:
            try:
                _verify_stored_snapshot(connection)
            except ProjectionError:
                # The exact approved bundle can repair drift under the writer lock.
                pass
            else:
                return False
        if not same_snapshot and restore_from is None and any(
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
                                manifest.end, row.entry.sha256, digest, row.coverage, len(row.rows),
                                fingerprints[row.item.id]))
        with connection.cursor() as cursor, cursor.copy("""
            COPY marketpulse_demo.macro_observations (
                indicator_id, period_raw, period_start, period_end, effective_date, value_decimal, status_kind, status_raw
            ) FROM STDIN
        """) as copy:
            for publication in publications:
                for row in publication.rows:
                    copy.write_row((publication.item.id, row.period_raw, row.period_start, row.period_end,
                                    row.effective_date, row.value, row.status_kind, row.status_raw))
        _verify_stored_snapshot(connection)
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
    load.add_argument("--expected-sha256", help="SHA-256 of the separately reviewed manifest")
    load.add_argument("--restore-from", help="Restore a reviewed bundle if DEMO currently has this exact SHA-256")
    args = parser.parse_args()
    if args.command == "import":
        if args.restore_from is not None and (not args.apply or not _DIGEST.fullmatch(args.restore_from)):
            parser.error("--restore-from requires --apply and the SHA-256 of the current snapshot")
        if args.expected_sha256 is not None and not _DIGEST.fullmatch(args.expected_sha256):
            parser.error("--expected-sha256 requires a lowercase SHA-256 digest")
        if args.apply and args.expected_sha256 is None:
            parser.error("--apply requires --expected-sha256 with the reviewed manifest SHA-256")
    try:
        if args.command == "capture":
            capture_bundle(args.folder, args.start, args.end)
        manifest, digest, publications = read_bundle(
            args.folder, expected_sha256=args.expected_sha256 if args.command == "import" else None
        )
        if args.command == "import" and args.apply:
            ref = os.environ.get("MARKETPULSE_DEMO_PROJECT_REF", "")
            if not ref or args.project_ref != ref or any(key.upper().startswith("PG") for key in os.environ):
                raise ValueError("Verify the explicit DEMO project and clean libpq environment")
            dsn = _shared_admin_dsn(
                os.environ.get("MARKETPULSE_DEMO_ADMIN_DSN", ""), ref,
                os.environ.get("MARKETPULSE_DEMO_SSL_ROOT_CERT", ""),
            )
            with psycopg.connect(dsn) as connection:
                applied = apply_bundle(connection, manifest, digest, publications, restore_from=args.restore_from,
                                       expected_target=("postgres", "postgres"))
            print("DEMO snapshot applied" if applied else "DEMO snapshot already current")
        else:
            print("Validated offline. Database untouched.")
    except (DemoConfigurationError, psycopg.Error, OSError, RuntimeError, ValueError, TypeError, KeyError,
            AttributeError, OverflowError, csv.Error):
        print("DEMO load failed. Check capture provenance, coverage, TLS, and the independently verified target.")
        return 1
    print(f"SHA-256 {digest}. Window {manifest.start} to {manifest.end}.")
    fingerprints = projection_hashes(manifest, digest, publications)
    for row in publications:
        print(f"{row.item.id}: {len(row.rows)} rows, {row.item.unit}, multiplier {row.item.multiplier}, "
              f"captured {row.entry.captured_at.isoformat()}, projection SHA-256 {fingerprints[row.item.id]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
