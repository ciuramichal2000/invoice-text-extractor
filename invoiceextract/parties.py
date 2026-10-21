"""Locate the seller and buyer sections so every NIP is attributed to the right party.

A bare "NIP: ..." says nothing about whose number it is; the section header
above it does. PDF-to-text often prints the seller and buyer side by side, so
a header line can hold two headers and the lines below hold two columns.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

SELLER = "seller"
BUYER = "buyer"

# A section normally ends at a blank line; the cap stops a missing blank line
# from swallowing the whole item table.
MAX_BLOCK_LINES = 12

_SELLER_WORDS = r"Sprzedawca|Wystawca|Dostawca|Seller|Supplier|Vendor|From"
_BUYER_WORDS = (
    r"Nabywca|Kupuj[ąa]cy|Buyer|Customer|Client|Bill(?:ed)?\s+to|Invoice\s+to|Sold\s+to"
)
# Sections that are neither party; they only mark where the neighbouring one ends.
_OTHER_WORDS = r"Odbiorca|Recipient|Ship(?:ped)?\s+to|Deliver(?:y)?\s+to"

# A header sits at the start of a line or after a column gap (two spaces) and
# is followed by a colon, the end of the line, or another column gap.
_HEADER = re.compile(
    rf"(?:^\s*|(?<=\s\s))"
    rf"(?:(?P<seller>{_SELLER_WORDS})|(?P<buyer>{_BUYER_WORDS})|(?P<other>{_OTHER_WORDS}))"
    rf"\b(?=\s*:|\s*$|\s\s+\S)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PartyBlock:
    """The text belonging to one party header, as (document line index, text) pairs."""

    role: str
    lines: tuple[tuple[int, str], ...]


def find_party_blocks(lines: Sequence[str]) -> list[PartyBlock]:
    blocks: list[PartyBlock] = []
    for index, line in enumerate(lines):
        headers = list(_HEADER.finditer(line))
        multi_column = len(headers) > 1
        for position, header in enumerate(headers):
            role = header.lastgroup
            if role == "other":
                continue
            # In a multi-column layout each party owns the columns from its own
            # header to the next one; otherwise it owns whole lines.
            start = header.start(role) if multi_column else 0
            end = None
            if position + 1 < len(headers):
                following = headers[position + 1]
                end = following.start(following.lastgroup)
            body = [(index, line[header.end():end])]
            for next_index in range(index + 1, len(lines)):
                text = lines[next_index]
                if not text.strip() or _HEADER.search(text) or len(body) >= MAX_BLOCK_LINES:
                    break
                body.append((next_index, text[start:end]))
            blocks.append(PartyBlock(role, tuple(body)))
    return blocks
