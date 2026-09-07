"""Reporting-period normalization.

Every publisher in the starter set writes the same year differently:

    Economic Survey   "FY25"
    RBI               "2024-25"
    IMF               "FY2024/25"
    Delhivery         "FY24"        <- a DIFFERENT year

All of these resolve here to (start, end, grain). Getting this wrong is
what makes a naive system call a period difference a contradiction.

Convention: the Indian financial year runs 1 April -> 31 March.
  - A single year token is the year the FY *ends*:      FY25 -> 2024-04-01..2025-03-31
  - A span token is anchored on the year it *starts*:   2024-25 -> 2024-04-01..2025-03-31
Both spellings therefore land on the same interval, which is the point.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

FY_START_MONTH = 4  # April. Configurable per jurisdiction; India is the default here.

MONTHS = {
    m: i
    for i, m in enumerate(
        "january february march april may june july august september october november december".split(),
        start=1,
    )
}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    grain: str  # annual | quarterly | monthly | instant | unknown
    confidence: float = 1.0
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "grain": self.grain,
            "confidence": self.confidence,
            "note": self.note,
        }


def _fy_from_end_year(end_year: int) -> tuple[date, date]:
    return date(end_year - 1, FY_START_MONTH, 1), date(end_year, FY_START_MONTH - 1, 31)


def _fy_from_start_year(start_year: int) -> tuple[date, date]:
    return date(start_year, FY_START_MONTH, 1), date(start_year + 1, FY_START_MONTH - 1, 31)


def _expand2(yy: int, anchor: int = 2000) -> int:
    """'24' -> 2024. Two-digit years are assumed to be this century."""
    return yy if yy > 99 else anchor + yy


def _last_day(year: int, month: int) -> int:
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]


def _month(year: int, month: int) -> "Period":
    return Period(date(year, month, 1), date(year, month, _last_day(year, month)), "monthly")


def _fy_quarter(fy_end_year: int, q: int) -> tuple[date, date]:
    """Q1 of an Apr-Mar year is Apr-Jun; Q4 is Jan-Mar of the ending year."""
    start_month = FY_START_MONTH + 3 * (q - 1)
    year = fy_end_year - 1 + (start_month - 1) // 12
    start_month = (start_month - 1) % 12 + 1
    start = date(year, start_month, 1)
    end_month = start_month + 2
    end_year = year + (end_month - 1) // 12
    end_month = (end_month - 1) % 12 + 1
    last = [31, 29 if end_year % 4 == 0 and (end_year % 100 or end_year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][end_month - 1]
    return start, date(end_year, end_month, last)


# Ordered most-specific first. Each returns a Period.
def parse_period(raw: str | None) -> Period | None:
    if not raw:
        return None
    s = re.sub(r"\s+", " ", str(raw)).strip().lower()
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")

    # Q4 FY24 / Q4FY2024 / fourth quarter of FY24
    m = re.search(r"\bq([1-4])\s*[-/ ]?\s*fy\s*(\d{2,4})\b", s)
    if m:
        q, y = int(m.group(1)), _expand2(int(m.group(2)))
        a, b = _fy_quarter(y, q)
        return Period(a, b, "quarterly")

    # FY2024-25 / FY2023-24 / FY 2024/25 / FY2024/2025
    m = re.search(r"\bfy\s*(\d{4})\s*[-/]\s*(\d{2,4})\b", s)
    if m:
        a, b = _fy_from_start_year(int(m.group(1)))
        return Period(a, b, "annual")

    # bare span: 2024-25 / 2023-24 (RBI house style)
    m = re.search(r"\b(19|20)(\d{2})\s*[-/]\s*(\d{2})\b", s)
    if m:
        start_year = int(m.group(1) + m.group(2))
        nxt = _expand2(int(m.group(3)), anchor=start_year - start_year % 100)
        if nxt in (start_year + 1, start_year + 1 - 100):  # sane span
            a, b = _fy_from_start_year(start_year)
            return Period(a, b, "annual")

    # FY24 / FY2024 / fiscal 2024 / fiscal year 2024
    m = re.search(r"\b(?:fy|fiscal(?:\s+year)?)\s*(\d{2,4})\b", s)
    if m:
        a, b = _fy_from_end_year(_expand2(int(m.group(1))))
        return Period(a, b, "annual")

    # calendar year: CY2024 / calendar year 2024
    m = re.search(r"\b(?:cy|calendar\s+year)\s*(\d{2,4})\b", s)
    if m:
        y = _expand2(int(m.group(1)))
        return Period(date(y, 1, 1), date(y, 12, 31), "annual")

    # 2025Q2 / 2025 q2  -- IMF writes fiscal quarters this way but the token
    # itself is ambiguous, so we resolve to the calendar quarter and say so.
    m = re.search(r"\b(19|20)(\d{2})\s*q([1-4])\b", s)
    if m:
        y, q = int(m.group(1) + m.group(2)), int(m.group(3))
        start = date(y, 3 * (q - 1) + 1, 1)
        end_m = 3 * q
        last = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][end_m - 1]
        return Period(
            start, date(y, end_m, last), "quarterly", confidence=0.55,
            note="bare YYYYQn is ambiguous between calendar and fiscal quarters; read as calendar",
        )

    # "as of 2022-05" -- a status is often stated as of a month.
    m = re.search(r"\b((?:19|20)\d{2})-(0[1-9]|1[0-2])\b(?!-\d)", s)
    if m:
        return _month(int(m.group(1)), int(m.group(2)))

    # explicit date: March 31, 2024 / 31 March 2024 / as of 2024-03-31.
    # These run BEFORE the bare-month rule so a day is never discarded.
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", s)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return Period(d, d, "instant")
    m = re.search(r"\b([a-z]+)\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b", s)
    if m and m.group(1) in MONTHS:
        d = date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
        return Period(d, d, "instant")
    m = re.search(r"\b(\d{1,2})\s+([a-z]+),?\s+((?:19|20)\d{2})\b", s)
    if m and m.group(2) in MONTHS:
        d = date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
        return Period(d, d, "instant")

    # bare month: "May 2022", "as of May 2022"
    m = re.search(r"\b([a-z]+)\s+((?:19|20)\d{2})\b", s)
    if m and m.group(1) in MONTHS:
        return _month(int(m.group(2)), MONTHS[m.group(1)])

    # bare year
    m = re.fullmatch(r"(?:in\s+|during\s+)?((?:19|20)\d{2})", s)
    if m:
        y = int(m.group(1))
        return Period(date(y, 1, 1), date(y, 12, 31), "annual", confidence=0.7,
                      note="bare year read as calendar year")

    return None


def relation(a: Period, b: Period) -> str:
    """same | contains | contained_by | overlaps | disjoint"""
    if a.start == b.start and a.end == b.end:
        return "same"
    if a.start <= b.start and a.end >= b.end:
        return "contains"
    if b.start <= a.start and b.end >= a.end:
        return "contained_by"
    if a.start <= b.end and b.start <= a.end:
        return "overlaps"
    return "disjoint"
