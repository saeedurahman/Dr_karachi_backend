"""
Review model — ratings on doctors or services.
target_type determines what target_id references (polymorphic association).
is_approved allows admin moderation before reviews go public.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base
from app.models.base import TimestampMixin


class ReviewTargetType(str, enum.Enum):
    doctor = "doctor"
    service = "service"


class Review(TimestampMixin, Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating"),
        UniqueConstraint("user_id", "target_type", "target_id", name="uq_user_target_review"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[ReviewTargetType] = mapped_column(
        Enum(ReviewTargetType, name="reviewtargettype"), nullable=False, index=True
    )
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    # ── Relationships ──────────────────────────────────────────
    author: Mapped["User"] = relationship(back_populates="reviews")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Review target={self.target_type}:{self.target_id} rating={self.rating}>"
