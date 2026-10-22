"""
PasswordResetOtp model — database store for password reset OTPs.

Stores HMAC-SHA256 hash of OTPs (never plaintext), with rate-limiting,
attempt counting, single-use token tracking, and IP metadata.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base


class PasswordResetOtp(Base):
    __tablename__ = "password_reset_otps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    otp_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reset_token_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────────
    user: Mapped[User | None] = relationship(  # noqa: F821
        back_populates="password_reset_otps"
    )

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def __repr__(self) -> str:
        return (
            f"<PasswordResetOtp id={self.id} phone={self.phone} "
            f"user_id={self.user_id} attempts={self.attempts} used={self.is_used}>"
        )
