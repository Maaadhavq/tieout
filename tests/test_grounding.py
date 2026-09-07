"""The grounding gate, exercised against the real starter PDFs.

These are not mocks: every quote below was located by hand in Step 0 and is
recorded in tests/golden/cases.yaml. If the gate stops finding them, evidence
links are broken and the whole system is untrustworthy.
"""
from pathlib import Path

import pytest
import yaml

from tieout.ingest.ground import locate
from tieout.ingest.pdf import read_pages

DATA = Path(__file__).resolve().parents[1] / "data" / "starter-datasets"
GOLD = yaml.safe_load((Path(__file__).parent / "golden" / "cases.yaml").read_text(encoding="utf-8"))

pytestmark = pytest.mark.skipif(not DATA.exists(), reason="starter dataset not present")

_cache: dict[str, list] = {}


def pages_for(rel: str):
    if rel not in _cache:
        _cache[rel] = read_pages(DATA / rel)
    return _cache[rel]


@pytest.mark.parametrize("fact", GOLD["facts"], ids=[f["id"] for f in GOLD["facts"]])
def test_every_gold_quote_grounds_on_its_stated_page(fact):
    page = pages_for(fact["doc"])[fact["page"] - 1]
    g = locate(page, fact["quote"])
    assert g.ok, f"{fact['id']}: quote not found on {fact['doc']} p{fact['page']}"
    assert g.bboxes, f"{fact['id']}: located but produced no bounding box to highlight"


@pytest.mark.parametrize("fact", GOLD["facts"][:4], ids=[f["id"] for f in GOLD["facts"][:4]])
def test_a_quote_does_not_ground_on_the_wrong_page(fact):
    """The gate must be specific, not merely permissive."""
    pages = pages_for(fact["doc"])
    wrong = pages[(fact["page"] + 20) % len(pages)]
    assert not locate(wrong, fact["quote"]).ok


def test_invented_text_is_rejected():
    page = pages_for(GOLD["facts"][0]["doc"])[7]
    fabricated = "India's real GDP growth accelerated to 9.9 per cent in 2024-25, a record high."
    assert not locate(page, fabricated).ok, "the gate let a fabricated quote through"


def test_a_real_sentence_from_the_wrong_document_is_rejected():
    rbi = pages_for("india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf")[7]
    delhivery_quote = "revenue from operations on consolidated basis for FY24"
    assert not locate(rbi, delhivery_quote).ok


def test_too_short_a_quote_is_refused():
    page = pages_for(GOLD["facts"][0]["doc"])[7]
    assert not locate(page, "6.5").ok, "a bare number is not evidence"


def test_whitespace_and_dash_drift_still_matches():
    """Models retype quotes with plain hyphens and collapsed spacing."""
    page = pages_for("india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf")[7]
    g = locate(page, "growth   moderated  to 6.5 per cent in 2024-25")
    assert g.ok and g.match_type in ("normalized", "fuzzy")


def test_image_only_page_grounds_nothing():
    """IMF p1 is a scanned cover: no text layer, so no fact may come from it."""
    page = pages_for("india-macroeconomy/03-imf-india-2025-article-iv-excerpt.pdf")[0]
    assert page.text.strip() == ""
    assert not locate(page, "India 2025 Article IV Consultation").ok
