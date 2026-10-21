"""Polish and English label vocabularies, and the helper that reads the value after a label.

Invoice generators disagree on layout but nearly all print a short label next
to each value. Keeping the vocabularies apart from the extraction logic makes
them easy to extend. Polish diacritics are optional in every pattern because
PDF-to-text tools often drop them.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .amounts import CURRENCY_PATTERN, NUMBER_PATTERN
from .dates import DATE_BODY

_BLANK_TAIL_CHARS = " \t:.-–—=_"

_TOTAL_WORDS = r"(?:Razem|Suma|Warto[śs][ćc]|Kwota|[ŁL][ąa]cznie|Og[óo][łl]em)"
_RATE = r"\d{1,2}(?:[.,]\d+)?"


@dataclass(frozen=True)
class LabelHit:
    """A label followed by a value; ``line`` is where the value was found."""

    line: int
    label: re.Match[str]
    value: re.Match[str]


def compile_labels(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


def find_after_labels(
    lines: Sequence[str],
    label_set: Sequence[re.Pattern[str]],
    value: re.Pattern[str],
) -> list[LabelHit]:
    """Find every ``label value`` pair.

    The value is taken from the rest of the label's line; if the label ends its
    line (text extraction often breaks the line there), from the start of the
    next non-blank line. The same value reached through two overlapping labels
    ("Payment due" and "Due date") is reported once.
    """
    hits: list[LabelHit] = []
    seen: set[tuple[int, int]] = set()
    for index, line in enumerate(lines):
        for label in label_set:
            for label_match in label.finditer(line):
                tail = line[label_match.end():]
                target, offset = index, label_match.end()
                value_match = value.match(tail)
                if value_match is None and not tail.strip(_BLANK_TAIL_CHARS):
                    following = _next_non_blank(lines, index)
                    if following is not None:
                        target, offset = following, 0
                        value_match = value.match(lines[following])
                if value_match is None:
                    continue
                key = (target, offset + value_match.end())
                if key not in seen:
                    seen.add(key)
                    hits.append(LabelHit(target, label_match, value_match))
    return hits


def _next_non_blank(lines: Sequence[str], index: int) -> int | None:
    for candidate in range(index + 1, len(lines)):
        if lines[candidate].strip():
            return candidate
    return None


# --- invoice number -------------------------------------------------------

INVOICE_NUMBER_LABELS = compile_labels(
    r"\bFaktura(?:\s+(?:VAT|pro\s*forma|koryguj[ąa]ca))*\s+(?:nr|numer|no)\b\.?",
    r"\b(?:Nr|Numer)\s+faktury\b",
    r"\bInvoice\s*(?:(?:no|nr|number|num)\b\.?|#)",
    r"\bInvoice\s*:",
)
# An optional short prefix ("FV 12/2024"), then one token that must contain a digit.
INVOICE_NUMBER_VALUE = re.compile(
    r"[\s:.\-–—#№=]*(?P<v>(?:[A-Za-z]{1,5} )?(?=[A-Za-z0-9/\-_.]*\d)[A-Za-z0-9][A-Za-z0-9/\-_.]*)"
)

# --- dates ----------------------------------------------------------------

ISSUE_DATE_LABELS = compile_labels(
    r"\bData\s+wystawienia\b",
    r"\bData\s+faktury\b",
    r"\bWystawion[ao](?:\s+dnia)?\b",
    r"\b(?:Issue|Invoice)\s+date\b",
    r"\bDate\s+of\s+issue\b",
    r"\bDate\s+issued\b",
    r"\bIssued(?:\s+on)?\b",
)
DUE_DATE_LABELS = compile_labels(
    r"\bTermin\s+(?:p[łl]atno[śs]ci|zap[łl]aty)\b",
    r"\bP[łl]atne\s+do\b",
    r"\bDue\s+date\b",
    r"\bDate\s+due\b",
    r"\bPayment\s+due(?:\s+date)?\b",
    r"\bDue\s+(?:on|by)\b",
    r"\bPay(?:able)?\s+(?:by|until)\b",
)
DATE_VALUE = re.compile(rf"[\s:.\-–—=]*(?P<v>{DATE_BODY})(?![\d/])", re.IGNORECASE)

# --- NIP (Polish tax id) --------------------------------------------------

NIP_LABELS = compile_labels(
    r"(?<![A-Za-z])(?:NIP|VAT[\s-]*(?:ID|No\.?|Number)|Tax[\s-]*(?:ID|No\.?|Number)|TIN)(?![A-Za-z])"
)
# Digits with single spaces/hyphens between them; the optional PL is the EU VAT prefix.
NIP_VALUE = re.compile(r"[\s:.\-#)(]*(?:PL[ \-]?)?(?P<v>\d(?:[ \-]?\d)*)", re.IGNORECASE)

# --- amounts --------------------------------------------------------------

NET_LABELS = compile_labels(
    rf"\b{_TOTAL_WORDS}\s+netto\b",
    r"\bTotal\s+net\b(?:\s+(?:amount|value))?",
    r"\bNet\s+(?:amount|total|value)\b",
    r"\bSub-?total\b",
    r"\b(?:Total|Amount)\s+(?:excl(?:uding|\.)?|without)\s+(?:VAT|tax)\b",
    # Bare "Netto"/"Net" only at line start, so item rows such as "Cena netto" are ignored.
    r"^\s*(?:Netto|Net)\b",
)
GROSS_LABELS = compile_labels(
    rf"\b{_TOTAL_WORDS}\s+brutto\b",
    r"\b(?:Razem\s+)?do\s+zap[łl]aty\b",
    r"\bTotal\s+(?:gross|due|payable|amount(?:\s+due)?|incl(?:uding|\.)?\s*(?:VAT|tax)|to\s+pay)\b",
    r"\bGross\s+(?:amount|total|value)\b",
    r"\bAmount\s+(?:due|payable)\b",
    r"\bGrand\s+total\b",
    r"\bBalance\s+due\b",
    r"^\s*(?:Brutto|Total|Razem|Suma|Gross)\b",
)
# A VAT label with a rate ("VAT 23%", "Kwota VAT (8%)") is one line of a per-rate
# breakdown; without a rate it is the invoice total.
VAT_LABELS = compile_labels(
    rf"\b(?:(?:{_TOTAL_WORDS}|Podatek|Total)\s+)?(?:VAT|Tax)\b(?:\s+(?:amount|total))?"
    rf"(?:\s*\(?\s*(?P<rate>{_RATE})\s*%\s*\)?)?",
    rf"\bKwota\s+podatku\b(?:\s*\(?\s*(?P<rate>{_RATE})\s*%\s*\)?)?",
)
# After a label: optional "(EUR)" in the label itself, separators, optional
# leading currency, the number, optional trailing currency. Percentages are not amounts.
AMOUNT_VALUE = re.compile(
    rf"(?:\s*\(\s*(?P<paren>{CURRENCY_PATTERN})\s*\))?"
    rf"[\s:.=_]*(?:(?P<pre>{CURRENCY_PATTERN})\s*)?"
    rf"(?<![\d.,])(?P<num>{NUMBER_PATTERN})(?![.,]?\d)(?!\s*%)"
    rf"(?:\s*(?P<post>{CURRENCY_PATTERN}))?"
)
# The same shape without a label, for the cells of a "Razem  net  vat  gross" row.
AMOUNT_TOKEN = re.compile(
    rf"(?:(?P<pre>{CURRENCY_PATTERN})\s*)?"
    rf"(?<![\d.,])(?P<num>{NUMBER_PATTERN})(?![.,]?\d)(?!\s*%)"
    rf"(?:\s*(?P<post>{CURRENCY_PATTERN}))?"
)
TOTALS_ROW = re.compile(r"^\s*(?:Razem|Suma|Og[óo][łl]em|Total|Totals|Sum)\b[\s:.]*", re.IGNORECASE)

# --- currency -------------------------------------------------------------

CURRENCY_LABELS = compile_labels(r"\b(?:Currency|Waluta)\b")
CURRENCY_VALUE = re.compile(r"[\s:=\-]*(?P<v>[A-Z]{3})\b")
CURRENCY_TOKEN = re.compile(CURRENCY_PATTERN)

# --- IBAN -----------------------------------------------------------------

# Four-character groups; the last group may be shorter. Lookarounds stop the
# match from running into neighbouring words.
IBAN_SPACED = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{2} ?\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![A-Za-z0-9])"
)
# Polish domestic account number: 26 digits, usually printed without the PL prefix.
PL_DOMESTIC_ACCOUNT = re.compile(r"(?<![0-9A-Za-z])\d{2}(?: ?\d{4}){6}(?!\d)")
IBAN_WORD = re.compile(r"\bIBAN\b", re.IGNORECASE)
ACCOUNT_LABEL = re.compile(r"\b(?:IBAN|konto|rachunek|rachunku|account)\b", re.IGNORECASE)
