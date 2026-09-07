from datetime import date

import pytest

from tieout.normalize.periods import parse_period, relation


def iv(s):
    p = parse_period(s)
    assert p is not None, f"failed to parse {s!r}"
    return p.start, p.end


FY2425 = (date(2024, 4, 1), date(2025, 3, 31))
FY2324 = (date(2023, 4, 1), date(2024, 3, 31))


@pytest.mark.parametrize("raw", ["FY25", "FY 25", "FY2025", "fiscal 2025", "fiscal year 2025"])
def test_single_year_token_is_the_ending_year(raw):
    assert iv(raw) == FY2425


@pytest.mark.parametrize("raw", ["2024-25", "FY2024-25", "FY2024/25", "FY 2024/2025", "2024–25"])
def test_span_token_is_anchored_on_the_starting_year(raw):
    assert iv(raw) == FY2425


def test_the_three_publishers_agree_on_one_interval():
    """The whole point: Economic Survey, RBI and IMF spell the same year
    three different ways."""
    assert iv("FY25") == iv("2024-25") == iv("FY2024/25") == FY2425


def test_delhivery_fy24_is_a_different_year():
    assert iv("FY24") == FY2324
    assert iv("FY24") != iv("FY25")


def test_quarters_of_an_april_march_year():
    assert iv("Q4 FY24") == (date(2024, 1, 1), date(2024, 3, 31))
    assert iv("Q1 FY24") == (date(2023, 4, 1), date(2023, 6, 30))
    assert iv("Q2FY25") == (date(2024, 7, 1), date(2024, 9, 30))


def test_calendar_year():
    assert iv("CY2024") == (date(2024, 1, 1), date(2024, 12, 31))


def test_bare_fiscal_quarter_is_flagged_ambiguous():
    p = parse_period("2025Q2")
    assert p.grain == "quarterly"
    assert p.confidence < 0.7, "YYYYQn is genuinely ambiguous and must say so"
    assert "ambiguous" in p.note


def test_instants():
    assert iv("March 31, 2024") == (date(2024, 3, 31), date(2024, 3, 31))
    assert iv("as of 31 March 2024") == (date(2024, 3, 31), date(2024, 3, 31))


def test_unparseable_returns_none():
    assert parse_period("") is None
    assert parse_period(None) is None
    assert parse_period("during the period under review") is None


def test_relations():
    q4 = parse_period("Q4 FY24")
    fy = parse_period("FY24")
    assert relation(fy, q4) == "contains"
    assert relation(q4, fy) == "contained_by"
    assert relation(parse_period("FY24"), parse_period("FY25")) == "disjoint"
    assert relation(parse_period("FY25"), parse_period("2024-25")) == "same"


def test_quarter_with_connecting_words():
    """'Q2 of FY25' fell through to the plain-FY rule and was read as the whole
    of FY25, which turned quarter-versus-year pairs into false contradictions
    on the live corpus. All these forms must give the same quarter."""
    want = (date(2024, 7, 1), date(2024, 9, 30))
    for raw in ["Q2 FY25", "Q2FY25", "Q2 of FY25", "Q2 - FY25", "Q2/FY25",
                "second quarter of FY25", "Q2 of the FY25"]:
        assert iv(raw) == want, f"{raw!r} did not resolve to Q2 FY25"


def test_period_ended_phrasings():
    """The standard financial phrasing. A period stated this way must not
    collapse to the instant it ends on."""
    assert iv("for the year ended March 31, 2024") == (date(2023, 4, 1), date(2024, 3, 31))
    assert iv("year ended March 31, 2021") == (date(2020, 4, 1), date(2021, 3, 31))
    assert iv("quarter ended September 30, 2024") == (date(2024, 7, 1), date(2024, 9, 30))
    assert iv("three months ended June 30, 2025") == (date(2025, 4, 1), date(2025, 6, 30))
    assert iv("nine months period ended December 31, 2021") == (date(2021, 4, 1), date(2021, 12, 31))


def test_a_year_ended_matches_the_fiscal_year_it_is():
    """'for the year ended March 31, 2024' and 'FY24' are the same interval."""
    assert iv("for the year ended March 31, 2024") == iv("FY24")


def test_a_quarter_is_contained_by_its_year_not_equal_to_it():
    q = parse_period("Q2 of FY25")
    y = parse_period("FY25")
    assert relation(y, q) == "contains"
    assert relation(q, y) == "contained_by"


def test_a_quarter_inherits_the_years_own_convention():
    """Found live: 'first quarter of FY2025/26' was landing twelve months early.
    In a span form the first year is the year the FY STARTS, and the quarter has
    to follow whatever the year form means -- which is why the parser splits the
    quarter off and re-parses the remainder rather than matching both at once."""
    assert iv("first quarter of FY2025/26") == (date(2025, 4, 1), date(2025, 6, 30))
    assert iv("Q1 FY2025/26") == (date(2025, 4, 1), date(2025, 6, 30))
    # ... and a single-year form still means the year it ends
    assert iv("Q1 FY25") == (date(2024, 4, 1), date(2024, 6, 30))


def test_a_quarter_without_the_letters_fy():
    """'Q1:2024-25' lost its quarter entirely because the pattern demanded an
    'FY', and a quarterly figure was then compared against a full year."""
    assert iv("Q1:2024-25") == (date(2024, 4, 1), date(2024, 6, 30))
    assert iv("Q1 2024-25") == (date(2024, 4, 1), date(2024, 6, 30))
    assert iv("fourth quarter of 2024-25") == (date(2025, 1, 1), date(2025, 3, 31))
    assert parse_period("Q1:2024-25") != parse_period("2024-25")
