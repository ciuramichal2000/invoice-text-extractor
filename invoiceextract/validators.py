"""Checksum validation for Polish NIP numbers and IBANs.

Both validators return an explanation instead of a bare boolean so the
extractor can tell the user *why* a number was rejected.
"""

import re

_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)

# Fixed IBAN lengths for the countries invoices most often mention. Other
# countries are checked for shape and mod 97 only.
IBAN_LENGTHS = {
    "AT": 20, "BE": 16, "CH": 21, "CZ": 24, "DE": 22, "DK": 18, "ES": 24, "FR": 27,
    "GB": 22, "IE": 22, "IT": 27, "LT": 20, "NL": 18, "PL": 28, "SE": 24, "SK": 24,
}


def nip_error(nip: str) -> str | None:
    """Explain why ``nip`` (digits only) is not a valid Polish NIP, or return None."""
    if not re.fullmatch(r"[0-9]{10}", nip):
        return f"NIP must be exactly 10 digits, found {len(nip)} characters"
    if nip.startswith("000"):
        # 0000000000 satisfies the checksum trivially, but no tax office has code 000.
        return "NIP tax-office prefix 000 does not exist"
    remainder = sum(int(digit) * weight for digit, weight in zip(nip, _NIP_WEIGHTS)) % 11
    if remainder == 10:
        # A remainder of 10 has no single-digit check digit; taking "% 10" here
        # would wrongly accept numbers whose last digit is 0.
        return "NIP checksum remainder is 10, which no check digit can match"
    if remainder != int(nip[9]):
        return f"NIP checksum mismatch: expected check digit {remainder}, found {nip[9]}"
    return None


def is_valid_nip(nip: str) -> bool:
    return nip_error(nip) is None


def normalize_iban(text: str) -> str:
    """Drop all whitespace and upper-case, the canonical machine form of an IBAN."""
    return "".join(text.split()).upper()


def iban_error(iban: str) -> str | None:
    """Explain why ``iban`` is invalid (shape, country length or mod 97), or return None."""
    compact = normalize_iban(iban)
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", compact):
        return "not in IBAN format (2 letters, 2 check digits, 11-30 letters or digits)"
    expected_length = IBAN_LENGTHS.get(compact[:2])
    if expected_length is not None and len(compact) != expected_length:
        return f"{compact[:2]} IBANs have {expected_length} characters, found {len(compact)}"
    # ISO 13616: move the first four characters to the end, turn letters into
    # 10..35, and the resulting integer must leave remainder 1 modulo 97.
    rearranged = compact[4:] + compact[:4]
    number = int("".join(str(int(char, 36)) for char in rearranged))
    if number % 97 != 1:
        return "IBAN checksum (mod 97) failed"
    return None


def is_valid_iban(iban: str) -> bool:
    return iban_error(iban) is None
