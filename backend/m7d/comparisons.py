"""Read dated observations from explicit annual HICP statements."""
from __future__ import annotations

import re
from decimal import Decimal

from scripts.m7b_schema import Claim

from .predicates import When, _MONTH, _MONTH_NUMBER, _YEAR, normalize

_VALUE = r"-?\d{1,4}(?:[.,]\d{1,8})?"
_HICP_OBSERVATIONS = re.compile(
    rf"euro area (?P<annual>annual )?hicp inflation (?:was|reached|rose to|eased to|fell to) "
    rf"(?P<value>{_VALUE})%(?: (?P<yoy>year on year|year-on-year))? "
    rf"in (?P<month>{_MONTH}) (?P<year>{_YEAR})"
    rf"(?:(?:,? (?:after|down from|up from|from)) (?P<prior>{_VALUE})% in "
    rf"(?P<prior_month>{_MONTH})(?: (?P<prior_year>{_YEAR}))?)?\.?"
)


def hicp_observations(claim: Claim) -> tuple[tuple[When, Decimal], ...]:
    """Bind each value to its own month and the claim's approved release."""
    match = _HICP_OBSERVATIONS.fullmatch(normalize(claim.text))
    if not match or not (match["annual"] or match["yoy"]):
        return ()
    month, year = _MONTH_NUMBER[match["month"]], int(match["year"])
    current = When(year, month)
    start, end = current.bounds(year)
    if (start.isoformat(), end.isoformat()) != (claim.historical_period.start, claim.historical_period.end):
        return ()
    observations = [(current, Decimal(match["value"].replace(",", ".")))]
    if match["prior"] is not None:
        prior_month = _MONTH_NUMBER[match["prior_month"]]
        prior_year = int(match["prior_year"]) if match["prior_year"] else year - int(prior_month > month)
        prior = When(prior_year, prior_month)
        if prior.bounds(prior_year)[1] < start:
            observations.append((prior, Decimal(match["prior"].replace(",", "."))))
    return tuple(observations)
