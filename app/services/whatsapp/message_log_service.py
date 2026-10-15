"""WhatsApp message logging and delivery reconciliation service.

Handles:
- Creating outbound message logs with deduplication keys
- Transitioning message states (sent, delivered, read, failed) via a monotonic state machine
- Recording inbound messages idempotently
- Safe auditing without storing message content or raw webhook payloads
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.whatsapp_message import (
    WhatsAppMessage,
    WhatsAppMessageDirection,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from app.services.whatsapp.exceptions import DuplicateMessageError
from app.utils.phone import normalize_phone_number

# Monotonic state progression ranks: queued < sent < delivered < read
STATUS_RANKS: dict[WhatsAppMessageStatus, int] = {
    WhatsAppMessageStatus.queued: 0,
    WhatsAppMessageStatus.sent: 1,
    WhatsAppMessageStatus.delivered: 2,
    WhatsAppMessageStatus.read: 3,
}


def _mask_phone(phone: str) -> str:
    """Mask phone number for privacy-safe logs."""
    if len(phone) >= 7:
        return f"{phone[:5]}***{phone[-2:]}"
    return "***"


def _clean_phone_lenient(phone: str) -> str:
    """Normalize phone if valid Pakistani, else strip non-digits without crashing."""
    try:
        return normalize_phone_number(phone)
    except Exception:
        return re.sub(r"[^\d]", "", str(phone or ""))


def _parse_timestamp(timestamp: datetime | int | float | str | None) -> datetime:
    """Safely convert webhook timestamp (Unix epoch or datetime) to timezone-aware UTC datetime."""
    if timestamp is None:
        return datetime.now(UTC)
    if isinstance(timestamp, datetime):
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
    try:
        ts_float = float(timestamp)
        return datetime.fromtimestamp(ts_float, tz=UTC)
    except Exception:
        return datetime.now(UTC)


class MessageLogService:
    """Service for managing WhatsApp message audit logs and monotonic status progression."""

    @staticmethod
    async def create_outbound(
        db: AsyncSession,
        recipient_phone: str,
        message_type: WhatsAppMessageType | str,
        template_name: str | None = None,
        related_entity_type: str | None = None,
        related_entity_id: uuid.UUID | str | None = None,
        dedupe_key: str | None = None,
        raise_on_duplicate: bool = False,
    ) -> WhatsAppMessage:
        """Create a queued outbound message log entry.

        If dedupe_key is provided and already exists:
        - If raise_on_duplicate is True: raises DuplicateMessageError
        - If raise_on_duplicate is False: returns the existing record (never duplicate or re-queue)

        Args:
            db: Active async database session.
            recipient_phone: Destination phone number.
            message_type: Type of message (template, text, interactive, etc.).
            template_name: Optional template name.
            related_entity_type: Optional business entity type (e.g. "appointment", "order").
            related_entity_id: Optional business entity UUID.
            dedupe_key: Optional unique deduplication key (e.g. "appointment:{id}:reminder_1h").
            raise_on_duplicate: Whether to raise DuplicateMessageError on collision.

        Returns:
            The created or existing WhatsAppMessage ORM instance.
        """
        normalized_phone = _clean_phone_lenient(recipient_phone)

        # 1. Pre-check deduplication key
        if dedupe_key:
            stmt = select(WhatsAppMessage).where(WhatsAppMessage.dedupe_key == dedupe_key)
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing:
                logger.info(
                    "Duplicate outbound WhatsApp message ignored | dedupe_key={} existing_id={}",
                    dedupe_key,
                    existing.id,
                )
                if raise_on_duplicate:
                    raise DuplicateMessageError(
                        f"WhatsApp message with dedupe_key '{dedupe_key}' already exists.",
                        existing_message=existing,
                    )
                return existing

        # 2. Resolve enum message type
        resolved_type = WhatsAppMessageType.other
        if isinstance(message_type, WhatsAppMessageType):
            resolved_type = message_type
        elif isinstance(message_type, str):
            try:
                resolved_type = WhatsAppMessageType(message_type.lower())
            except ValueError:
                resolved_type = WhatsAppMessageType.other

        entity_uuid: uuid.UUID | None = None
        if related_entity_id:
            entity_uuid = (
                related_entity_id
                if isinstance(related_entity_id, uuid.UUID)
                else uuid.UUID(str(related_entity_id))
            )

        msg = WhatsAppMessage(
            recipient_phone=normalized_phone,
            direction=WhatsAppMessageDirection.outbound,
            message_type=resolved_type,
            template_name=template_name,
            status=WhatsAppMessageStatus.queued,
            related_entity_type=related_entity_type,
            related_entity_id=entity_uuid,
            dedupe_key=dedupe_key,
        )
        db.add(msg)

        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            if dedupe_key:
                stmt = select(WhatsAppMessage).where(WhatsAppMessage.dedupe_key == dedupe_key)
                existing = (await db.execute(stmt)).scalar_one_or_none()
                if existing:
                    if raise_on_duplicate:
                        raise DuplicateMessageError(
                            f"WhatsApp message with dedupe_key '{dedupe_key}' already exists.",
                            existing_message=existing,
                        )
                    return existing
            raise

        logger.info(
            "Outbound WhatsApp message logged | id={} to={} type={} dedupe_key={}",
            msg.id,
            _mask_phone(normalized_phone),
            resolved_type.value,
            dedupe_key,
        )
        return msg

    @staticmethod
    async def mark_sent(
        db: AsyncSession,
        message_id: uuid.UUID | str,
        wamid: str,
        sent_at: datetime | None = None,
    ) -> WhatsAppMessage | None:
        """Mark a queued message as sent with Meta's assigned wamid."""
        uid = message_id if isinstance(message_id, uuid.UUID) else uuid.UUID(str(message_id))
        msg = await db.get(WhatsAppMessage, uid)
        if not msg:
            logger.warning("Attempted to mark_sent on unknown message_id={}", message_id)
            return None

        msg.wamid = wamid
        msg.status = WhatsAppMessageStatus.sent
        msg.sent_at = sent_at or datetime.now(UTC)
        await db.flush()

        logger.info("WhatsApp message marked sent | id={} wamid={}", msg.id, wamid)
        return msg

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        message_id: uuid.UUID | str,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> WhatsAppMessage | None:
        """Mark a message as failed with error code and description."""
        uid = message_id if isinstance(message_id, uuid.UUID) else uuid.UUID(str(message_id))
        msg = await db.get(WhatsAppMessage, uid)
        if not msg:
            logger.warning("Attempted to mark_failed on unknown message_id={}", message_id)
            return None

        # Do not allow transitioning to failed if message was already read
        if msg.status == WhatsAppMessageStatus.read:
            logger.info("Ignoring mark_failed on already read message id={}", msg.id)
            return msg

        msg.status = WhatsAppMessageStatus.failed
        msg.error_code = error_code
        msg.error_message = error_message
        await db.flush()

        logger.warning(
            "WhatsApp message marked failed | id={} code={} error={}",
            msg.id,
            error_code,
            error_message,
        )
        return msg

    @staticmethod
    async def apply_status_update(
        db: AsyncSession,
        wamid: str,
        status: WhatsAppMessageStatus | str,
        timestamp: datetime | int | float | str | None = None,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> WhatsAppMessage | None:
        """Apply a status update from Meta webhook following a monotonic state machine.

        State progression rules:
        - Hierarchy: queued (0) < sent (1) < delivered (2) < read (3)
        - Never move backwards (e.g. delivered after read is ignored)
        - Failed allowed from any non-read state
        - Unknown wamid safely logged and ignored without error
        """
        if not wamid:
            return None

        stmt = select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        msg = (await db.execute(stmt)).scalar_one_or_none()

        if not msg:
            logger.warning("Status update received for unknown wamid={}", wamid)
            return None

        event_time = _parse_timestamp(timestamp)

        # Resolve target status
        target_status: WhatsAppMessageStatus | None = None
        if isinstance(status, WhatsAppMessageStatus):
            target_status = status
        elif isinstance(status, str):
            try:
                target_status = WhatsAppMessageStatus(status.lower())
            except ValueError:
                logger.warning("Unrecognized WhatsApp status '{}' for wamid={}", status, wamid)
                return msg

        # Rule 1: Handling transition to 'failed'
        if target_status == WhatsAppMessageStatus.failed:
            if msg.status == WhatsAppMessageStatus.read:
                logger.info("Ignoring failed status for already read message wamid={}", wamid)
                return msg

            msg.status = WhatsAppMessageStatus.failed
            if error_code is not None:
                msg.error_code = error_code
            if error_message:
                msg.error_message = error_message
            await db.flush()
            logger.warning("WhatsApp message updated to failed | wamid={} code={}", wamid, error_code)
            return msg

        # Rule 2: If message is already failed, do not regress
        if msg.status == WhatsAppMessageStatus.failed:
            logger.info("Ignoring status {} for already failed message wamid={}", target_status.value, wamid)
            return msg

        # Rule 3: Progressive status transitions (queued < sent < delivered < read)
        curr_rank = STATUS_RANKS.get(msg.status, -1)
        target_rank = STATUS_RANKS.get(target_status, -1)

        if target_rank <= curr_rank:
            logger.info(
                "Ignoring non-progressive status update {} -> {} for wamid={}",
                msg.status.value,
                target_status.value,
                wamid,
            )
            return msg

        # Advance status and backfill progression timestamps
        msg.status = target_status
        if target_status == WhatsAppMessageStatus.sent:
            if not msg.sent_at:
                msg.sent_at = event_time
        elif target_status == WhatsAppMessageStatus.delivered:
            if not msg.delivered_at:
                msg.delivered_at = event_time
            if not msg.sent_at:
                msg.sent_at = event_time
        elif target_status == WhatsAppMessageStatus.read:
            if not msg.read_at:
                msg.read_at = event_time
            if not msg.delivered_at:
                msg.delivered_at = event_time
            if not msg.sent_at:
                msg.sent_at = event_time

        await db.flush()
        logger.info("WhatsApp status progressed | wamid={} new_status={}", wamid, target_status.value)
        return msg

    @staticmethod
    async def log_inbound(
        db: AsyncSession,
        wamid: str,
        phone: str,
        message_type: WhatsAppMessageType | str = WhatsAppMessageType.text,
    ) -> WhatsAppMessage:
        """Log an incoming WhatsApp message idempotently.

        If the wamid was already logged, returns the existing record without duplicating.
        Tolerates non-Pakistani phone numbers without crashing.
        """
        if not wamid:
            raise ValueError("wamid is required for logging inbound messages")

        # 1. Idempotency check on wamid
        stmt = select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            logger.info("Inbound WhatsApp message already logged | wamid={}", wamid)
            return existing

        normalized_phone = _clean_phone_lenient(phone)

        resolved_type = WhatsAppMessageType.other
        if isinstance(message_type, WhatsAppMessageType):
            resolved_type = message_type
        elif isinstance(message_type, str):
            try:
                resolved_type = WhatsAppMessageType(message_type.lower())
            except ValueError:
                resolved_type = WhatsAppMessageType.other

        now = datetime.now(UTC)
        inbound_msg = WhatsAppMessage(
            wamid=wamid,
            recipient_phone=normalized_phone,
            direction=WhatsAppMessageDirection.inbound,
            message_type=resolved_type,
            status=WhatsAppMessageStatus.delivered,
            delivered_at=now,
        )
        db.add(inbound_msg)

        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            stmt = select(WhatsAppMessage).where(WhatsAppMessage.wamid == wamid)
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing:
                return existing
            raise

        logger.info(
            "Inbound WhatsApp message logged | wamid={} from={} type={}",
            wamid,
            _mask_phone(normalized_phone),
            resolved_type.value,
        )
        return inbound_msg


# Module-level convenience functions
create_outbound = MessageLogService.create_outbound
mark_sent = MessageLogService.mark_sent
mark_failed = MessageLogService.mark_failed
apply_status_update = MessageLogService.apply_status_update
log_inbound = MessageLogService.log_inbound
