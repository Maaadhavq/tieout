import pytest

from tieout.normalize.units import agree, comparable_units, format_value, parse_quantity


def q(*a, **kw):
    out = parse_quantity(*a, **kw)
    assert out is not None, f"failed to parse {a!r}"
    return out


def test_magnitudes_resolve_to_base_units():
    assert q("81,415.38", "INR", "million").value == pytest.approx(8.141538e10)
    assert q("8,142", "INR", "crore").value == pytest.approx(8.142e10)
    assert q("1.4", "count", "million").value == pytest.approx(1.4e6)


def test_the_delhivery_corroboration_across_magnitudes():
    """The annual report says millions, the earnings deck says crore, and the
    deck has rounded. They are the same figure."""
    ar = q("81,415.38", "INR", "million")
    deck = q("8,142", "INR", "crore")
    ok, delta = agree(ar, deck)
    assert ok, f"should agree, delta={delta:,.0f}"
    assert delta < 5e6


def test_precision_drives_tolerance_not_a_fixed_epsilon():
    """6.5 and 6.4 differ by less in absolute terms than the crore figures
    above, yet must NOT agree: both were stated to one decimal."""
    a, b = q("6.5", "percent"), q("6.4", "percent")
    ok, delta = agree(a, b)
    assert not ok
    assert delta == pytest.approx(0.1)


def test_a_rounded_figure_is_not_held_to_precision_it_never_claimed():
    assert agree(q("8,142", "INR", "crore"), q("8,141.538", "INR", "crore"))[0]
    # ... but two precise figures are held to their own precision
    assert not agree(q("8,142.0", "INR", "crore"), q("8,141.5", "INR", "crore"))[0]


def test_percent_never_takes_a_magnitude():
    x = q("6.5 per cent")
    assert x.unit == "percent" and x.magnitude is None
    assert x.value == pytest.approx(6.5)


def test_basis_points_fold_into_percent():
    assert q("640 bps").value == pytest.approx(6.4)
    assert agree(q("640 bps"), q("6.4 percent"))[0]


def test_currency_symbols_and_words():
    assert q("₹ 74,540.82 million").unit == "INR"
    assert q("Rs. 8,142 crore").unit == "INR"
    assert q("$3.2 billion").unit == "USD"


def test_currencies_are_never_converted():
    assert not comparable_units(q("100", "USD", "million"), q("100", "INR", "million"))
    assert comparable_units(q("100", "INR", "million"), q("1", "INR", "billion"))


def test_negatives_in_accounting_parentheses():
    assert q("(2,491.86)", "INR", "million").value == pytest.approx(-2.49186e9)


def test_formatting_round_trips_the_written_form():
    assert format_value(q("8,142", "INR", "crore")) == "₹8,142 crore"
    assert format_value(q("6.5", "percent")) == "6.5%"


def test_unparseable():
    assert parse_quantity("not a number") is None
    assert parse_quantity(None) is None


def test_a_magnitude_in_a_rate_denominator_does_not_scale_the_value():
    """"0.56 per one million-person hours worked" is an injury frequency of
    0.56. The magnitude scan searched the whole blob, found "million" inside
    the rate's denominator and multiplied by it, storing 560,000 -- and the
    explanation then read "0.56 million" for a safety statistic. The model had
    correctly returned no magnitude at all; this was ours.
    """
    q = parse_quantity("0.56", "per one million-person hours worked")
    assert q.value == 0.56, f"rate scaled by its own denominator: {q.value}"
    assert q.magnitude is None

    for unit in ("per million people", "per one lakh population",
                 "per a thousand live births"):
        assert parse_quantity("2.4", unit).value == 2.4, unit

    # A magnitude BEFORE "per" still applies -- it scales the numerator.
    q = parse_quantity("12.5", "crore per annum")
    assert q.value == 12.5e7 and q.magnitude == "crore"
    q = parse_quantity("81,415.38", "Rs. million")
    assert q.value == 81415.38e6 and q.magnitude == "million"


def test_a_currency_the_extractor_dropped_is_read_back_from_the_quote():
    """"Rs. 1,000 crore" comes back as value="1,000", unit="crore": the scale
    word lands in the unit field and the currency is left in the sentence. The
    figure then normalized to a unitless count, so it would not compare against
    the same amount written "Rs. 10,000 million" -- the cross-magnitude match
    this system is built to make.
    """
    quote = "Northwind Logistics recorded revenue of Rs. 1,000 crore in FY2024."
    a = parse_quantity("1,000", "crore", None, context=quote)
    b = parse_quantity("10,000", "Rs. million", None)
    assert a.unit == "INR", f"currency not recovered: {a.unit}"
    assert comparable_units(a, b) and agree(a, b)[0], f"{a.value} vs {b.value}"

    # Only when the unit field is nothing but a scale word. A stated unit wins,
    # and a fact with no unit at all is NOT given a currency from its sentence.
    counted = parse_quantity(
        "1,200", "people", None,
        context="The Company employed 1,200 people; revenue was Rs. 10,000 million.")
    assert counted.unit == "person", counted.unit
    bare = parse_quantity(
        "1,200", None, None,
        context="The Company employed 1,200 people; revenue was Rs. 10,000 million.")
    assert bare.unit == "count", f"a unitless count was given a currency: {bare.unit}"

    # No context, no change in behaviour.
    assert parse_quantity("1,000", "crore", None).unit == "count"
