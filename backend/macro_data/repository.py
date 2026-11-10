"""Transactional, append-only macro observation storage in PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .contracts import CATALOG, Indicator, Observation


@dataclass(frozen=True)
class Capture:
    id: UUID
    start: date
    end: date
    captured_at: datetime
    url: str
    http_status: int | None
    attempts: int
    body: bytes | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class IngestionResult:
    indicator_id: str
    outcome: str
    capture_id: str
    observations: int
    new_revisions: int
    stale_observations: int
    http_status: int | None
    attempts: int
    error_code: str | None


def register_catalog(connection: psycopg.Connection) -> None:
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '15s'")
        for item in CATALOG.values():
            values = (
                item.id, item.provider, item.dataset, item.series_key, 1, item.frequency, item.unit, item.multiplier,
                item.geography, item.adjustment, item.title, item.source_url, item.reuse_url, item.attribution,
            )
            connection.execute("""
                INSERT INTO macro_data.series (
                    indicator_id, provider, dataset, series_key, contract_version, frequency, unit, multiplier,
                    geography, adjustment, title, source_url, reuse_url, attribution, dimensions
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (indicator_id) DO NOTHING
            """, (*values, Jsonb(dict(item.dimensions))))
            stored = connection.execute("""
                SELECT indicator_id, provider, dataset, series_key, contract_version, frequency, unit, multiplier,
                       geography, adjustment, title, source_url, reuse_url, attribution, dimensions
                FROM macro_data.series WHERE indicator_id = %s
            """, (item.id,)).fetchone()
            if stored != (*values, dict(item.dimensions)):
                raise RuntimeError("stored macro catalog disagrees with the pinned contract")
        total = connection.execute("SELECT count(*) FROM macro_data.series").fetchone()[0]
        if total != len(CATALOG):
            raise RuntimeError("unexpected macro catalog entries")


def _fingerprint(item: Observation) -> str:
    metadata = {
        "raw": item.value_raw, "status": item.status_raw, "kind": item.status_kind,
        "confidentiality": item.confidentiality_raw, "attributes": item.attributes,
        "valid_to": item.provider_valid_to.isoformat() if item.provider_valid_to is not None else None,
    }
    return hashlib.sha256(json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def record_capture(
    connection: psycopg.Connection,
    indicator: Indicator,
    capture: Capture,
    observations: tuple[Observation, ...] = (),
    source_attributes: dict | None = None,
) -> IngestionResult:
    outcome = "failed" if capture.error_code else "success"
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '60s'")
        # Serialize reconciliation per indicator without holding locks while fetching.
        connection.execute(
            "SELECT indicator_id FROM macro_data.series WHERE indicator_id = %s FOR UPDATE", (indicator.id,),
        )
        replay = connection.execute("""
            SELECT indicator_id, outcome, observations, new_revisions, stale_observations,
                   http_status, attempts, error_code, response_sha256, requested_start, requested_end,
                   captured_at, request_url
            FROM macro_data.capture WHERE capture_id = %s
        """, (capture.id,)).fetchone()
        digest = hashlib.sha256(capture.body).hexdigest() if capture.body is not None else None
        if replay is not None:
            series, result, count, added, stale, status, attempts, error, previous_digest, start, end, stamp, url = replay
            if (series, result, status, attempts, error, previous_digest, start, end, stamp, url) != (
                indicator.id, outcome, capture.http_status, capture.attempts, capture.error_code, digest,
                capture.start, capture.end, capture.captured_at, capture.url,
            ):
                raise RuntimeError("capture identifier collision")
            return IngestionResult(series, result, str(capture.id), count, added, stale, status, attempts, error)

        connection.execute("""
            INSERT INTO macro_data.capture (
                capture_id, indicator_id, requested_start, requested_end, captured_at, request_url,
                outcome, http_status, attempts, error_code, response_sha256, response_body,
                source_attributes, observations, new_revisions, stale_observations
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0)
        """, (
            capture.id, indicator.id, capture.start, capture.end, capture.captured_at, capture.url, outcome,
            capture.http_status, capture.attempts, capture.error_code, digest,
            capture.body if outcome == "success" else None, Jsonb(source_attributes or {}), len(observations),
        ))
        added, stale = _reconcile(connection, indicator, capture, observations) if observations else (0, 0)
        if added or stale:
            connection.execute("""
                UPDATE macro_data.capture SET new_revisions = %s, stale_observations = %s WHERE capture_id = %s
            """, (added, stale, capture.id))
    return IngestionResult(
        indicator.id, outcome, str(capture.id), len(observations), added, stale, capture.http_status,
        capture.attempts, capture.error_code,
    )


def _reconcile(connection, indicator, capture, observations) -> tuple[int, int]:
    if observations[0].provider_valid_from is not None:
        return _store_provider_history(connection, indicator, capture, observations), 0
    rows = connection.execute("""
        SELECT DISTINCT ON (r.period_raw) r.period_raw, r.revision_id, r.observation_sha256, c.captured_at
        FROM macro_data.observation_revision AS r
        JOIN macro_data.revision_capture AS rc USING (revision_id, indicator_id)
        JOIN macro_data.capture AS c USING (capture_id, indicator_id)
        WHERE r.indicator_id = %s AND r.period_raw = ANY(%s) AND r.provider_valid_from IS NULL
        ORDER BY r.period_raw, c.captured_at DESC, r.revision_id DESC
    """, (indicator.id, [item.period.raw for item in observations])).fetchall()
    previous = {period: (revision, digest, captured_at) for period, revision, digest, captured_at in rows}
    changed, links = [], []
    stale = 0
    for item in observations:
        digest = _fingerprint(item)
        before = previous.get(item.period.raw)
        if before is not None and capture.captured_at < before[2]:
            stale += 1
        elif before is not None and before[1] == digest:
            links.append((before[0], capture.id, indicator.id))
        elif before is not None and before[2] == capture.captured_at:
            # Equal receipt times cannot establish order for conflicting values.
            stale += 1
        else:
            changed.append(_revision_values(indicator, capture, item, digest, before[0] if before else None))
    _insert_revisions_and_links(connection, indicator, capture, changed, links)
    return len(changed), stale


def _store_provider_history(connection, indicator, capture, observations) -> int:
    periods = sorted({item.period.raw for item in observations})
    versions = connection.execute("""
        SELECT period_raw, provider_valid_from, provider_action, observation_sha256, revision_id
        FROM macro_data.observation_revision
        WHERE indicator_id = %s AND period_raw = ANY(%s) AND provider_valid_from IS NOT NULL
    """, (indicator.id, periods)).fetchall()
    stored = {(period, stamp, action, digest): revision for period, stamp, action, digest, revision in versions}
    changed, links = [], []
    for item in observations:
        digest = _fingerprint(item)
        existing = stored.get((item.period.raw, item.provider_valid_from, item.provider_action, digest))
        if existing is None:
            changed.append(_revision_values(indicator, capture, item, digest, None))
        else:
            links.append((existing, capture.id, indicator.id))
    _insert_revisions_and_links(connection, indicator, capture, changed, links)
    return len(changed)


def _revision_values(indicator, capture, item, digest, supersedes) -> tuple:
    return (
        indicator.id, item.period.raw, item.period.start, item.period.end, item.period.effective_date,
        item.value, item.value_raw, item.status_raw, item.status_kind, item.confidentiality_raw,
        Jsonb(item.attributes), digest, item.provider_valid_from, item.provider_valid_to,
        item.provider_action, capture.id, supersedes,
    )


def _insert_revisions_and_links(connection, indicator, capture, changed, links) -> None:
    with connection.cursor() as cursor:
        if changed:
            cursor.executemany("""
                INSERT INTO macro_data.observation_revision (
                    indicator_id, period_raw, period_start, period_end, effective_date, value_decimal, value_raw,
                    status_raw, status_kind, confidentiality_raw, source_attributes, observation_sha256,
                    provider_valid_from, provider_valid_to, provider_action, created_capture_id, supersedes_revision_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING revision_id
            """, changed, returning=True)
            for _ in changed:
                links.append((cursor.fetchone()[0], capture.id, indicator.id))
                cursor.nextset()
        if links:
            cursor.executemany("""
                INSERT INTO macro_data.revision_capture (revision_id, capture_id, indicator_id) VALUES (%s, %s, %s)
            """, links)


def read_observations(
    connection: psycopg.Connection, indicator_id: str, *, known_at: datetime | None = None,
) -> list[dict]:
    """Read promoted snapshot revisions captured by the cutoff.

    Out-of-order receipts excluded during reconciliation remain in the raw
    capture log and do not contribute to this normalized timeline.
    """
    if indicator_id not in CATALOG:
        raise ValueError("unknown macro indicator")
    if known_at is not None and known_at.utcoffset() is None:
        raise ValueError("macro cutoff requires a timezone")
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute("""
            SELECT DISTINCT ON (r.period_start, r.period_raw)
                   r.*, c.captured_at AS last_seen_at, original.captured_at AS first_seen_at
            FROM macro_data.observation_revision AS r
            JOIN macro_data.revision_capture AS rc USING (revision_id, indicator_id)
            JOIN macro_data.capture AS c USING (capture_id, indicator_id)
            JOIN macro_data.capture AS original ON original.capture_id = r.created_capture_id
            WHERE r.indicator_id = %s AND r.provider_valid_from IS NULL
              AND (%s::timestamptz IS NULL OR c.captured_at <= %s)
            ORDER BY r.period_start, r.period_raw, c.captured_at DESC, r.revision_id DESC
        """, (indicator_id, known_at, known_at)).fetchall()
