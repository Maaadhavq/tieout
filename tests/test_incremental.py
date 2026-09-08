"""Incremental ingest.

The claim in the README is that adding a document compares its facts only
against the blocks they join, and never recomputes existing relationships.
These tests hold that claim to account without needing an API key.
"""
import copy


from tieout.goldset import load_facts
from tieout.reason import blocking
from tieout.reason.compare import compare

FACTS = load_facts()
ALL = list(FACTS.values())


def _pairset(pairs):
    return {tuple(sorted((a["fact_id"], b["fact_id"]))) for a, b in pairs}


def test_blocking_beats_the_naive_pair_count():
    s = blocking.stats(ALL)
    assert s["pairs_considered"] < s["pairs_if_naive"], "blocking bought nothing"
    assert s["blocks"] > 1


def test_incremental_reaches_the_same_verdicts_as_a_full_pass():
    """Split the corpus, ingest one half 'later', and check the relationships
    that involve the new facts are identical to the full-pass ones."""
    new_ids = {"M5", "M6", "D3", "D5"}
    new = [f for f in ALL if f["fact_id"] in new_ids]
    old = [f for f in ALL if f["fact_id"] not in new_ids]

    full = {p: compare(FACTS[p[0]], FACTS[p[1]]).label for p in _pairset(blocking.candidate_pairs(ALL))}
    incr = {p: compare(FACTS[p[0]], FACTS[p[1]]).label
            for p in _pairset(blocking.pairs_for_new_facts(new, old))}

    touching_new = {p: lbl for p, lbl in full.items() if p[0] in new_ids or p[1] in new_ids}
    assert incr == touching_new, (
        "incremental pass disagrees with the full pass on pairs involving new facts:\n"
        f"  only in full: {sorted(set(touching_new) - set(incr))}\n"
        f"  only in incr: {sorted(set(incr) - set(touching_new))}"
    )


def test_incremental_never_recomputes_an_old_to_old_pair():
    new_ids = {"M5", "M6", "D3", "D5"}
    new = [f for f in ALL if f["fact_id"] in new_ids]
    old = [f for f in ALL if f["fact_id"] not in new_ids]
    for a, b in _pairset(blocking.pairs_for_new_facts(new, old)):
        assert a in new_ids or b in new_ids, f"({a}, {b}) is an old-to-old pair"


def test_a_new_fact_in_an_unrelated_block_costs_nothing():
    """A document about something the layer has never seen should not cause
    any comparison at all -- that is what keeps ingest sub-linear."""
    stranger = copy.deepcopy(FACTS["M1"])
    stranger.update(fact_id="X1", entity_key="patagonia", entity_raw="Patagonia",
                    metric_key="rainfall", metric_raw="annual rainfall")
    pairs = blocking.pairs_for_new_facts([stranger], ALL)
    assert pairs == [], f"an unrelated fact generated {len(pairs)} comparisons"


def test_a_new_fact_lands_in_its_block_and_relates():
    """The demo moment: a later document restating an existing metric."""
    later = copy.deepcopy(FACTS["M1"])
    later.update(fact_id="X2", doc_id="unseen.pdf", value_raw="6.5",
                 period_raw="2024-25")
    pairs = blocking.pairs_for_new_facts([later], ALL)
    ids = {b["fact_id"] for a, b in pairs} | {a["fact_id"] for a, b in pairs}
    assert "M2" in ids, "did not reach the IMF fact stating the same figure"

    partner = next(b for a, b in pairs if b["fact_id"] == "M2")
    assert compare(later, partner).label == "CORROBORATES"


def test_pair_generation_is_order_independent():
    a = _pairset(blocking.candidate_pairs(ALL))
    b = _pairset(blocking.candidate_pairs(list(reversed(ALL))))
    assert a == b


def test_a_more_specific_label_is_still_asked_about():
    """One document writes "revenue", another "revenue from operations". Their
    token overlap is 0.333 against a 0.34 floor, so the pair was never proposed
    and the adjudicator never saw it -- losing a cross-magnitude corroboration
    and a period reconciliation to seven thousandths.

    Lowering the floor is the wrong repair: at 0.33 the starter corpus gains 714
    pairs, mostly "gfce growth" against "real growth" -- different metrics
    sharing one common word. Containment is the sharper signal, and it adds 303.
    """
    from tieout.reason.blocking import near_miss

    # one label is a more specific form of the other -> a real question
    for a, b in (("revenue", "revenue_from_operations"),
                 ("income", "other_comprehensive_income"),
                 ("expenses", "employee_benefits_expenses"),
                 ("cad", "cad_as_percentage")):
        assert near_miss(a, b), f"{a!r} ~ {b!r} should be asked about"
        assert near_miss(b, a), "the test must be symmetric"

    # merely sharing a common word is not a question worth spending a call on
    for a, b in (("gfce_growth", "real_growth"),
                 ("nominal_growth", "real_growth"),
                 ("adjusted_ebitda", "ebitda_margin"),
                 ("revenue_from_services", "revenue_from_contract_with_customers")):
        assert not near_miss(a, b), f"{a!r} ~ {b!r} should NOT be proposed"

    # unrelated keys stay unrelated, and identical keys are handled before this
    assert not near_miss("count_employees", "headcount")
    assert not near_miss("", "revenue")


def test_the_adjudicator_uses_a_model_that_exists_and_falls_through():
    """The adjudicator defaulted to a hard-coded "gemini-2.5-flash", which
    returns 404 NOT_FOUND on a key issued after that alias was retired. The
    bare `except` then turned the 404 into "unresolved", so the component never
    ran, `metric_aliases` stayed empty, and /api/stats reported "relationships
    decided by a model: 0" -- which reads like the rules settled everything
    rather than like a dead dependency.
    """
    from tieout.ingest.extract import MODEL_CHAIN
    from tieout.reason.adjudicate import Adjudicator

    class Store:
        def q(self, *a, **k):
            return []
        def insert(self, *a, **k):
            pass

    adj = Adjudicator(Store(), api_key="test-key")
    assert adj.model == MODEL_CHAIN[0], f"default is {adj.model!r}, not the chain head"
    assert adj.model != "gemini-2.5-flash", "back on the retired alias"
    assert len(adj._chain) > 1, "no fallback if the head model is unavailable"

    # A dead head model must not be mistaken for an answer: it falls through.
    tried = []

    class Models:
        def generate_content(self, model, **kw):
            tried.append(model)
            if model != MODEL_CHAIN[-1]:
                raise RuntimeError("404 NOT_FOUND")
            return type("R", (), {"text": '{"same": true, "confidence": 0.9,'
                                          ' "rationale": "same quantity"}'})()

    adj._client = type("C", (), {"models": Models()})()
    assert adj.resolve("revenue", "revenue", "revenue_from_operations",
                       "revenue from operations") is True
    assert len(tried) > 1, f"did not fall through the chain: {tried}"
    assert adj.calls == 1


def test_a_document_ingested_with_no_key_is_read_again_once_there_is_one(tmp_path):
    """Uploading a PDF without GEMINI_API_KEY records the document and extracts
    nothing -- the extractor can only replay a cache that has never seen it.

    Dedup is by content hash and did not look at whether anything was actually
    extracted, so every later upload of that file answered "already in the layer
    -- nothing recomputed". The file could never be read, and the message sounded
    like success. Adding a key did not help; you had to know to delete the row.
    """
    import fitz

    from tieout.ingest.pipeline import ingest_document
    from tieout.store.db import Store

    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Contoso Freight revenue was Rs. 40 crore in FY2024.")
    pdf = tmp_path / "contoso.pdf"
    pdf.write_bytes(doc.tobytes())
    doc.close()

    store = Store(tmp_path / "t.db")

    from tieout.ingest.extract import PageResult

    class Extractor:
        """Extracts nothing, which is exactly what an offline run does with a
        page it has never cached."""
        def __init__(self, offline):
            self.offline = offline
            self.calls = 0
            self.cache_hits = 0
        def extract_pages(self, pages, workers=6, progress=None):
            return [PageResult(page_no=p.number, facts=[], cached=False)
                    for p in pages]

    offline = Extractor(offline=True)
    first = ingest_document(store, pdf, offline)
    assert first.already_ingested is False
    assert first.kept == 0, "an offline run cannot extract from an unseen page"

    # Same bytes, but a model is available now: it must not be waved through.
    second = ingest_document(store, pdf, Extractor(offline=False))
    assert second.already_ingested is False, \
        "an empty offline record still blocks the document from ever being read"

    # And exactly one document row survives -- the stale one is replaced.
    rows = store.q("SELECT doc_id FROM documents")
    assert len(rows) == 1, f"re-ingest left {len(rows)} document rows"

    # And the re-read only happens when a model is actually available: still
    # offline, the empty record is left alone rather than churned every upload.
    third = ingest_document(store, pdf, Extractor(offline=True))
    assert third.already_ingested is True,         "an offline re-upload re-ingested instead of deduping"
