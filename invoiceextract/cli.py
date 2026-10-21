"""Command-line entry point: read invoice text, print the extraction as JSON."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .core import DEFAULT_TOLERANCE, extract_invoice


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m invoiceextract",
        description="Extract invoice fields from plain text and print them as JSON.",
    )
    parser.add_argument(
        "path", nargs="?", default="-", help="UTF-8 text file; '-' or omitted reads standard input"
    )
    parser.add_argument(
        "--tolerance",
        default=str(DEFAULT_TOLERANCE),
        help="largest accepted gap between net + VAT and gross (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    try:
        text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
        result = extract_invoice(text, tolerance=args.tolerance)
    except (OSError, ValueError) as error:  # includes InvoiceTextError and UnicodeDecodeError
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(result.to_json())
    return 0
