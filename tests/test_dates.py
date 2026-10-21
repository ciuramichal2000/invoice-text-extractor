import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date

import pytest

from invoiceextract.dates import DateError, infer_slash_order, parse_date


@pytest.mark.parametrize(
    "text",
    [
        "2024-05-14",
        "14.05.2024",
        "14/05/2024",
        "14 May 2024",
        "14th May 2024",
        "14 may 2024",
        "14  MAY  2024",
        "14 May. 2024",
        "14.5.2024",
    ],
)
def test_every_supported_format_reads_the_same_day(text):
    reading = parse_date(text)
    assert reading.candidates == (date(2024, 5, 14),)
    assert not reading.is_ambiguous


def test_month_names_and_abbreviations():
    assert parse_date("1st June 2024").candidates == (date(2024, 6, 1),)
    assert parse_date("30 Sept 2024").candidates == (date(2024, 9, 30),)
    assert parse_date("3 Sep 2024").candidates == (date(2024, 9, 3),)
    assert parse_date("31 December 2023").candidates == (date(2023, 12, 31),)
    assert parse_date("5 Jul 2024").candidates == (date(2024, 7, 5),)


def test_leap_day_is_valid_only_in_leap_years():
    assert parse_date("29.02.2024").candidates == (date(2024, 2, 29),)
    with pytest.raises(DateError):
        parse_date("29.02.2023")
    with pytest.raises(DateError):
        parse_date("29.02.1900")  # divisible by 100 but not 400


@pytest.mark.parametrize(
    "text",
    ["2024-13-01", "2024-00-10", "31.04.2024", "0000-01-01", "32/01/2024", "31 June 2024"],
)
def test_impossible_calendar_dates_raise(text):
    with pytest.raises(DateError, match="not a valid calendar date"):
        parse_date(text)


@pytest.mark.parametrize("text", ["", "14.5.24", "2024/05/14", "May 14 2024", "14 Foo 2024", "yesterday"])
def test_unsupported_formats_raise(text):
    with pytest.raises(DateError, match="unsupported date format"):
        parse_date(text)


def test_slash_date_with_day_above_twelve_is_day_first_without_any_hint():
    assert parse_date("14/05/2024").candidates == (date(2024, 5, 14),)


def test_slash_date_with_month_above_twelve_is_month_first_without_any_hint():
    assert parse_date("05/14/2024").candidates == (date(2024, 5, 14),)


def test_slash_date_with_both_parts_up_to_twelve_reports_both_readings():
    reading = parse_date("03/04/2024")
    assert reading.is_ambiguous
    assert reading.candidates == (date(2024, 3, 4), date(2024, 4, 3))


def test_slash_date_with_equal_parts_is_not_ambiguous():
    assert parse_date("05/05/2024").candidates == (date(2024, 5, 5),)


def test_slash_order_hint_picks_one_reading():
    assert parse_date("03/04/2024", slash_order="dmy").candidates == (date(2024, 4, 3),)
    assert parse_date("03/04/2024", slash_order="mdy").candidates == (date(2024, 3, 4),)


def test_slash_order_hint_that_makes_the_date_impossible_raises():
    with pytest.raises(DateError):
        parse_date("05/14/2024", slash_order="dmy")


def test_unknown_slash_order_is_rejected():
    with pytest.raises(ValueError):
        parse_date("03/04/2024", slash_order="ymd")


def test_infer_slash_order_from_a_day_above_twelve():
    assert infer_slash_order("issued 03/04/2024, due 17/04/2024") == "dmy"


def test_infer_slash_order_from_a_second_part_above_twelve():
    assert infer_slash_order("issued 03/04/2024, due 04/17/2024") == "mdy"


def test_infer_slash_order_gives_up_on_conflicting_or_missing_evidence():
    assert infer_slash_order("17/04/2024 and 04/17/2024") is None
    assert infer_slash_order("only 03/04/2024 here") is None
    assert infer_slash_order("no slash dates, just 14.05.2024") is None
    assert infer_slash_order("") is None


def test_infer_slash_order_ignores_fragments_of_longer_numbers():
    assert infer_slash_order("ref 17/04/20245 and 2024/17/04/2024") is None
