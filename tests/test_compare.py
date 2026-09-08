"""The comparator against hand-labelled pairs.

Every expectation here was decided by reading the source PDFs, before the
comparator existed. These are the four required cases.
"""
import pytest

from tieout.goldset import load_facts, load_pairs, load_raw
from tieout.reason.blocking import candidate_pairs
from tieout.reason.compare import (CONTRADICTS, CORROBORATES, INSUFFICIENT,
                                   RECONCILED, UNRELATED, compare)
from tieout.reason.explain import explain

FACTS = load_facts()
PAIRS = load_pairs()


# The one alias the rules cannot derive: whether the earnings deck's "revenue
# from services" names the same quantity as the annual report's "revenue from
# operations". Supplied here the way the adjudicator supplies it at runtime.
ALIASES = {("revenue_from_operations", "revenue_from_services"): True}


@pytest.mark.parametrize("a,b,expected,why", PAIRS, ids=[f"{p[0]}~{p[1]}" for p in PAIRS])
def test_labelled_pairs(a, b, expected, why):
    v = compare(FACTS[a], FACTS[b], ALIASES)
    assert v.label == expected, (
        f"{a}~{b}: expected {expected} ({why}), got {v.label}\n"
        + "\n".join(f"    {c.dimension:14s} {c.status:8s} {c.detail}" for c in v.checks)
    )


def test_reconciled_pairs_name_the_dimension():
    """A reconciliation that cannot say WHICH dimension differs is useless."""
    expected_dims = {("M1", "M3"): "vintage", ("M1", "M4"): "measure",
                     ("D1", "D2"): "consolidation", ("D4", "D5"): "period"}
    for (a, b), dim in expected_dims.items():
        v = compare(FACTS[a], FACTS[b])
        assert v.label == RECONCILED and v.dimension == dim, \
            f"{a}~{b}: expected reconciled on {dim}, got {v.label}/{v.dimension}"


def test_the_negative_control_is_never_paired():
    """M3 and M4 are both 6.4% for the same period and country, but one is GDP
    and the other GVA. A value-first system corroborates them. We must not."""
    for a, b, why in load_raw()["non_pairs"]:
        v = compare(FACTS[a], FACTS[b])
        assert v.label not in (CORROBORATES, CONTRADICTS), f"{a}~{b} wrongly related: {why}"


def test_blocking_is_recall_and_the_comparator_is_precision():
    """Blocking should SURFACE the near-miss pair so the comparator gets to
    rule on it; rejecting it is the comparator's job, not the index's."""
    pairs = {tuple(sorted((a["fact_id"], b["fact_id"])))
             for a, b in candidate_pairs(list(FACTS.values()))}
    assert ("M3", "M4") in pairs, "the near-miss pair must reach the comparator"
    assert compare(FACTS["M3"], FACTS["M4"]).label == UNRELATED


def test_an_unsettled_metric_alias_escalates_rather_than_guessing():
    """Without the alias, D2~D3 must come back as a question, not a verdict."""
    v = compare(FACTS["D2"], FACTS["D3"])
    assert v.label == INSUFFICIENT
    assert v.needs_adjudication and v.adjudication_question
    assert "revenue from operations" in v.adjudication_question

    settled = compare(FACTS["D2"], FACTS["D3"], ALIASES)
    assert settled.label == CORROBORATES, "with the alias supplied it must corroborate"


def test_every_verdict_carries_a_readable_trace():
    for a, b, expected, _ in PAIRS:
        v = compare(FACTS[a], FACTS[b], ALIASES)
        assert v.checks, f"{a}~{b} produced no rule trace"
        assert all(c.detail for c in v.checks), f"{a}~{b} has a check with no detail"
        text = explain(v, FACTS[a], FACTS[b])
        assert len(text) > 40, f"{a}~{b} explanation too thin: {text!r}"


def test_a_missing_period_is_insufficient_not_contradiction():
    """The honest-uncertainty requirement: an unstated dimension must never be
    reported as disagreement."""
    a = dict(FACTS["M1"])
    b = dict(FACTS["M5"])
    b["period_start"] = b["period_end"] = None
    b["period_raw"] = None
    v = compare(a, b)
    assert v.label == INSUFFICIENT and v.dimension == "period"


def test_cross_currency_is_never_guessed():
    a = dict(FACTS["D2"])
    b = dict(FACTS["D2"])
    b["fact_id"], b["unit_canon"], b["value_num"] = "D2usd", "USD", 975.0
    v = compare(a, b)
    assert v.label == INSUFFICIENT
    assert any(c.dimension == "unit" and c.status == "differs" for c in v.checks)


def test_different_entities_are_unrelated():
    a, b = dict(FACTS["M1"]), dict(FACTS["D2"])
    assert compare(a, b).label == UNRELATED


def test_state_facts_reconcile_over_time():
    """The assignment's own example: active in one document, resigned in a later
    one. Must be a state change, not a contradiction."""
    v = compare(FACTS["D4"], FACTS["D5"])
    assert v.label == RECONCILED
    text = explain(v, FACTS["D4"], FACTS["D5"])
    assert "contradiction" in text.lower()


def test_first_person_entities_are_refused():
    """Filings say "our Company" and "the Group". Those name nothing on their
    own, and blocking on them would compare every filing's facts against every
    other filing's. They must never become an entity key."""
    from tieout.normalize.entities import entity_key, is_resolvable
    for bad in ("our Company", "the Group", "the Board", "we", "it", "Company",
                "the", "management", "our subsidiary"):
        assert not is_resolvable(entity_key(bad)), f"{bad!r} was accepted as an entity"
    for good in ("Delhivery Limited", "India", "Spoton Logistics", "Sahil Barua",
                 "Reserve Bank of India", "International Monetary Fund"):
        assert is_resolvable(entity_key(good)), f"{good!r} was wrongly refused"


def test_containment_does_not_merge_distinct_organisations():
    """One name may extend another, but a LEADING qualifier is what tells two
    institutions apart: "State Bank of India" and "Bank of India" are different
    banks, and "India" is not the "Reserve Bank of India".

    Containment was tested on token *sets*, which discards word order, so all
    of these answered "same" -- the one error the module docstring promises
    never to make. Nothing reached it in practice because blocking keys on the
    exact entity_key, so the corpus shows no damage; that makes this a landmine
    rather than a live fault, and it is why the fix changes no stored figure.
    """
    from tieout.normalize.entities import entity_key, same_entity

    def merged(a: str, b: str) -> bool:
        return same_entity(entity_key(a), entity_key(b))[0]

    for a, b in (("State Bank of India", "Bank of India"),
                 ("Reserve Bank of India", "Bank of India"),
                 ("Punjab National Bank", "National Bank"),
                 ("India", "Reserve Bank of India"),
                 ("India", "Government of India"),
                 ("Food Corporation of India", "India"),
                 ("European Union", "Union"),
                 ("basmati rice", "rice")):
        assert not merged(a, b), f"{a!r} was wrongly merged with {b!r}"

    # A trailing qualifier still folds -- that is what the rule exists for.
    for a, b in (("Delhivery Limited", "Delhivery"),
                 ("Reserve Bank", "Reserve Bank of India"),
                 ("HDFC Bank", "HDFC"),
                 ("Spoton", "Spoton Logistics"),
                 ("Tata Motors", "Tata Motors Limited")):
        assert merged(a, b), f"{a!r} should still fold with {b!r}"
