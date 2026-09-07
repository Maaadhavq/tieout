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
