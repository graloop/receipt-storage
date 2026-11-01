# The two database tables: who (profiles) and what (receipts).

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    color: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    receipts: Mapped[list["Receipt"]] = relationship(back_populates="profile")


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), index=True)
    source: Mapped[str] = mapped_column(String(10))  # photo, screenshot, pdf or email
    merchant: Mapped[str | None] = mapped_column(String(200))
    purchased_at: Mapped[date | None] = mapped_column(Date, index=True)
    total: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    category: Mapped[str] = mapped_column(String(20), default="other")
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{name, qty, price}]
    raw_text: Mapped[str | None] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(String(100))  # a file name inside uploads/
    # UNIQUE lets the database itself refuse a second copy of the same file or email.
    file_sha256: Mapped[str | None] = mapped_column(String(64), unique=True)
    email_message_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    needs_review: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    profile: Mapped[Profile] = relationship(back_populates="receipts", lazy="joined")

    @property
    def profile_name(self) -> str:
        return self.profile.name

    @property
    def added_on(self) -> date:
        """The day it was added, on the server's clock (created_at is stored in UTC)."""
        return self.created_at.replace(tzinfo=UTC).astimezone().date()

    @property
    def file_type(self) -> str | None:
        """What the page should show: an image, a PDF, or nothing (an email has no file)."""
        if not self.image_path:
            return None
        return "pdf" if self.image_path.endswith(".pdf") else "image"
