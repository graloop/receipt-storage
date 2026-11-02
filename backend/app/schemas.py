# The shapes of what the API accepts and answers. FastAPI checks every request
# against these and answers 422 when something doesn't fit.

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal[
    "groceries",
    "restaurant",
    "household",
    "electronics",
    "clothing",
    "health",
    "transport",
    "entertainment",
    "services",
    "other",
]
Source = Literal["photo", "screenshot", "pdf", "email"]

ProfileName = Annotated[str, Field(min_length=1, max_length=40)]


class ProfileCreate(BaseModel):
    name: ProfileName
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    email: str | None = Field(default=None, max_length=200)

    @field_validator("name", mode="before")
    @classmethod
    def _tidy_name(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def _tidy_email(cls, value: str | None) -> str | None:
        email = (value or "").strip().casefold()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+", email):
            raise ValueError("This doesn't look like an email address.")
        return email or None


class ProfileUpdate(ProfileCreate):
    name: ProfileName | None = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str
    email: str | None = None
    receipt_count: int = 0


class Item(BaseModel):
    name: str
    qty: float | None = None
    price: float | None = None


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    profile_name: str
    source: Source
    merchant: str | None
    purchased_at: date | None
    added_on: date
    total: float | None
    currency: str
    category: Category
    items: list[Item]
    raw_text: str | None
    file_type: Literal["image", "pdf"] | None
    needs_review: bool
    unread: bool


class ReceiptUpdate(BaseModel):
    profile_id: int | None = None
    merchant: str | None = Field(default=None, max_length=200)
    purchased_at: date | None = None
    total: float | None = None
    currency: str | None = None
    category: Category | None = None
    items: list[Item] | None = None
    raw_text: str | None = None
    needs_review: bool | None = None

    @field_validator("currency")
    @classmethod
    def _currency_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError("Currency must be a 3-letter code like CAD.")
        return code


class UploadResult(BaseModel):
    """One per file, so one bad file doesn't hide what happened to the others."""

    filename: str
    status: Literal["added", "duplicate", "error"]
    message: str | None = None
    receipt: ReceiptOut | None = None


class UploadResponse(BaseModel):
    results: list[UploadResult]
