"""Evidence accuracy: does the highlight actually cover the quoted text?

A citation that points at the wrong part of the page is worse than no
citation, because it looks checked. So rather than trusting the stored
rectangles, this re-reads the PDF inside each rectangle and asserts the text
found there is the text we claimed to have quoted.
"""
import json
import re
from pathlib import Path

import fitz
import pytest

from tieout.goldset import load_raw
from tieout.goldstore import build_gold_store
from tieout.ingest.pdf import normalize_ws
from tieout.store.db import Store

DATA = Path(__file__).resolve().parents[1] / "data" / "starter-datasets"
pytestmark = pytest.mark.skipif(not DATA.exists(), reason="starter dataset not present")


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    st = Store(tmp_path_factory.mktemp("ev") / "t.db")
    build_gold_store(st)
    return st


def _words_in(path, page_no, rects):
    doc = fitz.open(path)
    try:
        page = doc[page_no - 1]
        out = []
        for x0, y0, x1, y1 in rects:
            clip = fitz.Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1)
            out.append(page.get_text("text", clip=clip))
        return normalize_ws(" ".join(out))
    finally:
        doc.close()


def test_every_stored_box_covers_its_quote(store):
    rows = store.q(
        "SELECT e.*, d.path FROM evidence e JOIN documents d ON d.doc_id = e.doc_id")
    assert rows, "gold store produced no evidence"

    for r in rows:
        boxes = json.loads(r["bbox_json"])
        assert boxes, f"{r['evidence_id']} stored no bounding box"
        found = _words_in(r["path"], r["page_no"], boxes).lower()
        quote = normalize_ws(r["quote"]).lower()
        # Every substantial word of the quote must appear inside the rectangles.
        words = [w for w in re.findall(r"[a-z0-9.,%/-]{3,}", quote)]
        missing = [w for w in words if w not in found]
        assert not missing, (
            f"{r['evidence_id']} p{r['page_no']}: the highlight misses "
            f"{missing[:6]} — found {found[:160]!r}")


def test_boxes_are_inside_the_page(store):
    rows = store.q(
        "SELECT e.*, d.path FROM evidence e JOIN documents d ON d.doc_id = e.doc_id")
    for r in rows:
        doc = fitz.open(r["path"])
        try:
            rect = doc[r["page_no"] - 1].rect
        finally:
            doc.close()
        for x0, y0, x1, y1 in json.loads(r["bbox_json"]):
            assert 0 <= x0 < x1 <= rect.width + 1, f"{r['evidence_id']} box off-page in x"
            assert 0 <= y0 < y1 <= rect.height + 1, f"{r['evidence_id']} box off-page in y"


def test_a_box_does_not_cover_the_whole_page(store):
    """A rectangle covering everything would trivially pass the test above."""
    rows = store.q(
        "SELECT e.*, d.path FROM evidence e JOIN documents d ON d.doc_id = e.doc_id")
    for r in rows:
        doc = fitz.open(r["path"])
        try:
            area = doc[r["page_no"] - 1].rect.get_area()
        finally:
            doc.close()
        covered = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in json.loads(r["bbox_json"]))
        assert covered < 0.5 * area, f"{r['evidence_id']} highlight covers half the page"


def test_gold_facts_all_survived_grounding(store):
    ids = {r["fact_id"] for r in store.q("SELECT fact_id FROM facts")}
    expected = {f["id"] for f in load_raw()["facts"]}
    assert ids == expected, f"missing from the store: {sorted(expected - ids)}"
