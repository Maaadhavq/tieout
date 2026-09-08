"""Would this still work if Superjoin handed us completely different PDFs?

The starter set is Indian financial and macroeconomic filings, so every rule in
`normalize/` risks having been fitted to that corpus without anyone noticing.
These facts come from domains that share nothing with it -- rainfall, clinical
trials, football, energy, headcount, shipping -- and are written in styles the
starter documents never use. Nothing is mocked: they go through the real
normalizers and the real comparator.

If a rule here turns out to be finance-specific, this is where it shows.
"""
import json

import pytest

from tieout.normalize import basis as basis_mod
from tieout.normalize.entities import entity_key, is_resolvable
from tieout.normalize.metrics import metric_key
from tieout.normalize.periods import parse_period
from tieout.normalize.units import agree, comparable_units, parse_quantity
from tieout.reason.compare import (CONTRADICTS, CORROBORATES, INSUFFICIENT,
                                   RECONCILED, UNRELATED, compare)


def fact(fid, entity, metric, value=None, unit=None, magnitude=None, period=None,
         basis=None, doc="doc_x", page=1, text=None):
    qty = parse_quantity(value, unit, magnitude)
    per = parse_period(period)
    known, _ = basis_mod.normalize_basis(basis or {}, context="")
    for dim, val in basis_mod.infer_from_label(metric).items():
        known.setdefault(dim, val)
    return {
        "fact_id": fid, "doc_id": doc, "page_no": page,
        "fact_kind": "measurement" if value is not None else "state",
        "entity_raw": entity, "entity_key": entity_key(entity),
        "metric_raw": metric, "metric_key": metric_key(metric),
        "value_raw": value, "value_num": qty.value if qty else None,
        "value_text": text, "value_tol": qty.tolerance if qty else None,
        "unit_raw": unit, "unit_canon": qty.unit if qty else None,
        "magnitude_raw": qty.magnitude if qty else magnitude,
        "period_raw": period,
        "period_start": per.start.isoformat() if per else None,
        "period_end": per.end.isoformat() if per else None,
        "period_grain": per.grain if per else "unknown",
        "basis_json": json.dumps(known), "qualifiers_json": "{}", "confidence": 1.0,
    }


# --------------------------------------------------------------- new domains
def test_rainfall_corroborates_across_two_agencies():
    a = fact("r1", "Kerala", "monsoon rainfall", "2,891", "mm", period="2024",
             doc="imd.pdf")
    b = fact("r2", "Kerala", "monsoon rainfall", "2891", "millimetres",
             period="2024", doc="skymet.pdf")
    assert compare(a, b).label == CORROBORATES


def test_clinical_trial_enrolment_contradicts():
    a = fact("c1", "Trial NCT04321", "participants enrolled", "1,204", "patients",
             period="2023", doc="protocol.pdf")
    b = fact("c2", "Trial NCT04321", "participants enrolled", "1,180", "patients",
             period="2023", doc="results.pdf")
    v = compare(a, b)
    assert v.label == CONTRADICTS
    assert v.value_delta == 24


def test_a_football_score_keeps_its_own_unit():
    a = fact("f1", "Arsenal", "goals scored", "72", "goals", period="2023-24")
    b = fact("f2", "Arsenal", "goals scored", "72", "goal", period="2023-24")
    assert compare(a, b).label == CORROBORATES
    assert a["unit_canon"] == "goal", "a goal is not a currency and not a bare count"


def test_energy_output_reconciles_on_a_unit_the_corpus_never_used():
    """Different units are refused rather than converted -- there is no
    conversion factor in evidence, and that is true outside finance too."""
    a = fact("e1", "Hornsea Two", "annual generation", "6.0", "TWh", period="2024")
    b = fact("e2", "Hornsea Two", "annual generation", "6000", "GWh", period="2024")
    v = compare(a, b)
    assert v.label == INSUFFICIENT, "unknown units must not be silently equated"


def test_headcount_reconciles_on_period_scope():
    a = fact("h1", "Acme GmbH", "employees", "1,450", "people", period="Q1 FY24",
             doc="a.pdf")
    b = fact("h2", "Acme GmbH", "employees", "1,610", "people", period="FY24",
             doc="b.pdf")
    v = compare(a, b)
    assert v.label == RECONCILED and v.dimension == "period"


def test_shipping_tonnage_across_magnitudes_in_a_new_domain():
    a = fact("s1", "Port of Rotterdam", "cargo throughput", "438.8", "tonnes",
             magnitude="million", period="2024", doc="a.pdf")
    b = fact("s2", "Port of Rotterdam", "cargo throughput", "438,800,000",
             "tonnes", period="2024", doc="b.pdf")
    assert compare(a, b).label == CORROBORATES


# ------------------------------------------------- formats the corpus lacks
@pytest.mark.parametrize("written", ["2024", "CY2024", "calendar year 2024"])
def test_calendar_years_outside_the_indian_fiscal_convention(written):
    p = parse_period(written)
    assert p and p.start.year == 2024 and p.start.month == 1


def test_percent_and_basis_points_outside_finance():
    a = parse_quantity("4.2", "per cent")
    b = parse_quantity("420", "bps")
    assert agree(a, b)[0]


def test_a_currency_the_starter_set_never_contained():
    q = parse_quantity("2.4", "€ billion")
    assert q.unit == "EUR" and q.value == pytest.approx(2.4e9)
    assert not comparable_units(q, parse_quantity("2.4", "$ billion"))


def test_metric_keys_are_derived_for_words_never_seen_before():
    for label in ["monsoon rainfall", "participants enrolled", "goals scored",
                  "cargo throughput", "nitrous oxide emissions intensity"]:
        key = metric_key(label)
        assert key and key != "unknown" and " " not in key


def test_the_entity_guard_is_linguistic_not_financial():
    """It must refuse deictic references in any domain, and accept real names
    from domains the starter set never mentions."""
    for bad in ("the study", "our team", "the group", "we", "management"):
        assert not is_resolvable(entity_key(bad))
    for good in ("Hornsea Two", "Port of Rotterdam", "Trial NCT04321",
                 "Kerala", "Arsenal", "Dr Amara Okafor"):
        assert is_resolvable(entity_key(good)), f"{good!r} refused"


# ------------------------------------------------------------- safety rails
def test_two_different_entities_never_relate_however_alike_the_metric():
    a = fact("x1", "Port of Rotterdam", "cargo throughput", "438.8", "tonnes",
             magnitude="million", period="2024")
    b = fact("x2", "Port of Antwerp", "cargo throughput", "438.8", "tonnes",
             magnitude="million", period="2024")
    assert compare(a, b).label == UNRELATED


def test_a_missing_period_outside_finance_is_still_insufficient():
    a = fact("m1", "Kerala", "monsoon rainfall", "2,891", "mm", period="2024")
    b = fact("m2", "Kerala", "monsoon rainfall", "2,600", "mm")
    assert compare(a, b).label == INSUFFICIENT


def test_no_corpus_specific_names_in_the_shared_normalizers():
    """The obvious way this regresses is someone adding a company or an index
    from the starter set to a shared rule table, or leaving one in an example."""
    import inspect
    from tieout.normalize import entities, metrics, periods, units
    for mod in (entities, metrics, periods, units):
        src = inspect.getsource(mod).lower()
        for token in ("delhivery", "spoton", "sensex", "nifty", "suvir"):
            assert token not in src, f"{token!r} appears in {mod.__name__}"


def test_unrecognised_units_are_kept_apart_not_merged():
    """Everything that was not a currency or a percentage used to collapse to
    "count", so TWh and GWh -- or mm and inches -- compared as the same unit and
    a unit mismatch surfaced as a contradiction."""
    seen = {}
    for unit in ("TWh", "GWh", "mm", "tonnes", "patients", "goals", "hectares"):
        q = parse_quantity("10", unit)
        assert q.unit != "count", f"{unit} collapsed to a generic count"
        seen[unit] = q.unit
    assert len(set(seen.values())) == len(seen), f"two units share a key: {seen}"
    assert parse_quantity("100", "Mn").unit == "count",         "a magnitude word is not a unit"
    assert parse_quantity("15,065", None).unit == "count"


def test_a_stated_geography_survives_normalization():
    """`geography` is declared in DIMENSIONS and asked for in the extraction
    prompt, but it had no entry in _VOCAB. normalize_basis walked _VOCAB to
    build `known`, so the dimension was never read, and the leftover pass
    skips anything already in DIMENSIONS, so it was not preserved either: a
    stated geography fell between the two and vanished.

    The committed cache shows the model returned one on 99 facts and every one
    was discarded. It is an open-ended dimension -- a region, a population, an
    exchange -- so it cannot have a vocabulary; it is kept verbatim when the
    document states it, and never guessed from prose.
    """
    for value in ("all-India", "Maharashtra", "urban", "Workers", "NSE"):
        known, leftover = basis_mod.normalize_basis({"geography": value})
        assert known.get("geography") or leftover.get("geography"), \
            f"geography={value!r} was dropped by normalize_basis"

    # Two figures that differ only by the population they cover are reconciled
    # on that dimension, not reported as a contradiction.
    a = fact("g1", "Northwind", "injury frequency rate", "0.56", period="FY24",
             basis={"geography": "Employees"}, doc="esg.pdf")
    b = fact("g2", "Northwind", "injury frequency rate", "1.21", period="FY24",
             basis={"geography": "Workers"}, doc="esg.pdf")
    v = compare(a, b)
    assert v.label == RECONCILED, f"expected reconciled, got {v.label}"
    assert v.dimension == "geography", f"reconciled on {v.dimension!r}"

    # Absent from the document, it is not invented from the sentence.
    known, _ = basis_mod.normalize_basis({}, context="rainfall across all-India was high")
    assert "geography" not in known, "geography was guessed from prose"


def test_a_one_sided_geography_does_not_suppress_a_contradiction():
    """Reading `geography` naively cost more than it bought.

    It arrives on ~5% of facts and usually on one side of a pair only -- often
    just restating the entity. Counted as a dimension "one side states", it
    demoted a genuine cross-document contradiction to INSUFFICIENT_EVIDENCE
    because one publisher named the country the other left implicit. Before the
    dimension was read at all, that pair contradicted; a fix that silences a
    real finding is worse than the data loss it repairs.

    One-sided: ignored, exactly as before. Both-sided: compared, which is the
    whole gain.
    """
    plain = fact("s1", "Northwind", "output growth", "6.7", "per cent",
                 period="Q1 FY25", doc="a.pdf")
    with_geo = fact("s2", "Northwind", "output growth", "6.5", "per cent",
                    period="Q1:2024-25", basis={"geography": "Northwind"}, doc="b.pdf")
    v = compare(plain, with_geo)
    assert v.label == CONTRADICTS, \
        f"a one-sided geography suppressed a contradiction: {v.label}/{v.dimension}"

    both = fact("s3", "Northwind", "output growth", "6.5", "per cent",
                period="Q1 FY25", basis={"geography": "urban"}, doc="a.pdf")
    other = fact("s4", "Northwind", "output growth", "2.1", "per cent",
                 period="Q1 FY25", basis={"geography": "rural"}, doc="b.pdf")
    v = compare(both, other)
    assert v.label == RECONCILED and v.dimension == "geography", \
        f"both-sided geography was not compared: {v.label}/{v.dimension}"
