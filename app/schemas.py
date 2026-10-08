from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _iso_utc(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


UtcDatetime = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]


class JobCreate(BaseModel):
    """Request body. Recipients stay untyped here and are validated row by row."""

    model_config = ConfigDict(str_strip_whitespace=True)

    event_name: str = Field(min_length=1, max_length=200)
    issuer: str = Field(default="Certificate Authority", min_length=1, max_length=200)
    issue_date: date | None = None  # defaults to today (UTC)
    recipients: list[Any] = Field(min_length=1)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    event_name: str
    issuer: str
    issue_date: date
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    error: str | None
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    index: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None
    generated_at: UtcDatetime | None
    download_url: str | None


class CertificatePage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]


class JobPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[JobOut]
