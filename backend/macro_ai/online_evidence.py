"""Reviewed supplements to M7.B and live eligibility checks for news records."""

from datetime import date
from hashlib import sha256

from psycopg.rows import dict_row

from .models import Claim
from .v2_models import CuratedEvidence

ECB_RIGHTS = "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html"
ECB_FX = {
    "covid": (
        "2020-06-18", "2020-03-12", "2020-06-03", "202004",
        "The ECB reported that the euro weakened slightly against the US dollar from 12 March to 3 June 2020.",
    ),
    "ukraine": (
        "2022-08-04", "2022-06-09", "2022-07-20", "202205",
        "The ECB reported a weaker euro against the US dollar amid expectations of faster US monetary tightening.",
    ),
}


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
    if indicator == "eur-usd" and event in ECB_FX:
        released, start, end, issue, summary = ECB_FX[event]
        published, first, last = map(date.fromisoformat, (released, start, end))
        if window.start <= first <= last <= window.end and published <= cutoff:
            return [_evidence({
                "canonical_url": f"https://www.ecb.europa.eu/press/economic-bulletin/html/eb{issue}.en.html",
                "title": f"ECB Economic Bulletin {int(issue[-2:])}/{issue[:4]}",
                "attribution": "European Central Bank · MacroPulse AI paraphrase", "rights_url": ECB_RIGHTS,
                "published_on": published, "period_start": first, "period_end": last,
                "geography": "Euro area and United States", "locator": "Financial developments",
                "qualifiers": "Bilateral exchange-rate context for the bulletin's review period.",
                "summary": summary,
            }, scope, indicator)]
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute("""
            SELECT * FROM macropulse_online.news_evidence
            WHERE event = %s AND indicator = %s AND published_on BETWEEN %s AND %s
            ORDER BY published_on DESC, canonical_url, period_start
            LIMIT 1
        """, (scope, indicator, window.start, min(window.end, cutoff))).fetchone()
    return [_evidence(row, scope, indicator)] if row else []
