"""WhatsApp broadcast and campaign engine models.

Tracks marketing broadcasts, targeting filters, recipient dispatch queues,
and delivery state transitions.
"""
from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base
from app.models.base import TimestampMixin


class BroadcastStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    sending = "sending"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"


class BroadcastRecipientStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    skipped = "skipped"


class WhatsAppBroadcast(TimestampMixin, Base):
    """WhatsApp marketing broadcast campaign."""

    __tablename__ = "whatsapp_broadcasts"
    __table_args__ = (
        Index("ix_whatsapp_broadcasts_status", "status"),
        Index("ix_whatsapp_broadcasts_branch_id", "branch_id"),
        Index("ix_whatsapp_broadcasts_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_key: Mapped[str] = mapped_column(String(100), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")

    # Static template parameters (e.g. offer_title, discount_details, expiry_date)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # Segment filter parameters (marketing_opt_in=True is mandatory/forced)
    segment_filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    # Branch scope (nullable FK, set for branch-scoped broadcasts)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[BroadcastStatus] = mapped_column(
        Enum(BroadcastStatus, name="whatsappbroadcaststatus"),
        default=BroadcastStatus.draft,
        nullable=False,
    )

    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    total_recipients: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationships
    recipients: Mapped[list[WhatsAppBroadcastRecipient]] = relationship(
        back_populates="broadcast",
        cascade="all, delete-orphan",
    )
    creator = relationship("User", foreign_keys=[created_by])
    branch = relationship("Branch", foreign_keys=[branch_id])

    def __repr__(self) -> str:
        return (
            f"<WhatsAppBroadcast id={self.id} name='{self.name}' "
            f"status={self.status} total={self.total_recipients}>"
        )


class WhatsAppBroadcastRecipient(Base):
    """Individual recipient queue item for a broadcast campaign."""

    __tablename__ = "whatsapp_broadcast_recipients"
    __table_args__ = (
        UniqueConstraint(
            "broadcast_id", "phone", name="uq_broadcast_recipients_broadcast_phone"
        ),
        Index("ix_whatsapp_broadcast_recipients_status", "status"),
        Index("ix_whatsapp_broadcast_recipients_broadcast_id", "broadcast_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    broadcast_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whatsapp_broadcasts.id", ondelete="CASCADE"),
        nullable=False,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[BroadcastRecipientStatus] = mapped_column(
        Enum(BroadcastRecipientStatus, name="whatsappbroadcastrecipientstatus"),
        default=BroadcastRecipientStatus.pending,
        nullable=False,
    )
    skip_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whatsapp_messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    broadcast: Mapped[WhatsAppBroadcast] = relationship(back_populates="recipients")
    user = relationship("User", foreign_keys=[user_id])
    message = relationship("WhatsAppMessage", foreign_keys=[message_id])

    def __repr__(self) -> str:
        return (
            f"<WhatsAppBroadcastRecipient id={self.id} broadcast_id={self.broadcast_id} "
            f"phone={self.phone} status={self.status}>"
        )
