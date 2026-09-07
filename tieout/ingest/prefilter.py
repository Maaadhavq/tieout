"""Page prefilter -- the cheapest performance win available.

Institutional filings are mostly prose, tables of contents, boilerplate and
signature blocks. Sending all 511 pages of the starter set to a model costs
real money to learn that most of them assert nothing.

A page earns a model call by looking like it states a measurable claim:
figures, units, and a period to attach them to. The rule is a heuristic and
is meant to be: recall matters more than precision here, so the bar is low
and the thresholds are one constant each.

MEASURED, THEN CUT BACK. This started as a ranker: score every page and spend
a fixed model budget on the densest ones. Checked against the hand-labelled
gold set, that ranking turned out to be inverted -- the pages carrying the
headline claims scored in the BOTTOM decile:

    RBI Annual Report  p8   "growth moderated to 6.5 per cent"   rank 93/100
    Economic Survey    p4   "estimated to grow by 6.4 per cent"  rank 87/89

because a narrative page states one figure in a paragraph while a statistical
appendix states four hundred. Numeric density measures table-ness, not
importance. The ranker was removed; what is left is a junk filter that only
drops pages structurally incapable of asserting anything (no text layer,
almost no text, numbers with no unit or period). See docs/DECISIONS.md D4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .pdf import Page

NUM = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
UNIT = re.compile(
    r"(%|per\s*cent|percent|bps|basis\s+points|₹|rs\.?|inr|usd|\$|crore|lakh|million|billion|trillion|mn\b|bn\b|cr\b)",
    re.I,
)
PERIOD = re.compile(
    r"(fy\s*\d{2,4}|\b(19|20)\d{2}\s*[-/]\s*\d{2}\b|\bq[1-4]\b|\b(19|20)\d{2}\b|"
    r"quarter|half\s+year|annual|month)",
    re.I,
)
# Pages that are structurally incapable of stating a fact.
BOILERPLATE = re.compile(
    r"(table of contents|^\s*contents\s*$|this page has been intentionally left blank|"
    r"notice is hereby given|forward[- ]looking statements)",
    re.I | re.M,
)

MIN_CHARS = 200
MIN_NUMBERS = 4


@dataclass
class Verdict:
    scan: bool
    score: float
    reason: str


def assess(page: Page) -> Verdict:
    text = page.text
    if not text.strip():
        return Verdict(False, 0.0, "no text layer (image-only page)")
    if len(text) < MIN_CHARS:
        return Verdict(False, 0.0, f"too little text ({len(text)} chars)")

    numbers = NUM.findall(text)
    if len(numbers) < MIN_NUMBERS:
        return Verdict(False, 0.1, f"only {len(numbers)} numeric tokens")

    has_unit = bool(UNIT.search(text))
    has_period = bool(PERIOD.search(text))
    density = len(numbers) / max(1, len(text.split()))

    if BOILERPLATE.search(text) and density < 0.06:
        return Verdict(False, 0.15, "boilerplate section")

    score = min(1.0, density * 6) + (0.35 if has_unit else 0) + (0.25 if has_period else 0)
    if not (has_unit or has_period):
        return Verdict(False, score, "numbers present but no unit or period to anchor them")
    return Verdict(True, round(score, 3), f"{len(numbers)} figures, unit={has_unit}, period={has_period}")


def select(pages: list[Page]) -> tuple[list[Page], list[tuple[Page, Verdict]]]:
    keep, skip = [], []
    for p in pages:
        v = assess(p)
        (keep if v.scan else skip).append(p if v.scan else (p, v))
    return keep, skip
