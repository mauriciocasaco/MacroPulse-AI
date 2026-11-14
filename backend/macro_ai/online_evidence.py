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
    relation = row.get("relation", "contextual")
    claim_type = {
        "observation": "observed",
        "interpretation": "institutional_interpretation",
    }.get(row.get("evidence_type"), row.get("evidence_type") or "observed")
    source = CuratedEvidence(
        id=f"claim:{claim_id}", kind="document", title=row["title"],
        url=row.get("evidence_url") or row["canonical_url"],
        attribution=row["attribution"], rights_url=row["rights_url"], published_on=row["published_on"],
        period_start=row["period_start"], period_end=row["period_end"], claim_id=claim_id,
        document_id=document_id, event=event, indicator=indicator, geography=row["geography"],
        relation="context" if relation == "contextual" else relation,
        locator=row["locator"], qualifiers=row["qualifiers"], claim_type=claim_type,
    )
    return source, Claim(text=row["summary"], citations=[source.id])


def retrieve_online(connection, _question, event, indicator, window, cutoff):
    scope = "genai" if event == "generative-ai" else event
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
