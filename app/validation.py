"""Per-recipient validation.

Recipients are validated one by one (not as part of the request model) so that a single
bad row never rejects the whole bulk request.
"""
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Deliberately simple: one "@", no spaces, a dot in the domain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class RecipientIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if _CONTROL_CHARS_RE.search(value):
            raise ValueError("must not contain control characters")
        try:
            # The template uses PDF built-in fonts (WinAnsi); anything else would
            # render as blank boxes, so reject it up front with a clear message.
            value.encode("cp1252")
        except UnicodeEncodeError:
            raise ValueError(
                "contains characters the certificate font cannot render "
                "(only Latin characters are supported)"
            )
        return value

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not _EMAIL_RE.match(value):
            raise ValueError("is not a valid email address")
        return value.lower()


@dataclass
class ValidRecipient:
    index: int
    name: str
    email: str | None


@dataclass
class InvalidRecipient:
    index: int
    name: str | None  # raw values kept (truncated) so the client can identify the row
    email: str | None
    error: str


def _format_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "recipient"
        msg = err["msg"].removeprefix("Value error, ")
        parts.append(f"{field}: {msg}")
    return "; ".join(parts)


def _raw_str(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def validate_recipients(
    raw_recipients: list[Any],
) -> tuple[list[ValidRecipient], list[InvalidRecipient]]:
    valid: list[ValidRecipient] = []
    invalid: list[InvalidRecipient] = []

    for index, raw in enumerate(raw_recipients):
        if not isinstance(raw, dict):
            invalid.append(
                InvalidRecipient(index, None, None, "recipient must be an object")
            )
            continue
        try:
            parsed = RecipientIn.model_validate(raw)
        except ValidationError as exc:
            invalid.append(
                InvalidRecipient(
                    index,
                    _raw_str(raw.get("name"), 200),
                    _raw_str(raw.get("email"), 254),
                    _format_error(exc),
                )
            )
        else:
            valid.append(ValidRecipient(index, parsed.name, parsed.email))

    return valid, invalid
