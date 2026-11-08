"""Full closures in the independently checked US equity history window."""

from datetime import date

from .models import DateRange


US_EQUITY_VERIFIED_DATES = DateRange(date(2019, 11, 8), date(2026, 11, 8))

# NYSE Group holiday schedules and the 2025-01-09 mourning closure.
# The source releases and the exact scope are documented in docs/market-data-yahoo.md.
# Early-close dates remain sessions.
_US_EQUITY_CLOSED_MMDD = {
    2019: "1128 1225",
    2020: "0101 0120 0217 0410 0525 0703 0907 1126 1225",
    2021: "0101 0118 0215 0402 0531 0705 0906 1125 1224",
    2022: "0117 0221 0415 0530 0620 0704 0905 1124 1226",
    2023: "0102 0116 0220 0407 0529 0619 0704 0904 1123 1225",
    2024: "0101 0115 0219 0329 0527 0619 0704 0902 1128 1225",
    2025: "0101 0109 0120 0217 0418 0526 0619 0704 0901 1127 1225",
    2026: "0101 0119 0216 0403 0525 0619 0703 0907",
}
US_EQUITY_FULL_CLOSURES = frozenset(
    date(year, int(day[:2]), int(day[2:]))
    for year, days in _US_EQUITY_CLOSED_MMDD.items()
    for day in days.split()
)
