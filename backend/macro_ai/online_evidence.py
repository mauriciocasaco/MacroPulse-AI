"""Reviewed supplements to M7.B and live eligibility checks for news records."""

from hashlib import sha256

from psycopg.rows import dict_row

from .models import Claim
from .v2_models import CuratedEvidence


def _evidence(row, event, indicator):
    document_id = sha256(row["canonical_url"].encode()).hexdigest()
    claim_id = row.get("claim_id") or sha256(
        f"{document_id}:{event}:{indicator}:{row['summary']}".encode()
    ).hexdigest()
    version = row.get("claim_version")
    citation_id = f"claim:{claim_id}:{version}" if version else f"claim:{claim_id}"
    relation = row.get("relation", "contextual")
    claim_type = {
        "observation": "observed",
        "interpretation": "institutional_interpretation",
    }.get(row.get("evidence_type"), row.get("evidence_type") or "observed")
    source = CuratedEvidence(
        id=citation_id, kind="document", title=row["title"],
        url=row.get("evidence_url") or row["canonical_url"],
        attribution=row["attribution"], rights_url=row["rights_url"], published_on=row["published_on"],
        period_start=row["period_start"], period_end=row["period_end"], claim_id=claim_id,
        document_id=document_id, event=event, indicator=indicator, geography=row["geography"],
        relation="context" if relation == "contextual" else relation,
        locator=row["locator"], qualifiers=row["qualifiers"], claim_type=claim_type,
        claim_version=version, available_at=row.get("available_at"), measure=row.get("measure"),
        unit=row.get("unit"), event_linkage=row.get("event_linkage"), source_id=row.get("source_id"),
    )
    return source, Claim(text=row["summary"], citations=[source.id])


# The view is the authority for current rights and review eligibility.
# The legacy EIA context describes a pre-event baseline and has no editorial timestamp.
DYNAMIC_SQL = """
    WITH eligible AS (
        SELECT e.*, CASE relation WHEN 'direct' THEN 0 WHEN 'contextual' THEN 1 ELSE 2 END AS relevance,
               period_end - period_start AS span
        FROM macropulse_online.news_evidence e
        WHERE event = %(event)s AND indicator = %(indicator)s
          AND published_on <= %(cutoff)s AND published_on <= (statement_timestamp() AT TIME ZONE 'UTC')::date
          AND (
              (claim_id IS NOT NULL AND claim_version IS NOT NULL
               AND period_start >= %(start)s AND period_end <= %(end)s
               AND available_at < ((%(cutoff)s::date + 1)::timestamp AT TIME ZONE 'UTC')
               AND available_at <= statement_timestamp())
              OR (claim_id IS NULL AND source_id = 'eia'
                  AND canonical_url = 'https://www.eia.gov/todayinenergy/detail.php?id=51618'
                  AND published_on BETWEEN %(start)s AND %(end)s)
          )
          AND (indicator <> 'eur-usd' OR (
              relation = 'direct' AND evidence_type = 'observation'
              AND measure = 'spot_reference_rate' AND unit = 'USD per EUR' AND geography = 'bilateral'
          ))
    ), unique_versions AS (
        SELECT DISTINCT ON (claim_id, claim_version) *
        FROM eligible
        ORDER BY claim_id, claim_version, available_at, canonical_url, locator
    )
    SELECT * FROM unique_versions
    ORDER BY relevance, span, period_end DESC, published_on DESC, claim_id, claim_version, canonical_url
    LIMIT 5
"""


def retrieve_online(connection, _question, event, indicator, window, cutoff, *, dynamic=False):
    scope = "genai" if event == "generative-ai" else event
    if dynamic:
        with connection.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(DYNAMIC_SQL, {
                "event": scope, "indicator": indicator, "start": window.start,
                "end": window.end, "cutoff": cutoff,
            }).fetchall()
        return [_evidence(row, scope, indicator) for row in rows]

    # The JSON projection tolerates an older view during rollout. Its rows lack
    # phase 9.3 availability and cannot satisfy the typed bilateral spot gate.
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute("""
            SELECT e.* FROM macropulse_online.news_evidence e
            WHERE event = %s AND indicator = %s AND published_on BETWEEN %s AND %s
              AND ((to_jsonb(e)->>'claim_id') IS NULL OR
                   (to_jsonb(e)->>'available_at')::timestamptz < ((%s::date + 1)::timestamp AT TIME ZONE 'UTC'))
              AND (indicator <> 'eur-usd' OR (
                   to_jsonb(e)->>'relation' = 'direct' AND to_jsonb(e)->>'evidence_type' = 'observation'
                   AND to_jsonb(e)->>'measure' = 'spot_reference_rate' AND to_jsonb(e)->>'unit' = 'USD per EUR'
                   AND geography = 'bilateral'))
            ORDER BY published_on DESC, canonical_url, period_start
            LIMIT 1
        """, (scope, indicator, window.start, min(window.end, cutoff), cutoff)).fetchone()
    return [_evidence(row, scope, indicator)] if row else []
