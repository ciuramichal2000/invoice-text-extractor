import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from decimal import Decimal

import pytest

from invoiceextract.amounts import (
    AmountError,
    AmountReading,
    format_amount,
    infer_style,
    looks_like_money,
    parse_amount,
)


@pytest.mark.parametrize(
    ("token", "value", "style"),
    [
        ("1 234,56", "1234.56", "pl"),
        ("1\u00a0234,56", "1234.56", "pl"),
        ("1,234.56", "1234.56", "en"),
        ("1.234,56", "1234.56", "pl"),
        ("1234.56", "1234.56", "en"),
        ("1234,56", "1234.56", "pl"),
        ("1 234 567,89", "1234567.89", "pl"),
        ("1,234,567.89", "1234567.89", "en"),
        ("1,234,567", "1234567", "en"),
        ("1.234.567", "1234567", "pl"),
        ("0,500", "0.500", "pl"),
        ("1 234,567", "1234.567", "pl"),
        ("1 234", "1234", None),
        ("230", "230", None),
        ("7", "7", None),
        ("-45,10", "-45.10", "pl"),
        ("\u221245.10", "-45.10", "en"),
    ],
)
def test_unambiguous_notations_give_the_exact_decimal(token, value, style):
    reading = parse_amount(token)
    assert reading.candidates == (Decimal(value),)
    assert reading.style == style


@pytest.mark.parametrize(("token", "decimal_reading", "grouped_reading"), [
    ("1,234", "1.234", "1234"),
    ("1.234", "1.234", "1234"),
    ("12.345", "12.345", "12345"),
    ("999,999", "999.999", "999999"),
])
def test_single_separator_before_three_digits_is_ambiguous(token, decimal_reading, grouped_reading):
    reading = parse_amount(token)
    assert reading.is_ambiguous
    assert reading.candidates == (Decimal(decimal_reading), Decimal(grouped_reading))
    assert reading.style is None


def test_style_hint_resolves_an_ambiguous_token_either_way():
    assert parse_amount("1,234", style="en").candidates == (Decimal("1234"),)
    assert parse_amount("1,234", style="pl").candidates == (Decimal("1.234"),)
    assert parse_amount("1.234", style="pl").candidates == (Decimal("1234"),)
    assert parse_amount("1.234", style="en").candidates == (Decimal("1.234"),)


def test_style_hint_does_not_override_a_token_that_proves_its_own_notation():
    assert parse_amount("1 234,56", style="en").candidates == (Decimal("1234.56"),)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "abc",
        "--5",
        "1,23,456",      # group of two digits
        "12 34",         # group of two digits
        "1..5",          # empty group
        "1,234.567,89",  # decimal separator used as thousands separator
        "1.234,56,7",
        "1 234.567,89",  # space and dot both used for grouping
        "1 2345,00",
        "1 234 567 890 123 456,00",  # 16 integer digits
    ],
)
def test_malformed_tokens_raise_amount_error(token):
    with pytest.raises(AmountError):
        parse_amount(token)


def test_unknown_style_name_is_rejected():
    with pytest.raises(ValueError):
        parse_amount("1,234", style="de")


def test_infer_style_needs_agreement_between_tokens():
    pl = parse_amount("1 230,00")
    en = parse_amount("1,230.00")
    neutral = parse_amount("230")
    assert infer_style([pl, neutral]) == "pl"
    assert infer_style([en, neutral]) == "en"
    assert infer_style([pl, en]) is None
    assert infer_style([neutral]) is None
    assert infer_style([]) is None


def test_infer_style_ignores_ambiguous_tokens():
    assert infer_style([parse_amount("1,234"), parse_amount("5.00")]) == "en"


def test_format_amount_is_two_decimals_unless_that_would_lose_digits():
    assert format_amount(Decimal("1000")) == "1000.00"
    assert format_amount(Decimal("0.5")) == "0.50"
    assert format_amount(Decimal("1234.560")) == "1234.56"
    assert format_amount(Decimal("1.234")) == "1.234"
    assert format_amount(Decimal("-12.3")) == "-12.30"


def test_looks_like_money_rejects_bare_integers_and_tax_numbers():
    assert looks_like_money("1 230,00", False)
    assert looks_like_money("1,230.00", False)
    assert looks_like_money("1,234", True)
    assert not looks_like_money("23", False)
    assert not looks_like_money("5260250995", False)
    assert not looks_like_money("1,234", False)


def test_reading_without_candidates_is_never_ambiguous():
    assert not AmountReading((Decimal("1"),), None).is_ambiguous
