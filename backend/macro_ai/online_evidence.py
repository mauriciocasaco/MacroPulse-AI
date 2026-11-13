"""Reviewed supplements to M7.B and live eligibility checks for news records."""

from datetime import date
from hashlib import sha256

from psycopg.rows import dict_row

from .models import Claim
from .v2_models import CuratedEvidence

def _evidence(row, event, indicator):
    url = row["canonical_url"]
    document_id = sha256(url.encode()).hexdigest()
    claim_id = sha256(f"{document_id}:{event}:{indicator}:{row['summary']}".encode()).hexdigest()
    source = CuratedEvidence(
        id=f"claim:{claim_id}", kind="document", title=row["title"], url=url,
        attribution=row["attribution"], rights_url=row["rights_url"], published_on=row["published_on"],
        period_start=row["period_start"], period_end=row["period_end"], claim_id=claim_id,
        document_id=document_id, event=event, indicator=indicator, geography=row["geography"],
        relation="context", locator=row["locator"], qualifiers=row["qualifiers"], claim_type="observed",
    )
    return source, Claim(text=row["summary"], citations=[source.id])


def retrieve_online(connection, _question, event, indicator, window, cutoff):
    scope = "genai" if event == "generative-ai" else event
    if indicator == "eur-usd":
        # The news table does not certify that the stored measure is bilateral spot.
        # Reviewed spot evidence comes from M7.B.
        return []
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute("""
            SELECT * FROM macropulse_online.news_evidence
            WHERE event = %s AND indicator = %s AND published_on BETWEEN %s AND %s
            ORDER BY published_on DESC, canonical_url, period_start
            LIMIT 1
        """, (scope, indicator, window.start, min(window.end, cutoff))).fetchone()
    return [_evidence(row, scope, indicator)] if row else []
