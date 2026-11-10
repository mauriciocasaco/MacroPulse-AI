"""Run the approved macro ingestion without touching other schemas."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
import csv
from datetime import date, datetime, timezone
from decimal import DecimalException
import os
import re
from uuid import uuid4
from xml.etree.ElementTree import ParseError

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.pq import TransactionStatus

from . import ecb, eurostat
from .contracts import CATALOG
from .repository import Capture, IngestionResult, record_capture, register_catalog
from .transport import FetchError, Response, fetch


PROVIDERS = {"ecb": ecb, "eurostat": eurostat}
MAX_RANGE_DAYS = 366 * 15


def run_ingestion(
    connection: psycopg.Connection, start: date, end: date, *,
    ids: Iterable[str] | None = None, transport: Callable[[str, str], Response] = fetch,
    ecb_history: bool = False,
) -> tuple[IngestionResult, ...]:
    """Validate and commit each source response independently."""
    selection = tuple(CATALOG if ids is None else ids)
    if not selection or len(set(selection)) != len(selection) or any(key not in CATALOG for key in selection):
        raise ValueError("invalid macro indicator selection")
    if end < start or (end - start).days > MAX_RANGE_DAYS:
        raise ValueError("invalid macro date range")
    if ecb_history and (len(selection) != 1 or CATALOG[selection[0]].provider != "ecb" or (end - start).days > 366):
        raise ValueError("historical versions require one ECB indicator and a bounded interval")
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError("macro ingestion requires an idle database connection")
    register_catalog(connection)
    results = []
    for key in selection:
        item = CATALOG[key]
        provider = PROVIDERS[item.provider]
        url = ecb.request_url(item, start, end, history=True) if ecb_history else provider.request_url(item, start, end)
        accept = ecb.HISTORY_ACCEPT if ecb_history else provider.ACCEPT
        parse = ecb.parse_history if ecb_history else provider.parse
        capture_id = uuid4()
        try:
            response = transport(url, accept)
        except FetchError as error:
            capture = Capture(capture_id, start, end, datetime.now(timezone.utc), url, error.status,
                              error.attempts, error_code=error.code)
            results.append(record_capture(connection, item, capture))
            continue
        received_at = datetime.now(timezone.utc)
        error_code = None
        attributes = {}
        observations = ()
        if response.status != 200:
            error_code = "unexpected_http_status"
        else:
            try:
                parsed, attributes = parse(item, response)
                identities = {(row.period.raw, row.provider_valid_from, row.provider_action) for row in parsed}
                if (any(row.period.end < start or row.period.start > end for row in parsed)
                        or len(identities) != len(parsed)):
                    raise ValueError("out-of-range or duplicate observation")
                observations = parsed
            except (ValueError, TypeError, KeyError, AttributeError, IndexError, DecimalException,
                    csv.Error, ParseError, RecursionError, OverflowError):
                error_code = "invalid_provider_response"
        capture = Capture(capture_id, start, end, received_at, url, response.status, response.attempts,
                          response.body, error_code)
        results.append(record_capture(connection, item, capture, observations, attributes if not error_code else {}))
    return tuple(results)


def _local_dsn(value: str) -> str:
    options = conninfo_to_dict(value)
    host = options.get("host")
    dbname = options.get("dbname", "")
    if (host not in {"127.0.0.1", "::1"} or options.get("hostaddr", host) != host
            or not options.get("port") or not re.fullmatch(r"(macropulse_m2_|phase3_test_)[a-z0-9_]+", dbname)
            or "service" in options or "options" in options):
        raise ValueError("use an explicitly named disposable loopback PostgreSQL database")
    return make_conninfo(value, hostaddr=host, connect_timeout=8)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest approved series into a pre-migrated disposable PostgreSQL database.")
    parser.add_argument("--start", default=date(2019, 1, 1), type=date.fromisoformat)
    parser.add_argument("--end", default=date.today(), type=date.fromisoformat)
    parser.add_argument("--indicator", action="append", choices=CATALOG)
    parser.add_argument("--ecb-history", action="store_true")
    args = parser.parse_args()
    try:
        dsn = _local_dsn(os.environ.get("MACRO_DATA_TEST_DSN", ""))
        with psycopg.connect(dsn) as connection:
            results = run_ingestion(connection, args.start, args.end, ids=args.indicator, ecb_history=args.ecb_history)
    except (psycopg.Error, ValueError, RuntimeError):
        print("Macro ingestion failed. Verify the disposable target, migration and arguments.")
        return 1
    for item in results:
        print(f"{item.indicator_id} {item.outcome} capture={item.capture_id} observations={item.observations} "
              f"revisions={item.new_revisions} stale={item.stale_observations} "
              f"http={item.http_status} attempts={item.attempts} error={item.error_code}")
    return int(any(item.outcome != "success" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
