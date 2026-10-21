import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import io
import json

from invoiceextract.cli import main

INVOICE = (
    "Faktura nr FV/9/2024\n"
    "Razem netto: 100,00 PLN\n"
    "VAT: 23,00 PLN\n"
    "Razem brutto: 123,02 PLN\n"
)


def test_reads_a_utf8_file_and_prints_json(tmp_path, capsys):
    path = tmp_path / "invoice.txt"
    path.write_text("Termin płatności: 28.05.2024\n" + INVOICE, encoding="utf-8")

    assert main([str(path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["fields"]["due_date"]["value"] == "2024-05-28"
    assert payload["fields"]["gross_amount"]["value"] == "123.02"


def test_reads_standard_input_when_no_path_or_a_dash_is_given(monkeypatch, capsys):
    for argv in ([], ["-"]):
        monkeypatch.setattr(sys, "stdin", io.StringIO(INVOICE))
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["fields"]["invoice_number"]["value"] == "FV/9/2024"


def test_tolerance_option_changes_the_cross_check(tmp_path, capsys):
    path = tmp_path / "invoice.txt"
    path.write_text(INVOICE, encoding="utf-8")

    main([str(path)])
    strict = json.loads(capsys.readouterr().out)
    main([str(path), "--tolerance", "0.02"])
    relaxed = json.loads(capsys.readouterr().out)

    # 100.00 + 23.00 = 123.00 against 123.02: a gap of exactly 0.02.
    assert strict["checks"][0]["status"] == "inconsistent"
    assert relaxed["checks"][0]["status"] == "ok"


def test_empty_input_exits_with_code_2_and_a_message(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("   \n"))
    assert main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "empty" in captured.err


def test_missing_file_exits_with_code_2(tmp_path, capsys):
    assert main([str(tmp_path / "nope.txt")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:")


def test_file_that_is_not_utf8_exits_with_code_2(tmp_path, capsys):
    path = tmp_path / "latin.txt"
    path.write_bytes("Termin płatności".encode("cp1250"))
    assert main([str(path)]) == 2
    assert "error:" in capsys.readouterr().err


def test_invalid_tolerance_exits_with_code_2(tmp_path, capsys):
    path = tmp_path / "invoice.txt"
    path.write_text(INVOICE, encoding="utf-8")
    assert main([str(path), "--tolerance", "abc"]) == 2
    assert "tolerance" in capsys.readouterr().err
