"""Parsing of money amounts written in Polish (1 234,56) or English (1,234.56) notation.

The hard part is that a single separator is genuinely ambiguous: "1,234" is
1.234 to a Pole and 1234 to an Englishman. Instead of guessing, the parser
returns both readings and lets the caller resolve them with document context.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

STYLE_PL = "pl"  # 1 234,56  comma is the decimal separator
STYLE_EN = "en"  # 1,234.56  dot is the decimal separator

_MAX_INTEGER_DIGITS = 15

CURRENCY_CODES = frozenset(
    "PLN EUR USD GBP CHF CZK SEK NOK DKK HUF RON BGN CAD AUD JPY CNY UAH TRY".split()
)
_CURRENCY_SYMBOLS = {"zł": "PLN", "€": "EUR", "£": "GBP"}

# Building blocks shared with the label patterns. A bare "$" is deliberately
# absent: it can mean USD, CAD, AUD and more, so guessing would be wrong.
CURRENCY_PATTERN = (
    r"(?<![A-Za-z])(?:" + "|".join(sorted(CURRENCY_CODES)) + r"|zł|€|£)(?![A-Za-z])"
)
# Thousands groups need exactly three digits, so "1 000,00 230,00" splits into two numbers.
NUMBER_PATTERN = r"-?(?:\d{1,3}(?:[ .,]\d{3})+|\d+)(?:[.,]\d+)?"


class AmountError(ValueError):
    """Raised when text is not a well-formed amount."""


@dataclass(frozen=True)
class AmountReading:
    """All plausible values of one amount token.

    ``style`` is the notation the token itself proves (None when it does not,
    as for plain "1234" or an ambiguous "1,234").
    """

    candidates: tuple[Decimal, ...]
    style: str | None

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1


def currency_code(token: str) -> str:
    """Map a currency symbol such as "zł" to its ISO code; ISO codes pass through."""
    return _CURRENCY_SYMBOLS.get(token, token)


def looks_like_money(number: str, has_currency: bool) -> bool:
    """Bare integers (years, rates, tax numbers) are not amounts.

    Money either carries a currency or ends in a two-digit fraction.
    """
    return has_currency or re.search(r"[.,][0-9]{2}$", number) is not None


def format_amount(value: Decimal) -> str:
    """Canonical text: two decimals when that is lossless, otherwise the exact digits."""
    cents = value.quantize(Decimal("0.01"))
    return f"{cents:f}" if cents == value else f"{value.normalize():f}"


def infer_style(readings: Iterable[AmountReading]) -> str | None:
    """The notation shared by every token that reveals one; None if none or they disagree."""
    styles = {reading.style for reading in readings if reading.style}
    return next(iter(styles)) if len(styles) == 1 else None


def parse_amount(token: str, style: str | None = None) -> AmountReading:
    """Parse one amount token.

    ``style`` (STYLE_PL / STYLE_EN) resolves tokens whose notation is ambiguous.
    """
    if style not in (None, STYLE_PL, STYLE_EN):
        raise ValueError(f"unknown number style {style!r}")
    text = re.sub(r"[\u00a0\u2009\u202f]", " ", token).strip()
    negative = text.startswith(("-", "\u2212"))
    body = text[1:].strip() if negative else text
    if not re.fullmatch(r"[0-9]|[0-9][0-9 .,]*[0-9]", body):
        raise AmountError(f"not an amount: {token!r}")

    has_dot, has_comma = "." in body, "," in body
    if has_dot and has_comma:
        return _parse_both_separators(body, negative, token)
    if not has_dot and not has_comma:
        return AmountReading((_to_decimal(_join_groups(body, ""), "", negative),), None)

    separator = "." if has_dot else ","
    decimal_style = STYLE_EN if separator == "." else STYLE_PL
    parts = body.split(separator)
    if len(parts) > 2:
        # A repeated separator can only be thousands grouping: 1,234,567 or 1.234.567.
        grouping_style = STYLE_PL if separator == "." else STYLE_EN
        return AmountReading((_to_decimal(_join_groups(body, separator), "", negative),), grouping_style)

    integer_part, fraction = parts
    if _is_ambiguous_shape(integer_part, fraction):
        as_decimal = _to_decimal(integer_part, fraction, negative)
        as_grouped = _to_decimal(integer_part + fraction, "", negative)
        if style is None:
            return AmountReading((as_decimal, as_grouped), None)
        separator_is_decimal = decimal_style == style
        return AmountReading((as_decimal if separator_is_decimal else as_grouped,), None)
    return AmountReading((_to_decimal(_join_groups(integer_part, ""), fraction, negative),), decimal_style)


def _parse_both_separators(body: str, negative: bool, token: str) -> AmountReading:
    """When both "." and "," occur, the one that comes last is the decimal separator."""
    last = max(body.rfind("."), body.rfind(","))
    decimal_separator = body[last]
    group_separator = "." if decimal_separator == "," else ","
    integer_part, fraction = body[:last], body[last + 1:]
    if decimal_separator in integer_part or not fraction.isdigit():
        raise AmountError(f"inconsistent separators in {token!r}")
    style = STYLE_PL if decimal_separator == "," else STYLE_EN
    digits = _join_groups(integer_part, group_separator)
    return AmountReading((_to_decimal(digits, fraction, negative),), style)


def _is_ambiguous_shape(integer_part: str, fraction: str) -> bool:
    """One separator, three digits after it, and a short integer part: 1,234 / 12.345.

    A leading zero ("0,500") or an integer part already containing a space
    group ("1 234,567") can only be a decimal number.
    """
    return (
        " " not in integer_part
        and len(fraction) == 3
        and 1 <= len(integer_part) <= 3
        and not integer_part.startswith("0")
    )


def _join_groups(integer_text: str, group_separator: str) -> str:
    """Strip thousands separators after checking that they sit in valid places."""
    separators = {char for char in integer_text if not char.isdigit()}
    if len(separators) > 1 or (separators and separators != {" "} and separators != {group_separator}):
        raise AmountError(f"mixed or unexpected thousands separators in {integer_text!r}")
    groups = re.split(r"[^0-9]", integer_text)
    if len(groups) > 1 and (not 1 <= len(groups[0]) <= 3 or any(len(g) != 3 for g in groups[1:])):
        raise AmountError(f"misplaced thousands separator in {integer_text!r}")
    return "".join(groups)


def _to_decimal(digits: str, fraction: str, negative: bool) -> Decimal:
    if len(digits) > _MAX_INTEGER_DIGITS:
        raise AmountError(f"amount has more than {_MAX_INTEGER_DIGITS} integer digits")
    value = Decimal(f"{digits}.{fraction}" if fraction else digits)
    return -value if negative and value else value
