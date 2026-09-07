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
