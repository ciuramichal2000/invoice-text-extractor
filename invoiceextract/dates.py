"""Parsing of invoice dates: 2024-05-14, 14.05.2024, 14/05/2024 and 14 May 2024.

Slash dates are the trap: 03/04/2024 is 3 April in Europe and 4 March in the
US. The parser reports both readings unless the document itself settles it.
"""

import re
from dataclasses import dataclass
from datetime import date

SLASH_DMY = "dmy"
SLASH_MDY = "mdy"

_MONTH_NAMES = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
_MONTH_NUMBERS = {
    name: number
    for number, name in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1
    )
}

# Used by the label patterns to locate dates in text.
DATE_BODY = (
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\.\d{1,2}\.\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{4}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?[ ]+(?:{_MONTH_NAMES})(?![A-Za-z])\.?[ ]+\d{{4}}"
)

_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DOTTED = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_SLASHED = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_WRITTEN = re.compile(
    rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES})\.?\s+(\d{{4}})", re.IGNORECASE
)
_SLASH_IN_TEXT = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/\d{4}(?![\d/])")


class DateError(ValueError):
    """Raised when text is not a supported or not a real calendar date."""


@dataclass(frozen=True)
class DateReading:
    """One or two plausible dates for a token (two only for ambiguous slash dates)."""

    candidates: tuple[date, ...]

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1


def infer_slash_order(text: str) -> str | None:
    """Decide day/month order for slash dates from any unambiguous one in the text.

    17/04/2024 can only be day-first and 04/17/2024 only month-first. If the
    document contains both kinds (or neither), no order is inferred.
    """
    orders = set()
    for first, second in _SLASH_IN_TEXT.findall(text):
        if int(first) > 12 >= int(second):
            orders.add(SLASH_DMY)
        elif int(second) > 12 >= int(first):
            orders.add(SLASH_MDY)
    return next(iter(orders)) if len(orders) == 1 else None


def parse_date(text: str, slash_order: str | None = None) -> DateReading:
    """Parse one date token; ``slash_order`` pins the meaning of ``a/b/yyyy``."""
    if slash_order not in (None, SLASH_DMY, SLASH_MDY):
        raise ValueError(f"unknown slash order {slash_order!r}")
    token = " ".join(text.split())

    if match := _ISO.fullmatch(token):
        year, month, day = match.groups()
        return DateReading((_calendar_date(token, year, month, day),))
    if match := _DOTTED.fullmatch(token):
        day, month, year = match.groups()
        return DateReading((_calendar_date(token, year, month, day),))
    if match := _WRITTEN.fullmatch(token):
        day, month_name, year = match.groups()
        month = _MONTH_NUMBERS[month_name[:3].lower()]
        return DateReading((_calendar_date(token, year, month, day),))
    if match := _SLASHED.fullmatch(token):
        return DateReading(_slash_candidates(token, *match.groups(), slash_order))
    raise DateError(f"unsupported date format: {text!r}")


def _slash_candidates(token: str, first: str, second: str, year: str, order: str | None) -> tuple[date, ...]:
    day_first = _maybe_date(year, second, first)
    month_first = _maybe_date(year, first, second)
    if order == SLASH_DMY:
        options = [day_first]
    elif order == SLASH_MDY:
        options = [month_first]
    else:
        options = [day_first, month_first]
    found = sorted({option for option in options if option is not None})
    if not found:
        raise DateError(f"{token!r} is not a valid calendar date")
    return tuple(found)


def _maybe_date(year: str | int, month: str | int, day: str | int) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _calendar_date(token: str, year: str | int, month: str | int, day: str | int) -> date:
    result = _maybe_date(year, month, day)
    if result is None:
        raise DateError(f"{token!r} is not a valid calendar date")
    return result
