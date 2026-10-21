"""Result types returned by the extractor.

Every field carries a status instead of a bare value so that a caller can tell
"the invoice has no due date" apart from "the date we found is impossible".
"""

import json
from dataclasses import dataclass
from enum import Enum


class InvoiceTextError(ValueError):
    """Raised when the input cannot be processed at all (for example, empty text)."""


class FieldStatus(str, Enum):
    FOUND = "found"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    FAILED_VALIDATION = "failed_validation"


class CheckStatus(str, Enum):
    OK = "ok"
    INCONSISTENT = "inconsistent"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class FieldResult:
    """Outcome for one invoice field.

    ``value`` is normalised text (ISO date, plain decimal string, digits-only NIP,
    compact IBAN). It is None for MISSING and AMBIGUOUS results; for
    FAILED_VALIDATION it holds what was found so the caller can show it.
    ``candidates`` lists the competing readings of an AMBIGUOUS field.
    """

    status: FieldStatus
    value: str | None = None
    raw: str | None = None
    line: int | None = None
    candidates: tuple[str, ...] = ()
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "value": self.value,
            "raw": self.raw,
            "line": self.line,
            "candidates": list(self.candidates),
            "note": self.note,
        }


@dataclass(frozen=True)
class CrossCheck:
    """A consistency rule evaluated across several fields."""

    name: str
    status: CheckStatus
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status.value, "message": self.message}


@dataclass(frozen=True)
class InvoiceExtraction:
    fields: dict[str, FieldResult]
    checks: tuple[CrossCheck, ...]

    @property
    def inconsistencies(self) -> tuple[CrossCheck, ...]:
        """Only the cross-checks that failed."""
        return tuple(c for c in self.checks if c.status is CheckStatus.INCONSISTENT)

    def to_dict(self) -> dict[str, object]:
        return {
            "fields": {name: result.to_dict() for name, result in self.fields.items()},
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_json(self, indent: int | None = 2) -> str:
        """ASCII-only JSON so the output survives any console encoding."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=True)
