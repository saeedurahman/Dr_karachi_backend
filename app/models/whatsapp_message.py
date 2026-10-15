"""WhatsApp message log model for delivery auditing, state tracking, and deduplication.

Stores only metadata necessary for auditing and delivery reconciliation.
Raw webhook payloads, free-form text bodies, and sensitive medical details are
deliberately excluded.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database_base import Base
from app.models.base import TimestampMixin


class WhatsAppMessageDirection(str, enum.Enum):
    outbound = "outbound"
    inbound = "inbound"


class WhatsAppMessageType(str, enum.Enum):
    template = "template"
    text = "text"
    interactive = "interactive"
    button = "button"
    other = "other"


class WhatsAppMessageStatus(str, enum.Enum):
    queued = "queued"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"


class WhatsAppMessage(TimestampMixin, Base):
    """Log entry for an outbound or inbound WhatsApp message."""

    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        Index("ix_whatsapp_messages_related_entity", "related_entity_type", "related_entity_id"),
        Index(
            "uq_whatsapp_messages_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Meta message ID (e.g. wamid.HBgM...)
    wamid: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )

    # Recipient or sender phone (normalized E.164 digits without plus, e.g. 923001234567)
    recipient_phone: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    direction: Mapped[WhatsAppMessageDirection] = mapped_column(
        Enum(
            WhatsAppMessageDirection,
            name="whatsappmessagedirection",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    message_type: Mapped[WhatsAppMessageType] = mapped_column(
        Enum(
            WhatsAppMessageType,
            name="whatsappmessagetype",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    template_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    status: Mapped[WhatsAppMessageStatus] = mapped_column(
        Enum(
            WhatsAppMessageStatus,
            name="whatsappmessagestatus",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=WhatsAppMessageStatus.queued,
        index=True,
    )

    error_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Polymorphic association to business entities (e.g. appointment, order, lab_report)
    related_entity_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Deduplication key for idempotent job triggers (e.g. "appointment:<id>:reminder_1h")
    dedupe_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Monotonic lifecycle timestamps
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<WhatsAppMessage id={self.id} wamid={self.wamid} "
            f"to={self.recipient_phone} status={self.status.value}>"
        )
