"""Field extraction and cross-checks for the plain text of an invoice.

Every field is gathered as a list of *mentions* (a raw string, its line and its
normalised readings). One resolver then turns the mentions into a single
result: found, ambiguous, missing or failed validation. Keeping that decision
in one place gives all fields the same semantics.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import product

from . import labels
from .amounts import (
    CURRENCY_CODES,
    AmountError,
    AmountReading,
    currency_code,
    format_amount,
    infer_style,
    looks_like_money,
    parse_amount,
)
from .dates import DateError, infer_slash_order, parse_date
from .labels import find_after_labels
from .models import (
    CheckStatus,
    CrossCheck,
    FieldResult,
    FieldStatus,
    InvoiceExtraction,
    InvoiceTextError,
)
from .parties import BUYER, SELLER, PartyBlock, find_party_blocks
from .validators import IBAN_LENGTHS, iban_error, nip_error, normalize_iban

DEFAULT_TOLERANCE = Decimal("0.01")

_UNICODE_SPACES = {ord(char): " " for char in "\u00a0\u2007\u2009\u202f"}
# Shortest and longest string that can be an IBAN; shorter look-alikes such as
# an EU VAT id ("PL5260250995") must not be mistaken for one.
_IBAN_LENGTH_RANGE = (15, 34)


@dataclass(frozen=True)
class _Mention:
    """One occurrence of a field in the text, already normalised."""

    raw: str
    line: int  # 1-based
    values: tuple[str, ...] = ()  # empty when the text could not be parsed
    error: str | None = None


@dataclass(frozen=True)
class _AmountMention:
    raw: str
    line: int
    reading: AmountReading | None
    error: str | None
    currency: str | None
    rate: str | None = None


def extract_invoice(text: str, *, tolerance: Decimal | int | str = DEFAULT_TOLERANCE) -> InvoiceExtraction:
    """Extract structured fields from invoice text and cross-check the amounts.

    ``tolerance`` is the largest accepted gap between net + VAT and gross.
    Raises InvoiceTextError for empty text, TypeError for non-text input and
    ValueError for an unusable tolerance.
    """
    limit = _coerce_tolerance(tolerance)
    if not isinstance(text, str):
        raise TypeError(f"invoice text must be str, got {type(text).__name__}")
    lines = _normalise(text)
    if not any(lines):
        raise InvoiceTextError("invoice text is empty")

    slash_order = infer_slash_order("\n".join(lines))
    blocks = find_party_blocks(lines)
    amounts = _collect_amounts(lines)

    net = _amount_field(amounts["net"])
    vat = _vat_field(amounts["vat_total"], amounts["vat_rates"])
    gross = _amount_field(amounts["gross"])
    net, vat, gross = _resolve_by_arithmetic(net, vat, gross, limit)
    issue_date = _date_field(lines, labels.ISSUE_DATE_LABELS, slash_order)
    due_date = _date_field(lines, labels.DUE_DATE_LABELS, slash_order)

    fields = {
        "invoice_number": _invoice_number_field(lines),
        "issue_date": issue_date,
        "due_date": due_date,
        "seller_nip": _nip_field([b for b in blocks if b.role == SELLER]),
        "buyer_nip": _nip_field([b for b in blocks if b.role == BUYER]),
        "net_amount": net,
        "vat_amount": vat,
        "gross_amount": gross,
        "currency": _currency_field(lines, amounts["all"]),
        "iban": _iban_field(lines),
    }
    checks = (
        _check_totals(net, vat, gross, limit),
        _check_dates(issue_date, due_date),
    )
    return InvoiceExtraction(fields=fields, checks=checks)


def _coerce_tolerance(value: Decimal | int | str) -> Decimal:
    if isinstance(value, (float, bool)):
        raise TypeError("tolerance must be a Decimal, int or str, not a binary float or bool")
    try:
        tolerance = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"tolerance is not a number: {value!r}") from error
    if not tolerance.is_finite() or tolerance < 0:
        raise ValueError("tolerance must be a finite, non-negative number")
    return tolerance


def _normalise(text: str) -> list[str]:
    """Plain spaces, ASCII minus, expanded tabs, no trailing blanks: one canonical form."""
    text = text.translate(_UNICODE_SPACES).replace("\u200b", "").replace("\u2212", "-")
    return [line.expandtabs(8).rstrip() for line in text.splitlines()]


# --- the shared resolver --------------------------------------------------

def _resolve(mentions: Sequence[_Mention], sort_key: Callable[[str], object] = str) -> FieldResult:
    """Turn all mentions of a field into one result.

    Mentions may carry several readings (an ambiguous notation). The readings
    every mention agrees on decide the value, so "03/04/2024" in one place and
    "2024-04-03" in another settle each other.
    """
    if not mentions:
        return FieldResult(FieldStatus.MISSING)
    usable = [m for m in mentions if m.values]
    if not usable:
        first = mentions[0]
        return FieldResult(
            FieldStatus.FAILED_VALIDATION, value=first.raw, raw=first.raw, line=first.line, note=first.error
        )
    common = set(usable[0].values).intersection(*(m.values for m in usable[1:]))
    if len(common) == 1:
        (value,) = common
        holders = [m for m in usable if value in m.values]
        failure = next((m.error for m in holders if m.error), None)
        status = FieldStatus.FAILED_VALIDATION if failure else FieldStatus.FOUND
        return FieldResult(status, value=value, raw=holders[0].raw, line=holders[0].line, note=failure)
    if common:
        pool, note = common, "the notation allows more than one reading"
    else:
        pool = {value for m in usable for value in m.values}
        note = "conflicting values on lines " + ", ".join(str(n) for n in sorted({m.line for m in usable}))
    return FieldResult(
        FieldStatus.AMBIGUOUS,
        line=usable[0].line,
        candidates=tuple(sorted(pool, key=sort_key)),
        note=note,
    )


# --- invoice number and dates ---------------------------------------------

def _invoice_number_field(lines: Sequence[str]) -> FieldResult:
    mentions = []
    for hit in find_after_labels(lines, labels.INVOICE_NUMBER_LABELS, labels.INVOICE_NUMBER_VALUE):
        raw = hit.value.group("v")
        mentions.append(_Mention(raw, hit.line + 1, (raw.rstrip(".,;:-/_"),)))
    return _resolve(mentions)


def _date_field(lines: Sequence[str], label_set: Sequence[re.Pattern[str]], slash_order: str | None) -> FieldResult:
    mentions = []
    for hit in find_after_labels(lines, label_set, labels.DATE_VALUE):
        raw = hit.value.group("v")
        try:
            reading = parse_date(raw, slash_order)
        except DateError as error:
            mentions.append(_Mention(raw, hit.line + 1, (), str(error)))
        else:
            mentions.append(_Mention(raw, hit.line + 1, tuple(d.isoformat() for d in reading.candidates)))
    return _resolve(mentions)


# --- NIP and IBAN ---------------------------------------------------------

def _nip_field(blocks: Sequence[PartyBlock]) -> FieldResult:
    mentions = []
    for block in blocks:
        texts = [text for _, text in block.lines]
        for hit in find_after_labels(texts, labels.NIP_LABELS, labels.NIP_VALUE):
            raw = hit.value.group("v")
            digits = re.sub(r"\D", "", raw)
            document_line = block.lines[hit.line][0] + 1
            mentions.append(_Mention(raw, document_line, (digits,), nip_error(digits)))
    return _resolve(mentions)


def _trim_to_country_length(matched: str) -> str:
    """Cut off a word the greedy group pattern glued onto the IBAN ("... 2874 SWIFT").

    The cut is made only where the country's IBAN length ends exactly before a
    space, so a number with one digit too many is still reported as wrong.
    """
    expected = IBAN_LENGTHS.get(matched[:2])
    if expected is None:
        return matched
    counted = 0
    for position, char in enumerate(matched):
        counted += char != " "
        if counted == expected:
            rest = matched[position + 1:]
            return matched[: position + 1] if rest[:1] in ("", " ") else matched
    return matched


def _iban_field(lines: Sequence[str]) -> FieldResult:
    mentions = []
    for index, line in enumerate(lines):
        for match in labels.IBAN_SPACED.finditer(line):
            matched = _trim_to_country_length(match.group())
            compact = normalize_iban(matched)
            plausible_length = _IBAN_LENGTH_RANGE[0] <= len(compact) <= _IBAN_LENGTH_RANGE[1]
            known_or_labelled = compact[:2] in IBAN_LENGTHS or labels.IBAN_WORD.search(line)
            if plausible_length and known_or_labelled:
                mentions.append(_Mention(matched, index + 1, (compact,), iban_error(compact)))
        if labels.ACCOUNT_LABEL.search(line):
            for match in labels.PL_DOMESTIC_ACCOUNT.finditer(line):
                compact = "PL" + match.group().replace(" ", "")
                mentions.append(_Mention(match.group(), index + 1, (compact,), iban_error(compact)))
    return _resolve(mentions)


# --- amounts --------------------------------------------------------------

def _mention_from_match(match: re.Match[str], line: int, rate: str | None) -> _AmountMention | None:
    """Build an amount mention, or None when the number does not look like money."""
    raw = match.group("num")
    token = match.group("pre") or match.group("post") or match.groupdict().get("paren")
    if not looks_like_money(raw, token is not None):
        return None
    code = currency_code(token) if token else None
    try:
        reading = parse_amount(raw)
    except AmountError as error:
        return _AmountMention(raw, line, None, str(error), code, rate)
    return _AmountMention(raw, line, reading, None, code, rate)


def _totals_rows(lines: Sequence[str]) -> dict[int, list[re.Match[str]]]:
    """Rows such as "Razem  2 500,00  500,00  3 000,00": net, VAT and gross cells."""
    rows = {}
    for index, line in enumerate(lines):
        start = labels.TOTALS_ROW.match(line)
        if start is None:
            continue
        cells = [
            cell
            for cell in labels.AMOUNT_TOKEN.finditer(line, start.end())
            if looks_like_money(cell.group("num"), bool(cell.group("pre") or cell.group("post")))
        ]
        if len(cells) == 3:
            rows[index] = cells
    return rows


def _scan_amounts(lines: Sequence[str], label_set: Sequence[re.Pattern[str]], skip: set[int]) -> list[_AmountMention]:
    mentions = []
    for hit in find_after_labels(lines, label_set, labels.AMOUNT_VALUE):
        if hit.line in skip:
            continue
        rate = hit.label.groupdict().get("rate")
        mention = _mention_from_match(hit.value, hit.line + 1, rate)
        if mention is not None:
            mentions.append(mention)
    return mentions


def _collect_amounts(lines: Sequence[str]) -> dict[str, list[_AmountMention]]:
    rows = _totals_rows(lines)
    row_lines = set(rows)
    row_mentions = [
        [_mention_from_match(cell, index + 1, None) for cell in cells] for index, cells in rows.items()
    ]
    vat = _scan_amounts(lines, labels.VAT_LABELS, row_lines)
    collected = {
        "net": _scan_amounts(lines, labels.NET_LABELS, row_lines) + [r[0] for r in row_mentions],
        "gross": _scan_amounts(lines, labels.GROSS_LABELS, row_lines) + [r[2] for r in row_mentions],
        "vat_total": [m for m in vat if m.rate is None] + [r[1] for r in row_mentions],
        "vat_rates": [m for m in vat if m.rate is not None],
    }
    # Other amounts in the document reveal its notation, which settles tokens
    # such as "1,234" that are ambiguous on their own.
    every = [m for group in collected.values() for m in group]
    style = infer_style(m.reading for m in every if m.reading)
    if style is not None:
        collected = {
            name: [_with_style(m, style) for m in group] for name, group in collected.items()
        }
    collected["all"] = [m for group in collected.values() for m in group]
    return collected


def _with_style(mention: _AmountMention, style: str) -> _AmountMention:
    if mention.reading is not None and mention.reading.is_ambiguous:
        return replace(mention, reading=parse_amount(mention.raw, style))
    return mention


def _amount_mention(mention: _AmountMention) -> _Mention:
    if mention.reading is None:
        return _Mention(mention.raw, mention.line, (), mention.error)
    values = {format_amount(value) for value in mention.reading.candidates}
    return _Mention(mention.raw, mention.line, tuple(values))


def _amount_field(mentions: Sequence[_AmountMention]) -> FieldResult:
    return _resolve([_amount_mention(m) for m in mentions], sort_key=Decimal)


def _vat_field(total: Sequence[_AmountMention], by_rate: Sequence[_AmountMention]) -> FieldResult:
    """Prefer a labelled VAT total; otherwise add up the per-rate lines."""
    if total:
        return _amount_field(total)
    # The same rate and amount mentioned twice (summary and footer) is one line.
    unique = {}
    for mention in by_rate:
        if mention.reading is not None and not mention.reading.is_ambiguous:
            unique.setdefault((mention.rate, mention.reading.candidates), mention)
    all_clean = all(m.reading is not None and not m.reading.is_ambiguous for m in by_rate)
    if all_clean and len(unique) >= 2:
        lines_used = list(unique.values())
        amount = sum((m.reading.candidates[0] for m in lines_used), Decimal(0))
        return FieldResult(
            FieldStatus.FOUND,
            value=format_amount(amount),
            raw=" + ".join(m.raw for m in lines_used),
            line=lines_used[0].line,
            note=f"sum of {len(lines_used)} VAT-rate lines",
        )
    return _amount_field(by_rate)


def _resolve_by_arithmetic(
    net: FieldResult, vat: FieldResult, gross: FieldResult, tolerance: Decimal
) -> tuple[FieldResult, FieldResult, FieldResult]:
    """Settle ambiguous amounts when exactly one combination satisfies net + VAT = gross.

    An accidental fit is very unlikely, so a unique one is good evidence. The
    result says how it was chosen, so the caller can still treat it with care.
    """
    parts = (net, vat, gross)
    if all(part.status is FieldStatus.FOUND for part in parts):
        return parts
    options = []
    for part in parts:
        if part.status is FieldStatus.FOUND:
            options.append((part.value,))
        elif part.status is FieldStatus.AMBIGUOUS:
            options.append(part.candidates)
        else:
            return parts
    fits = [
        combo
        for combo in product(*options)
        if abs(Decimal(combo[0]) + Decimal(combo[1]) - Decimal(combo[2])) <= tolerance
    ]
    if len(fits) != 1:
        return parts
    note = "chosen because net + VAT = gross holds only for this combination"

    def settle(part: FieldResult, value: str) -> FieldResult:
        if part.status is FieldStatus.AMBIGUOUS:
            return FieldResult(FieldStatus.FOUND, value=value, line=part.line, note=note)
        return part

    settled_net, settled_vat, settled_gross = (settle(p, v) for p, v in zip(parts, fits[0]))
    return settled_net, settled_vat, settled_gross


# --- currency -------------------------------------------------------------

def _currency_field(lines: Sequence[str], amounts: Sequence[_AmountMention]) -> FieldResult:
    """Currency by decreasing trust: an explicit label, a code or symbol printed
    next to an extracted amount, any currency mentioned anywhere."""
    labelled = []
    for hit in find_after_labels(lines, labels.CURRENCY_LABELS, labels.CURRENCY_VALUE):
        code = hit.value.group("v")
        error = None if code in CURRENCY_CODES else f"unknown currency code {code}"
        labelled.append(_Mention(code, hit.line + 1, (code,), error))
    if labelled:
        return _resolve(labelled)
    beside_amounts = [_Mention(m.currency, m.line, (m.currency,)) for m in amounts if m.currency]
    if beside_amounts:
        return _resolve(beside_amounts)
    anywhere = [
        _Mention(match.group(), index + 1, (currency_code(match.group()),))
        for index, line in enumerate(lines)
        for match in labels.CURRENCY_TOKEN.finditer(line)
    ]
    return _resolve(anywhere)


# --- cross-checks ---------------------------------------------------------

def _check_totals(net: FieldResult, vat: FieldResult, gross: FieldResult, tolerance: Decimal) -> CrossCheck:
    name = "net_plus_vat_equals_gross"
    unavailable = [
        label
        for label, part in (("net", net), ("VAT", vat), ("gross", gross))
        if part.status is not FieldStatus.FOUND
    ]
    if unavailable:
        return CrossCheck(
            name,
            CheckStatus.SKIPPED,
            "needs one reliable value each for net, VAT and gross; unavailable: " + ", ".join(unavailable),
        )
    net_value, vat_value, gross_value = Decimal(net.value), Decimal(vat.value), Decimal(gross.value)
    expected = net_value + vat_value
    difference = abs(expected - gross_value)
    equation = f"net {format_amount(net_value)} + VAT {format_amount(vat_value)} = {format_amount(expected)}"
    if difference <= tolerance:
        return CrossCheck(
            name, CheckStatus.OK, f"{equation}, matches gross {format_amount(gross_value)}"
        )
    return CrossCheck(
        name,
        CheckStatus.INCONSISTENT,
        f"{equation}, but gross is {format_amount(gross_value)} "
        f"(difference {format_amount(difference)}, tolerance {format_amount(tolerance)})",
    )


def _check_dates(issue: FieldResult, due: FieldResult) -> CrossCheck:
    name = "due_date_not_before_issue_date"
    if issue.status is not FieldStatus.FOUND or due.status is not FieldStatus.FOUND:
        return CrossCheck(name, CheckStatus.SKIPPED, "needs one reliable issue date and due date")
    if date.fromisoformat(due.value) < date.fromisoformat(issue.value):
        return CrossCheck(
            name, CheckStatus.INCONSISTENT, f"due date {due.value} is before issue date {issue.value}"
        )
    return CrossCheck(name, CheckStatus.OK, f"due date {due.value} is not before issue date {issue.value}")
