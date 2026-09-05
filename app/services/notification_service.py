"""
Notification service — internal event-hook system.

Call emit_event() from any service to record a notification event.
A future delivery worker will process undelivered rows.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NotificationEvent, NotificationEventType
import uuid as _uuid


async def emit_event(
    db: AsyncSession,
    event_type: NotificationEventType,
    payload: dict,
    user_id: _uuid.UUID | None = None,
    delivery_channel: str | None = None,
) -> NotificationEvent:
    """
    Persist a notification event to the database.
    The delivery worker picks this up asynchronously.

    Args:
        db: active DB session
        event_type: one of NotificationEventType enum values
        payload: event-specific data dict (flexible JSON)
        user_id: optional — the patient/user to notify
        delivery_channel: optional hint ("whatsapp", "sms", "email")

    Returns:
        The persisted NotificationEvent ORM instance.
    """
    event = NotificationEvent(
        event_type=event_type,
        user_id=user_id,
        payload=payload,
        triggered_at=datetime.now(timezone.utc),
        delivery_channel=delivery_channel,
    )
    db.add(event)
    await db.flush()  # get the id without full commit (caller commits)
    return event
