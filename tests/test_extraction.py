import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from decimal import Decimal

import pytest

from invoiceextract import (
    CheckStatus,
    FieldStatus,
    InvoiceTextError,
    extract_invoice,
)

FOUND = FieldStatus.FOUND
AMBIGUOUS = FieldStatus.AMBIGUOUS
MISSING = FieldStatus.MISSING
FAILED = FieldStatus.FAILED_VALIDATION

# Numbers used in the invented invoices (checksums verified by hand in test_validators.py):
#   NIP 526-025-09-95 = 5260250995, NIP 525-224-84-81 = 5252248481, NIP 123-456-32-18 = 1234563218
#   IBAN PL61 1090 1014 0000 0712 1981 2874 is the registry example for Poland.


def _columns(left: str, right: str, width: int = 36) -> str:
    return left.ljust(width) + right


# --- invented sample invoices ---------------------------------------------

# 1. Polish, sections stacked vertically, Polish number notation, one VAT rate.
POLISH_STACKED = """\
FAKTURA VAT nr FV/2024/05/0142

Data wystawienia: 14.05.2024
Data sprzedaży: 14.05.2024
Termin płatności: 28.05.2024

Sprzedawca:
Alfa Systemy Sp. z o.o.
ul. Długa 5, 00-001 Warszawa
NIP: 526-025-09-95

Nabywca:
Beta Handel S.A.
ul. Krótka 12, 30-002 Kraków
NIP: 525-224-84-81

Lp. Nazwa towaru/usługi          Ilość  Cena netto  Wartość netto  VAT   Wartość brutto
1   Licencja oprogramowania      1      1 000,00    1 000,00       23%   1 230,00

Razem netto: 1 000,00 PLN
Kwota VAT (23%): 230,00 PLN
Razem brutto: 1 230,00 PLN
Do zapłaty: 1 230,00 PLN

Sposób płatności: przelew
Nr konta: PL61 1090 1014 0000 0712 1981 2874
"""

# 2. Polish, seller and buyer printed side by side, ISO dates, a "Razem" row with
#    net/VAT/gross cells for two VAT rates, domestic account number without "PL".
#    Net 2000 + 500 = 2500; VAT 23% of 2000 = 460 and 8% of 500 = 40, total 500; gross 3000.
POLISH_TWO_COLUMNS = "\n".join(
    [
        "Faktura nr 17/2024/FV" + " " * 30 + "Oryginał",
        "Wystawiono dnia: 2024-06-03",
        "Termin zapłaty:  2024-06-17",
        "",
        _columns("Sprzedawca", "Nabywca"),
        _columns("Gamma Logistyka Sp. z o.o.", "Delta Transport sp.j."),
        _columns("ul. Polna 8", "ul. Leśna 3"),
        _columns("00-950 Warszawa", "60-001 Poznań"),
        _columns("NIP 123-456-32-18", "NIP: 5252248481"),
        "",
        "Lp.  Nazwa                  Stawka VAT   Netto       VAT        Brutto",
        "1    Transport krajowy      23%          2 000,00    460,00     2 460,00",
        "2    Opłata paliwowa        8%             500,00     40,00       540,00",
        "",
        "Razem                                    2 500,00    500,00     3 000,00",
        "",
        "Do zapłaty: 3 000,00 zł",
        "Konto bankowe: 61 1090 1014 0000 0712 1981 2874",
    ]
)

# 3. English, English number notation, EU VAT prefix on the buyer's number.
ENGLISH_STACKED = """\
INVOICE

Invoice No: INV-2024-0457
Invoice Date: 14 May 2024
Due Date: 13 June 2024

From:
Northbridge Software Sp. z o.o.
12 Baker Street, Warsaw
Tax ID (NIP): 526-025-09-95

Bill To:
Orchard Retail Ltd
44 Mill Road, Leeds
VAT ID: PL 525-224-84-81

Description                  Qty    Unit price      Amount
Consulting services           10         100.00    1,000.00

Subtotal:               1,000.00 EUR
VAT (23%):                230.00 EUR
Total due:              1,230.00 EUR

Bank: Example Bank
IBAN: PL61 1090 1014 0000 0712 1981 2874
Payment reference: INV-2024-0457
"""

# 4. English text with Polish number notation and four planted defects:
#    seller NIP check digit wrong (should be 5), IBAN last digit changed,
#    gross 1850.00 although 1500.00 + 345.00 = 1845.00, and an ambiguous slash date
#    (03/04/2024) that the unambiguous 17/04/2024 elsewhere resolves to day-first.
FLAWED = """\
INVOICE
Invoice # 2024/117
Date of issue: 03/04/2024
Payment due: 17/04/2024

Seller:
Harbor Print Ltd
NIP: 526-025-09-96

Buyer:
Willow Studio
NIP: 525-224-84-81

Net amount:  1 500,00 EUR
VAT 23%:       345,00 EUR
Gross amount: 1 850,00 EUR

IBAN: PL61 1090 1014 0000 0712 1981 2875
"""

# 5. Polish, non-breaking spaces inside amounts, values on the line after their label,
#    a leap-day issue date, seller and buyer given inline, VAT printed per rate only.
#    Net 3000 + 1000; VAT 690 (23%) + 80 (8%) = 770; gross 4770.
POLISH_BREAKDOWN = (
    "Faktura VAT nr 0042/2024\n"
    "Data wystawienia\n"
    "2024-02-29\n"
    "Termin płatności: 30.03.2024\n"
    "\n"
    "Sprzedawca: Omega Serwis Sp. z o.o., NIP: 1234563218\n"
    "Nabywca: Sigma Meble S.A., NIP 525-224-84-81\n"
    "\n"
    "Waluta: PLN\n"
    "Razem netto:        4\u00a0000,00\n"
    "VAT 23%:              690,00\n"
    "VAT 8%:                80,00\n"
    "Razem brutto:       4\u00a0770,00\n"
)


def _totals_text(net: str, vat: str, gross: str) -> str:
    return f"Net amount: {net} EUR\nVAT: {vat} EUR\nTotal due: {gross} EUR\n"


def _check(result, name):
    return next(c for c in result.checks if c.name == name)


# --- the five sample invoices ---------------------------------------------

def test_polish_stacked_invoice_yields_every_field():
    result = extract_invoice(POLISH_STACKED)
    expected = {
        "invoice_number": "FV/2024/05/0142",
        "issue_date": "2024-05-14",
        "due_date": "2024-05-28",
        "seller_nip": "5260250995",
        "buyer_nip": "5252248481",
        "net_amount": "1000.00",
        "vat_amount": "230.00",
        "gross_amount": "1230.00",
        "currency": "PLN",
        "iban": "PL61109010140000071219812874",
    }
    assert {name: field.value for name, field in result.fields.items()} == expected
    assert all(field.status is FOUND for field in result.fields.values())
    assert result.fields["invoice_number"].line == 1
    assert result.fields["due_date"].line == 5
    assert not result.inconsistencies
    assert _check(result, "net_plus_vat_equals_gross").status is CheckStatus.OK


def test_polish_two_column_layout_keeps_seller_and_buyer_apart():
    result = extract_invoice(POLISH_TWO_COLUMNS)
    assert result.fields["seller_nip"].value == "1234563218"
    assert result.fields["buyer_nip"].value == "5252248481"
    assert result.fields["seller_nip"].status is FOUND
    assert result.fields["buyer_nip"].status is FOUND


def test_polish_two_column_invoice_reads_totals_row_and_domestic_account():
    result = extract_invoice(POLISH_TWO_COLUMNS)
    assert result.fields["invoice_number"].value == "17/2024/FV"
    assert result.fields["issue_date"].value == "2024-06-03"
    assert result.fields["due_date"].value == "2024-06-17"
    # The three cells of the "Razem" row are net, VAT and gross, not three gross candidates.
    assert result.fields["net_amount"].value == "2500.00"
    assert result.fields["vat_amount"].value == "500.00"
    assert result.fields["gross_amount"].value == "3000.00"
    assert result.fields["currency"].value == "PLN"  # from "zł"
    assert result.fields["iban"].value == "PL61109010140000071219812874"
    assert not result.inconsistencies


def test_english_invoice_with_english_notation_and_eu_vat_prefix():
    result = extract_invoice(ENGLISH_STACKED)
    assert result.fields["invoice_number"].value == "INV-2024-0457"
    assert result.fields["issue_date"].value == "2024-05-14"
    assert result.fields["due_date"].value == "2024-06-13"
    assert result.fields["seller_nip"].value == "5260250995"
    assert result.fields["buyer_nip"].value == "5252248481"
    assert result.fields["net_amount"].value == "1000.00"
    assert result.fields["vat_amount"].value == "230.00"
    assert result.fields["gross_amount"].value == "1230.00"
    assert result.fields["currency"].value == "EUR"
    assert result.fields["iban"].value == "PL61109010140000071219812874"
    assert all(field.status is FOUND for field in result.fields.values())


def test_flawed_invoice_flags_each_planted_defect_and_still_reads_the_rest():
    result = extract_invoice(FLAWED)

    seller = result.fields["seller_nip"]
    assert seller.status is FAILED
    assert seller.value == "5260250996"
    assert "expected check digit 5" in seller.note

    iban = result.fields["iban"]
    assert iban.status is FAILED
    assert iban.value == "PL61109010140000071219812875"
    assert "mod 97" in iban.note

    # 1500,00 + 345,00 = 1845,00, the invoice says 1850,00.
    check = _check(result, "net_plus_vat_equals_gross")
    assert check.status is CheckStatus.INCONSISTENT
    assert "1845.00" in check.message and "1850.00" in check.message
    assert "difference 5.00" in check.message
    assert result.inconsistencies == (check,)

    # Everything else is unaffected by the defects.
    assert result.fields["buyer_nip"].value == "5252248481"
    assert result.fields["invoice_number"].value == "2024/117"
    assert result.fields["net_amount"].value == "1500.00"
    assert result.fields["vat_amount"].value == "345.00"
    assert result.fields["gross_amount"].value == "1850.00"
    assert result.fields["currency"].value == "EUR"


def test_slash_date_is_settled_by_an_unambiguous_slash_date_elsewhere():
    result = extract_invoice(FLAWED)
    # 17/04/2024 can only be day-first, so 03/04/2024 is 3 April, not 4 March.
    assert result.fields["issue_date"].value == "2024-04-03"
    assert result.fields["due_date"].value == "2024-04-17"
    assert _check(result, "due_date_not_before_issue_date").status is CheckStatus.OK


def test_polish_breakdown_handles_nbsp_next_line_values_and_vat_per_rate():
    result = extract_invoice(POLISH_BREAKDOWN)
    assert result.fields["invoice_number"].value == "0042/2024"
    assert result.fields["issue_date"].value == "2024-02-29"
    assert result.fields["issue_date"].line == 3  # the value sits below its label
    assert result.fields["due_date"].value == "2024-03-30"
    assert result.fields["seller_nip"].value == "1234563218"
    assert result.fields["buyer_nip"].value == "5252248481"
    assert result.fields["net_amount"].value == "4000.00"
    assert result.fields["gross_amount"].value == "4770.00"
    assert result.fields["currency"].value == "PLN"
    assert result.fields["iban"].status is MISSING

    vat = result.fields["vat_amount"]
    assert vat.status is FOUND
    assert vat.value == "770.00"  # 690.00 + 80.00
    assert vat.note == "sum of 2 VAT-rate lines"
    assert _check(result, "net_plus_vat_equals_gross").status is CheckStatus.OK


# --- bad input -------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   \n\t\n  ", "\u00a0\u200b"])
def test_text_without_any_content_raises(text):
    with pytest.raises(InvoiceTextError, match="empty"):
        extract_invoice(text)


@pytest.mark.parametrize("value", [None, b"Faktura nr 1", 42, ["Faktura nr 1"]])
def test_non_text_input_raises_type_error(value):
    with pytest.raises(TypeError, match="must be str"):
        extract_invoice(value)


@pytest.mark.parametrize(
    ("tolerance", "error"),
    [("-0.01", ValueError), ("abc", ValueError), ("NaN", ValueError), (0.01, TypeError), (True, TypeError), (None, ValueError)],
)
def test_unusable_tolerance_is_rejected(tolerance, error):
    with pytest.raises(error):
        extract_invoice("Faktura nr 1", tolerance=tolerance)


def test_text_with_no_recognisable_content_gives_missing_fields_and_skipped_checks():
    result = extract_invoice("Hello there.\nThis is not an invoice.")
    assert all(field.status is MISSING for field in result.fields.values())
    assert all(field.value is None for field in result.fields.values())
    assert [c.status for c in result.checks] == [CheckStatus.SKIPPED, CheckStatus.SKIPPED]
    assert "net, VAT, gross" in _check(result, "net_plus_vat_equals_gross").message


# --- invoice number --------------------------------------------------------

def test_two_different_invoice_numbers_are_ambiguous_and_listed():
    result = extract_invoice("Invoice No: INV-1\nInvoice number: INV-2\n")
    field = result.fields["invoice_number"]
    assert field.status is AMBIGUOUS
    assert field.value is None
    assert field.candidates == ("INV-1", "INV-2")
    assert field.note == "conflicting values on lines 1, 2"


def test_same_invoice_number_mentioned_twice_is_found_once():
    result = extract_invoice("Faktura nr FV/1/2024\nNr faktury: FV/1/2024\n")
    assert result.fields["invoice_number"].status is FOUND
    assert result.fields["invoice_number"].value == "FV/1/2024"


def test_invoice_number_may_have_a_short_alphabetic_prefix_and_loses_trailing_punctuation():
    result = extract_invoice("Faktura VAT nr FV 12/2024.\n")
    assert result.fields["invoice_number"].value == "FV 12/2024"


def test_invoice_number_label_without_a_digit_value_is_missing():
    assert extract_invoice("Invoice No: pending\n").fields["invoice_number"].status is MISSING


def test_corrective_invoice_number_is_not_confused_with_the_corrected_one():
    text = "Faktura korygująca nr FK/3/2024 do faktury nr FV/1/2024\n"
    assert extract_invoice(text).fields["invoice_number"].value == "FK/3/2024"


# --- dates -----------------------------------------------------------------

def test_impossible_date_fails_validation_and_keeps_the_raw_text():
    field = extract_invoice("Data wystawienia: 31.02.2024\n").fields["issue_date"]
    assert field.status is FAILED
    assert field.value == "31.02.2024"
    assert "not a valid calendar date" in field.note


def test_slash_date_without_context_is_ambiguous_with_both_readings():
    field = extract_invoice("Issue date: 03/04/2024\n").fields["issue_date"]
    assert field.status is AMBIGUOUS
    assert field.value is None
    assert field.candidates == ("2024-03-04", "2024-04-03")


def test_slash_date_with_day_above_twelve_is_day_first():
    field = extract_invoice("Issue date: 14/05/2024\n").fields["issue_date"]
    assert (field.status, field.value) == (FOUND, "2024-05-14")


def test_us_slash_order_is_inferred_from_a_month_above_twelve_elsewhere():
    result = extract_invoice("Issue date: 03/04/2024\nDue date: 04/17/2024\n")
    assert result.fields["issue_date"].value == "2024-03-04"
    assert result.fields["due_date"].value == "2024-04-17"


def test_ambiguous_slash_date_mentioned_again_in_iso_form_is_settled():
    result = extract_invoice("Issue date: 03/04/2024\nInvoice date: 2024-04-03\n")
    assert result.fields["issue_date"].status is FOUND
    assert result.fields["issue_date"].value == "2024-04-03"


def test_conflicting_issue_dates_are_ambiguous():
    result = extract_invoice("Data wystawienia: 14.05.2024\nData faktury: 15.05.2024\n")
    field = result.fields["issue_date"]
    assert field.status is AMBIGUOUS
    assert field.candidates == ("2024-05-14", "2024-05-15")


def test_bilingual_labels_find_the_date_once():
    result = extract_invoice("Data wystawienia / Issue date: 14.05.2024\n")
    assert result.fields["issue_date"].status is FOUND
    assert result.fields["issue_date"].value == "2024-05-14"


def test_due_date_before_issue_date_is_reported_as_inconsistent():
    result = extract_invoice("Issue date: 2024-06-10\nDue date: 2024-06-01\n")
    check = _check(result, "due_date_not_before_issue_date")
    assert check.status is CheckStatus.INCONSISTENT
    assert "2024-06-01" in check.message and "2024-06-10" in check.message


def test_due_on_the_issue_day_is_consistent():
    result = extract_invoice("Issue date: 2024-06-10\nDue date: 2024-06-10\n")
    assert _check(result, "due_date_not_before_issue_date").status is CheckStatus.OK


# --- NIP -------------------------------------------------------------------

def test_nip_with_nine_digits_fails_validation():
    result = extract_invoice("Sprzedawca:\nAlfa Sp. z o.o.\nNIP: 526-025-09-9\n")
    field = result.fields["seller_nip"]
    assert field.status is FAILED
    assert field.value == "526025099"
    assert "exactly 10 digits" in field.note


def test_nip_without_a_seller_or_buyer_header_is_not_guessed():
    result = extract_invoice("Jakaś firma\nNIP: 526-025-09-95\n")
    assert result.fields["seller_nip"].status is MISSING
    assert result.fields["buyer_nip"].status is MISSING


def test_nip_on_the_line_after_its_label_is_found():
    result = extract_invoice("Nabywca:\nBeta SA\nNIP:\n525-224-84-81\n")
    assert result.fields["buyer_nip"].value == "5252248481"
    assert result.fields["buyer_nip"].line == 4


def test_two_different_nips_in_one_party_block_are_ambiguous():
    result = extract_invoice("Seller:\nNIP: 5260250995\nNIP: 5252248481\n")
    field = result.fields["seller_nip"]
    assert field.status is AMBIGUOUS
    assert field.candidates == ("5252248481", "5260250995")


def test_nip_written_with_and_without_pl_prefix_counts_as_one_number():
    text = "Buyer:\nNIP: 5252248481\nVAT ID: PL5252248481\n"
    field = extract_invoice(text).fields["buyer_nip"]
    assert (field.status, field.value) == (FOUND, "5252248481")


def test_blank_line_ends_a_party_block():
    text = "Sprzedawca:\nAlfa\n\nNIP: 5260250995\n"
    assert extract_invoice(text).fields["seller_nip"].status is MISSING


def test_buyer_listed_before_seller_is_still_attributed_correctly():
    text = "Nabywca:\nNIP: 5252248481\n\nSprzedawca:\nNIP: 5260250995\n"
    result = extract_invoice(text)
    assert result.fields["buyer_nip"].value == "5252248481"
    assert result.fields["seller_nip"].value == "5260250995"


def test_recipient_section_does_not_leak_its_nip_into_the_buyer():
    text = "Nabywca:\nBeta SA\nNIP: 5252248481\nOdbiorca:\nGamma SA\nNIP: 5260250995\n"
    field = extract_invoice(text).fields["buyer_nip"]
    assert (field.status, field.value) == (FOUND, "5252248481")


# --- amounts ---------------------------------------------------------------

def test_ambiguous_amount_alone_lists_both_readings_in_numeric_order():
    result = extract_invoice("Total due: 1,234 EUR\n")
    field = result.fields["gross_amount"]
    assert field.status is AMBIGUOUS
    assert field.candidates == ("1.234", "1234.00")
    assert _check(result, "net_plus_vat_equals_gross").status is CheckStatus.SKIPPED


def test_negative_ambiguous_amount_lists_the_more_negative_reading_first():
    field = extract_invoice("Total due: -1,234 EUR\n").fields["gross_amount"]
    assert field.status is AMBIGUOUS
    assert field.candidates == ("-1234.00", "-1.234")  # a text sort would reverse these


def test_ambiguous_amounts_stay_ambiguous_when_two_readings_both_balance():
    # 1.000 + 0 = 1.000 and 1000 + 0 = 1000 both hold, so the arithmetic cannot choose.
    result = extract_invoice("Net amount: 1,000 EUR\nVAT: 0 EUR\nTotal due: 1,000 EUR\n")
    assert result.fields["net_amount"].status is AMBIGUOUS
    assert result.fields["gross_amount"].status is AMBIGUOUS
    assert result.fields["vat_amount"].value == "0.00"
    assert _check(result, "net_plus_vat_equals_gross").status is CheckStatus.SKIPPED


def test_notation_of_another_amount_settles_an_ambiguous_one():
    text = "Net amount: 2,000 EUR\nVAT 23%: 460.00 EUR\nGross amount: 2,460 EUR\n"
    result = extract_invoice(text)
    # "460.00" proves dot-decimal notation, so "2,000" must be two thousand.
    assert result.fields["net_amount"].value == "2000.00"
    assert result.fields["gross_amount"].value == "2460.00"
    assert result.fields["net_amount"].status is FOUND
    assert result.fields["net_amount"].note is None


def test_arithmetic_settles_ambiguous_amounts_when_no_notation_is_revealed():
    text = "Net amount: 2,000 EUR\nVAT 23%: 460 EUR\nGross amount: 2,460 EUR\n"
    result = extract_invoice(text)
    # Only 2000 + 460 = 2460 balances; 2.000 + 460 and the other mixes do not.
    assert result.fields["net_amount"].value == "2000.00"
    assert result.fields["vat_amount"].value == "460.00"
    assert result.fields["gross_amount"].value == "2460.00"
    assert result.fields["net_amount"].status is FOUND
    assert "net + VAT = gross" in result.fields["net_amount"].note
    assert result.fields["vat_amount"].note is None


def test_conflicting_gross_values_are_resolved_by_the_balance_of_the_other_two():
    text = (
        "Razem netto: 1 000,00 PLN\n"
        "Kwota VAT (23%): 230,00 PLN\n"
        "Razem brutto: 1 230,00 PLN\n"
        "Do zapłaty: 615,00 PLN\n"  # half was already prepaid
    )
    field = extract_invoice(text).fields["gross_amount"]
    assert (field.status, field.value) == (FOUND, "1230.00")
    assert "net + VAT = gross" in field.note


def test_conflicting_gross_values_without_a_balance_stay_ambiguous_and_sort_numerically():
    field = extract_invoice("Razem brutto: 1 230,00 PLN\nDo zapłaty: 615,00 PLN\n").fields["gross_amount"]
    assert field.status is AMBIGUOUS
    assert field.candidates == ("615.00", "1230.00")  # a text sort would put 1230.00 first


@pytest.mark.parametrize(
    ("gross", "tolerance", "expected"),
    [
        ("123.00", None, CheckStatus.OK),
        ("123.01", None, CheckStatus.OK),        # exactly the default tolerance of 0.01
        ("123.02", None, CheckStatus.INCONSISTENT),
        ("122.98", None, CheckStatus.INCONSISTENT),
        ("123.02", "0.02", CheckStatus.OK),
        ("123.05", "0.02", CheckStatus.INCONSISTENT),
        ("123.01", "0", CheckStatus.INCONSISTENT),
    ],
)
def test_net_plus_vat_is_compared_with_gross_within_the_tolerance(gross, tolerance, expected):
    kwargs = {} if tolerance is None else {"tolerance": Decimal(tolerance)}
    result = extract_invoice(_totals_text("100.00", "23.00", gross), **kwargs)
    assert _check(result, "net_plus_vat_equals_gross").status is expected


def test_same_vat_rate_line_repeated_in_the_footer_is_counted_once():
    text = (
        "VAT 23%: 230,00 PLN\n"
        "VAT 8%: 40,00 PLN\n"
        "Podsumowanie\n"
        "VAT 23%: 230,00 PLN\n"
    )
    vat = extract_invoice(text).fields["vat_amount"]
    assert (vat.status, vat.value) == (FOUND, "270.00")


def test_two_rates_with_identical_vat_amounts_are_both_counted():
    vat = extract_invoice("VAT 23%: 100,00 PLN\nVAT 8%: 100,00 PLN\n").fields["vat_amount"]
    assert vat.value == "200.00"


def test_labelled_vat_total_wins_over_rate_lines():
    text = "VAT 23%: 230,00 PLN\nVAT 8%: 40,00 PLN\nRazem VAT: 270,00 PLN\n"
    vat = extract_invoice(text).fields["vat_amount"]
    assert (vat.status, vat.value, vat.note) == (FOUND, "270.00", None)


def test_percentages_and_tax_numbers_after_a_vat_label_are_not_amounts():
    result = extract_invoice("Stawka VAT: 23%\nVAT: 5260250995\n")
    assert result.fields["vat_amount"].status is MISSING


def test_malformed_amount_fails_validation():
    field = extract_invoice("Razem brutto: 1.234.56 PLN\n").fields["gross_amount"]
    assert field.status is FAILED
    assert field.value == "1.234.56"
    assert "misplaced thousands separator" in field.note


def test_negative_amounts_on_a_corrective_invoice_are_kept():
    text = "Net amount: -100,00 EUR\nVAT: -23,00 EUR\nTotal due: -123,00 EUR\n"
    result = extract_invoice(text)
    assert result.fields["net_amount"].value == "-100.00"
    assert _check(result, "net_plus_vat_equals_gross").status is CheckStatus.OK


def test_amount_label_with_currency_in_parentheses():
    result = extract_invoice("Total (EUR): 1,230.00\n")
    assert result.fields["gross_amount"].value == "1230.00"
    assert result.fields["currency"].value == "EUR"


def test_item_row_amounts_are_not_taken_for_totals():
    text = "Cena netto: 999,00 PLN\nRazem netto: 1 000,00 PLN\n"
    assert extract_invoice(text).fields["net_amount"].value == "1000.00"


# --- currency ---------------------------------------------------------------

def test_explicit_currency_label_wins_over_codes_beside_amounts():
    result = extract_invoice("Currency: EUR\nTotal due: 100,00 PLN\n")
    assert result.fields["currency"].value == "EUR"


def test_unknown_currency_code_after_a_label_fails_validation():
    field = extract_invoice("Currency: XYZ\n").fields["currency"]
    assert field.status is FAILED
    assert field.value == "XYZ"
    assert "unknown currency code" in field.note


def test_currency_symbols_map_to_iso_codes():
    assert extract_invoice("Razem brutto: 1 230,00 zł\n").fields["currency"].value == "PLN"
    assert extract_invoice("Total due: €1,230.00\n").fields["currency"].value == "EUR"
    assert extract_invoice("Total due: 1,230.00 £\n").fields["currency"].value == "GBP"


def test_different_currencies_beside_different_amounts_are_ambiguous():
    field = extract_invoice("Net amount: 100,00 EUR\nTotal due: 123,00 USD\n").fields["currency"]
    assert field.status is AMBIGUOUS
    assert field.candidates == ("EUR", "USD")


def test_currency_mentioned_only_in_prose_is_used_as_a_last_resort():
    field = extract_invoice("All prices are quoted in GBP.\n").fields["currency"]
    assert (field.status, field.value) == (FOUND, "GBP")


def test_lowercase_words_that_look_like_codes_are_not_currencies():
    assert extract_invoice("We try our best.\n").fields["currency"].status is MISSING


# --- IBAN -------------------------------------------------------------------

def test_unspaced_iban_is_recognised():
    field = extract_invoice("IBAN: DE89370400440532013000\n").fields["iban"]
    assert (field.status, field.value) == (FOUND, "DE89370400440532013000")


def test_two_different_accounts_are_ambiguous():
    text = "Konto PLN: PL61 1090 1014 0000 0712 1981 2874\nKonto EUR: DE89 3704 0044 0532 0130 00\n"
    field = extract_invoice(text).fields["iban"]
    assert field.status is AMBIGUOUS
    assert field.candidates == ("DE89370400440532013000", "PL61109010140000071219812874")


def test_eu_vat_number_is_not_mistaken_for_an_iban():
    assert extract_invoice("VAT ID: PL5260250995\n").fields["iban"].status is MISSING


def test_iban_of_wrong_length_fails_validation_with_the_expected_length():
    field = extract_invoice("IBAN: PL61 1090 1014 0000 0712 1981 287\n").fields["iban"]
    assert field.status is FAILED
    assert field.note == "PL IBANs have 28 characters, found 27"


def test_iban_of_an_unlisted_country_needs_the_iban_label_to_be_considered():
    # Norway (15 characters) is not in the length table: validated by mod 97 only.
    labelled = extract_invoice("IBAN: NO93 8601 1117 947\n").fields["iban"]
    assert (labelled.status, labelled.value) == (FOUND, "NO9386011117947")
    unlabelled = extract_invoice("Reference NO93 8601 1117 947\n").fields["iban"]
    assert unlabelled.status is MISSING


@pytest.mark.parametrize(
    "line",
    [
        "IBAN: PL61 1090 1014 0000 0712 1981 2874 SWIFT: WBKPPLPP",
        "IBAN: PL61109010140000071219812874 SWIFT",
        "IBAN: PL61 1090 1014 0000 0712 1981 2874 BANK",
    ],
)
def test_iban_followed_by_another_word_does_not_swallow_it(line):
    field = extract_invoice(line + "\n").fields["iban"]
    assert (field.status, field.value) == (FOUND, "PL61109010140000071219812874")


def test_iban_with_one_digit_too_many_is_not_trimmed_into_a_valid_one():
    field = extract_invoice("IBAN: PL61 1090 1014 0000 0712 1981 28745\n").fields["iban"]
    assert field.status is FAILED
    assert field.note == "PL IBANs have 28 characters, found 29"


# --- output -----------------------------------------------------------------

def test_json_output_has_every_field_and_both_checks_with_string_values():
    payload = json.loads(extract_invoice(POLISH_STACKED).to_json())
    assert list(payload) == ["fields", "checks"]
    assert list(payload["fields"]) == [
        "invoice_number", "issue_date", "due_date", "seller_nip", "buyer_nip",
        "net_amount", "vat_amount", "gross_amount", "currency", "iban",
    ]
    gross = payload["fields"]["gross_amount"]
    assert gross == {
        "status": "found",
        "value": "1230.00",
        "raw": "1 230,00",
        "line": 22,  # "Razem brutto"; "Do zapłaty" on line 23 repeats the value
        "candidates": [],
        "note": None,
    }
    assert [c["name"] for c in payload["checks"]] == [
        "net_plus_vat_equals_gross",
        "due_date_not_before_issue_date",
    ]
    assert {c["status"] for c in payload["checks"]} == {"ok"}


def test_json_escapes_non_ascii_raw_text_and_round_trips():
    result = extract_invoice("Wszystkie ceny podano w zł.\n")
    output = result.to_json()
    assert output.isascii()
    assert "z\\u0142" in output
    assert json.loads(output)["fields"]["currency"]["raw"] == "zł"


def test_json_without_indent_is_a_single_line():
    assert "\n" not in extract_invoice("Faktura nr 1\n").to_json(indent=None)
