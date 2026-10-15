"""WhatsApp user consent and opt-in preferences model.

Tracks:
- Transactional communication opt-in (appointments, order updates, report links)
- Promotional / marketing communication opt-in
- Timestamped audit history of opt-in/opt-out actions and sources
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database_base import Base
from app.models.base import TimestampMixin


class WhatsAppConsent(TimestampMixin, Base):
    """Stores WhatsApp messaging consent preferences per phone number."""

    __tablename__ = "whatsapp_consents"

    # Primary key: normalized E.164 phone digits (e.g. 923001234567)
    phone: Mapped[str] = mapped_column(
        String(20),
        primary_key=True,
    )

    # Optional foreign key to users table
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Transactional messages (appointment reminders, invoices, OTPs, lab reports)
    transactional_opt_in: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Promotional & marketing broadcasts
    marketing_opt_in: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Timestamp when marketing opt-out occurred
    opt_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Attribution source of the latest opt-in (e.g. "patient_registration", "checkout", "whatsapp_keyword")
    opt_in_source: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Audit history list: [{at: iso_string, action: string, source: string}]
    history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<WhatsAppConsent phone={self.phone} "
            f"transactional={self.transactional_opt_in} marketing={self.marketing_opt_in}>"
        )
