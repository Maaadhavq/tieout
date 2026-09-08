"""The grounding gate, exercised against the real starter PDFs.

These are not mocks: every quote below was located by hand in Step 0 and is
recorded in tests/golden/cases.yaml. If the gate stops finding them, evidence
links are broken and the whole system is untrustworthy.
"""
from pathlib import Path

import pytest
import yaml

from tieout.ingest.ground import locate
from tieout.ingest.pdf import Page, Word, read_pages


def _page(text: str) -> Page:
    """A synthetic page: one Word per token, carrying its true char offsets.

    Used for the offset-arithmetic tests below, where the point is the mapping
    from folded text back to raw characters and a real PDF would only make the
    failure harder to read.
    """
    words, cursor = [], 0
    for token in text.split(" "):
        if not token:
            continue
        start = text.index(token, cursor)
        words.append(Word(token, float(len(words) * 10), 0.0,
                          float(len(words) * 10 + 8), 10.0, start, start + len(token)))
        cursor = start + len(token)
    return Page(1, "1", text, words, 600.0, 800.0)

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


def test_a_length_changing_fold_does_not_shift_the_located_span():
    """The normalized rung folded the page with `normalize_ws` but built its
    offset map with a whitespace-only walk. Two of the folds change length -- a
    ligature expands to two letters, a soft hyphen vanishes -- so any of them
    earlier on the page pushed every later offset out.

    A single "fi" before the match returned the span one character late
    ("evenue rose..."), two returned it two late, and a soft hyphen returned it
    one early. The span is what the highlight boxes are computed from, so the
    box on the page image pointed at the wrong characters.
    """
    tail = "Revenue rose 5–6 per cent in 2024."   # en dash on the page
    quote = "Revenue rose 5-6 per cent in 2024."      # plain hyphen in the quote

    for lead in ("The plain section. ",
                 "The ﬁrst section. ",
                 "The ﬁrst ﬂow section. ",
                 "The sec­tion here. "):
        page = _page(lead + tail)
        g = locate(page, quote)
        assert g.match_type == "normalized", f"{lead!r} -> {g.match_type}"
        assert page.text[g.char_start:g.char_end] == tail, \
            f"span shifted for {lead!r}: {page.text[g.char_start:g.char_end]!r}"
        # and the boxes follow the span, so they must not reach back into the lead
        assert g.bboxes, "no highlight boxes for a located span"
        assert min(b[0] for b in g.bboxes) >= 0
