"""
NotificationEvent model — internal event-hook stub.

Services call emit_event() which writes here.
A future background worker (Celery / FastAPI BackgroundTasks)
will read undelivered events and dispatch to WhatsApp or other channels.

payload is JSONB for flexible, event-specific data.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class NotificationEventType(str, enum.Enum):
    order_placed = "order_placed"
    order_status_changed = "order_status_changed"
    appointment_confirmed = "appointment_confirmed"
    appointment_cancelled = "appointment_cancelled"
    report_ready = "report_ready"
    franchise_lead_received = "franchise_lead_received"
    new_review = "new_review"
    blog_published = "blog_published"


class NotificationEvent(TimestampMixin, Base):
    __tablename__ = "notification_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[NotificationEventType] = mapped_column(
        Enum(NotificationEventType, name="notificationeventtype"),
        nullable=False,
        index=True,
    )
    # Loosely coupled: user may or may not be known (e.g. franchise lead has no user)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Flexible JSON payload — structure varies per event_type
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_channel: Mapped[str | None] = mapped_column(
        String(50), nullable=True  # "whatsapp", "email", "sms" — extensible
    )
    delivery_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Relationships ──────────────────────────────────────────
    user: Mapped[User | None] = relationship(back_populates="notification_events")  # noqa: F821

    @property
    def is_delivered(self) -> bool:
        return self.delivered_at is not None

    def __repr__(self) -> str:
        return f"<NotificationEvent type={self.event_type} delivered={self.is_delivered}>"
