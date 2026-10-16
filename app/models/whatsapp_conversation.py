"""WhatsApp conversation state model for auto-reply and human handoff tracking."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database_base import Base
from app.models.base import TimestampMixin


class ConversationMode(str, enum.Enum):
    bot = "bot"
    human_agent = "human_agent"


class WhatsAppConversation(TimestampMixin, Base):
    """Tracks patient conversation mode, human handoff expiry, and rate limit counters."""

    __tablename__ = "whatsapp_conversations"

    # Normalized E.164 phone without plus sign (e.g. 923001234567)
    phone: Mapped[str] = mapped_column(String(20), primary_key=True)

    mode: Mapped[ConversationMode] = mapped_column(
        Enum(
            ConversationMode,
            name="whatsappconversationmode",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ConversationMode.bot,
        nullable=False,
    )

    handoff_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    handoff_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    auto_reply_count_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    auto_reply_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<WhatsAppConversation phone={self.phone!r} mode={self.mode.value!r}>"
