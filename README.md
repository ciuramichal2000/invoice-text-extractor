# invoice-text-extractor

A Python library and command-line tool that reads the plain text of an invoice (for example the output of a PDF-to-text tool) and returns structured, validated fields as JSON. It handles Polish and English invoices: number, issue and due date, seller and buyer NIP, net / VAT / gross amounts, currency and IBAN. Each field carries a status (`found`, `ambiguous`, `missing` or `failed_validation`) instead of a bare value, so a caller can tell "not on the invoice" from "on the invoice but wrong". It uses only the standard library; pytest is needed only to run the tests.

## Interesting engineering

- **Ambiguity is reported, not guessed.** `1,234` (1.234 in Polish, 1234 in English) and `03/04/2024` (3 April or 4 March) return both readings. They are settled only by evidence in the document itself: another amount that proves the notation, an unambiguous slash date such as `17/04/2024`, the same date written in ISO form elsewhere, or a net + VAT = gross equation that holds for exactly one combination. Otherwise the field is `ambiguous` and lists its candidates.
- **One resolver for every field.** All mentions of a field are collected first, then a single function turns them into found / ambiguous / missing / failed validation, so a repeated value counts once and two conflicting values are never silently picked between.
- **Validators explain themselves.** The NIP check (weights 6 5 7 2 3 4 5 6 7, mod 11) rejects a remainder of 10 rather than letting `% 10` accept it, and the IBAN check (ISO 13616 mod 97) first compares the length with a table of 16 countries. Both return the reason, which ends up in the field's `note`.
- **Seller and buyer are told apart by section.** A NIP belongs to the party whose header precedes it, including layouts where the two parties are printed side by side. A NIP outside any seller or buyer section is left `missing` rather than assigned by guess.
- **Money is `Decimal` throughout.** The totals cross-check uses a configurable tolerance (default 0.01), and a second cross-check flags a due date earlier than the issue date.

## Usage

```python
from invoiceextract import extract_invoice

result = extract_invoice(text)                 # text: str, one invoice
result.fields["gross_amount"].value            # "1250.00" for the example below
result.fields["seller_nip"].status             # FieldStatus.FOUND if the NIP checksum is valid
result.inconsistencies                         # tuple of cross-checks that failed
print(result.to_json())                        # ASCII-only JSON
```

Command line (reads a UTF-8 file, or standard input when the path is omitted or `-`):

```
python -m invoiceextract invoice.txt
python -m invoiceextract invoice.txt --tolerance 0.05
```

For an invoice with `Subtotal: 1,000.00 EUR`, `VAT (23%): 230.00 EUR` and `Total due: 1,250.00 EUR` the JSON contains, among the other fields:

```json
{
  "gross_amount": {
    "status": "found",
    "value": "1250.00",
    "raw": "1,250.00",
    "line": 15,
    "candidates": [],
    "note": null
  }
}
```

and, in `checks`:

```json
{
  "name": "net_plus_vat_equals_gross",
  "status": "inconsistent",
  "message": "net 1000.00 + VAT 230.00 = 1230.00, but gross is 1250.00 (difference 20.00, tolerance 0.01)"
}
```

`value` is normalised: ISO dates, plain decimal strings, digits-only NIP, compact IBAN. For `failed_validation` it holds what was found and `note` says why it failed; for `ambiguous` it is `null` and `candidates` lists the readings. Empty input raises `InvoiceTextError`; the command line prints the message and exits with code 2.

## Running the tests

```
python -m pytest -q
```

The tests are deterministic (no network, clock or randomness) and include five invented sample invoices, three Polish and two English, one of which has deliberately planted defects (wrong NIP check digit, wrong IBAN digit, totals that do not add up, an ambiguous slash date).

## Limitations

- Works on text, not on page images: there is no OCR, and the result depends on how well the PDF-to-text step kept labels next to their values.
- Labels are matched from fixed Polish and English vocabularies; an invoice in another language, or with unusual wording, will come back with `missing` fields.
- Month names in dates are English only (`14 May 2024`); `14 maja 2024` is not recognised.
- Only Polish NIP is validated, with an optional `PL` prefix. Other countries' tax numbers are not.
- IBAN length is checked for 16 countries; other countries get the shape and mod 97 check only. A bare `$` is not mapped to a currency, because it is ambiguous.
- Only invoice-level totals are read; line items are not parsed.
- The sample invoices are invented, and the label patterns have not been tuned on real-world invoices.

Built with AI coding assistance.
