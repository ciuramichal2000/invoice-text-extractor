import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from invoiceextract.validators import iban_error, is_valid_iban, is_valid_nip, nip_error, normalize_iban


# NIP weights are 6 5 7 2 3 4 5 6 7 over the first nine digits; the check digit is
# the weighted sum mod 11. Worked by hand for the first example:
#   5252248481 -> 5*6 + 2*5 + 5*7 + 2*2 + 2*3 + 4*4 + 8*5 + 4*6 + 8*7
#              = 30 + 10 + 35 + 4 + 6 + 16 + 40 + 24 + 56 = 221 = 20 * 11 + 1 -> check digit 1.
@pytest.mark.parametrize("nip", ["5252248481", "5260250995", "1234563218"])
def test_nip_accepts_numbers_whose_checksum_is_correct(nip):
    assert is_valid_nip(nip)
    assert nip_error(nip) is None


def test_nip_wrong_check_digit_says_which_digit_was_expected():
    # 526025099 gives a weighted sum of 225 = 20 * 11 + 5, so the check digit must be 5.
    message = nip_error("5260250996")
    assert message is not None
    assert "expected check digit 5" in message
    assert "found 6" in message


def test_nip_remainder_ten_is_rejected_even_when_last_digit_is_zero():
    # 123456789 weighs 230 = 20 * 11 + 10. Computing "% 11 % 10" would turn that into 0
    # and wrongly accept 1234567890; no single digit can ever be a valid check digit here.
    assert not is_valid_nip("1234567890")
    assert "remainder is 10" in nip_error("1234567890")


@pytest.mark.parametrize(
    "bad",
    ["", "526025099", "52602509955", "52602A0995", "5260250995 ", "٥٢٦٠٢٥٠٩٩٥"],
)
def test_nip_rejects_wrong_shapes(bad):
    assert not is_valid_nip(bad)


def test_nip_all_zero_prefix_is_rejected_although_it_passes_the_checksum():
    assert not is_valid_nip("0000000000")
    assert "000" in nip_error("0000000000")


# The three valid IBANs below are the official examples of the IBAN registry for
# Poland, the United Kingdom and Germany.
@pytest.mark.parametrize(
    "iban",
    [
        "PL61 1090 1014 0000 0712 1981 2874",
        "GB82 WEST 1234 5698 7654 32",
        "DE89 3704 0044 0532 0130 00",
        "pl61109010140000071219812874",
    ],
)
def test_iban_accepts_registry_examples_in_any_spacing_and_case(iban):
    assert is_valid_iban(iban)


def test_iban_changed_digit_fails_the_mod_97_check():
    assert iban_error("PL61 1090 1014 0000 0712 1981 2875") == "IBAN checksum (mod 97) failed"


def test_iban_swapped_adjacent_digits_fail_the_mod_97_check():
    assert "mod 97" in iban_error("PL61 1090 1014 0000 0712 1981 2847")


def test_iban_wrong_length_for_known_country_is_reported_before_the_checksum():
    message = iban_error("PL61 1090 1014 0000 0712 1981 287")
    assert message == "PL IBANs have 28 characters, found 27"


@pytest.mark.parametrize("bad", ["", "1234", "XX00", "PL6", "6L61 1090 1014 0000 0712 1981 2874"])
def test_iban_rejects_text_that_is_not_shaped_like_an_iban(bad):
    assert not is_valid_iban(bad)
    assert "IBAN format" in iban_error(bad)


def test_normalize_iban_strips_every_kind_of_whitespace_and_uppercases():
    assert normalize_iban(" pl61\t1090 1014\u00a00000 0712 1981 2874 ") == "PL61109010140000071219812874"
