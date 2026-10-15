"""Tests for WhatsApp message log service, state machine, deduplication, and webhook processing."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.main import create_app
from app.models.whatsapp_message import (
    WhatsAppMessage,
    WhatsAppMessageDirection,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from app.services.whatsapp.exceptions import DuplicateMessageError
from app.services.whatsapp.message_log_service import MessageLogService

SECRET = "test-webhook-app-secret-12345"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _wa_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "verify-token-123")


# ── 1. MessageLogService Tests ───────────────────────────────────────────────

async def test_create_outbound_and_mark_sent(db_session: AsyncSession):
    msg = await MessageLogService.create_outbound(
        db=db_session,
        recipient_phone="0300-1234567",
        message_type=WhatsAppMessageType.template,
        template_name="appointment_confirmed",
        related_entity_type="appointment",
        related_entity_id=uuid.uuid4(),
    )
    await db_session.flush()

    assert msg.id is not None
    assert msg.recipient_phone == "923001234567"
    assert msg.direction == WhatsAppMessageDirection.outbound
    assert msg.status == WhatsAppMessageStatus.queued
    assert msg.template_name == "appointment_confirmed"
    assert msg.wamid is None

    # Mark sent
    wamid = f"wamid.{uuid.uuid4()}"
    updated = await MessageLogService.mark_sent(db_session, msg.id, wamid)
    await db_session.flush()

    assert updated is not None
    assert updated.status == WhatsAppMessageStatus.sent
    assert updated.wamid == wamid
    assert updated.sent_at is not None


async def test_dedupe_key_collision_handling(db_session: AsyncSession):
    entity_id = uuid.uuid4()
    dedupe_key = f"appointment:{entity_id}:reminder_1h"

    # First insert
    msg1 = await MessageLogService.create_outbound(
        db=db_session,
        recipient_phone="03001234567",
        message_type="template",
        template_name="appointment_reminder_1h",
        dedupe_key=dedupe_key,
    )
    await db_session.flush()

    # Second call without raise_on_duplicate -> returns existing
    msg2 = await MessageLogService.create_outbound(
        db=db_session,
        recipient_phone="03001234567",
        message_type="template",
        template_name="appointment_reminder_1h",
        dedupe_key=dedupe_key,
        raise_on_duplicate=False,
    )
    assert msg2.id == msg1.id

    # Third call with raise_on_duplicate -> raises DuplicateMessageError
    with pytest.raises(DuplicateMessageError) as exc_info:
        await MessageLogService.create_outbound(
            db=db_session,
            recipient_phone="03001234567",
            message_type="template",
            template_name="appointment_reminder_1h",
            dedupe_key=dedupe_key,
            raise_on_duplicate=True,
        )
    assert exc_info.value.existing_message.id == msg1.id


async def test_monotonic_status_progression(db_session: AsyncSession):
    wamid = f"wamid.{uuid.uuid4()}"
    msg = await MessageLogService.create_outbound(
        db=db_session,
        recipient_phone="03001234567",
        message_type="text",
    )
    await MessageLogService.mark_sent(db_session, msg.id, wamid)
    await db_session.flush()

    # 1. Transition to delivered
    ts_delivered = 1729000000
    res_del = await MessageLogService.apply_status_update(
        db=db_session,
        wamid=wamid,
        status="delivered",
        timestamp=ts_delivered,
    )
    await db_session.flush()
    assert res_del is not None
    assert res_del.status == WhatsAppMessageStatus.delivered
    assert res_del.delivered_at is not None

    # 2. Transition to read
    ts_read = 1729000100
    res_read = await MessageLogService.apply_status_update(
        db=db_session,
        wamid=wamid,
        status="read",
        timestamp=ts_read,
    )
    await db_session.flush()
    assert res_read is not None
    assert res_read.status == WhatsAppMessageStatus.read
    assert res_read.read_at is not None


async def test_out_of_order_events_read_before_delivered(db_session: AsyncSession):
    """When read arrives before delivered, status jumps to read and delivered later is ignored."""
    wamid = f"wamid.{uuid.uuid4()}"
    msg = await MessageLogService.create_outbound(
        db=db_session,
        recipient_phone="03001234567",
        message_type="template",
    )
    await MessageLogService.mark_sent(db_session, msg.id, wamid)
    await db_session.flush()

    # Out of order: 'read' arrives first
    ts_read = 1729000200
    await MessageLogService.apply_status_update(
        db=db_session,
        wamid=wamid,
        status="read",
        timestamp=ts_read,
    )
    await db_session.flush()

    # Re-fetch
    updated = await db_session.get(WhatsAppMessage, msg.id)
    assert updated.status == WhatsAppMessageStatus.read
    assert updated.read_at is not None
    assert updated.delivered_at is not None  # backfilled

    # Delayed 'delivered' event arrives
    ts_del = 1729000100
    await MessageLogService.apply_status_update(
        db=db_session,
        wamid=wamid,
        status="delivered",
        timestamp=ts_del,
    )
    await db_session.flush()

    # Must NOT regress to delivered
    updated_again = await db_session.get(WhatsAppMessage, msg.id)
    assert updated_again.status == WhatsAppMessageStatus.read


async def test_duplicate_webhook_delivery_is_idempotent(db_session: AsyncSession):
    wamid = f"wamid.{uuid.uuid4()}"
    msg = await MessageLogService.create_outbound(
        db=db_session,
        recipient_phone="03001234567",
        message_type="text",
    )
    await MessageLogService.mark_sent(db_session, msg.id, wamid)
    await db_session.flush()

    # First delivery event
    await MessageLogService.apply_status_update(db_session, wamid, "delivered", 1729000100)
    await db_session.flush()

    # Duplicate delivery event
    res = await MessageLogService.apply_status_update(db_session, wamid, "delivered", 1729000100)
    await db_session.flush()

    assert res.status == WhatsAppMessageStatus.delivered


async def test_unknown_wamid_safely_ignored(db_session: AsyncSession):
    unknown_wamid = "wamid.unknown.123456"
    res = await MessageLogService.apply_status_update(db_session, unknown_wamid, "delivered")
    assert res is None


async def test_failed_status_with_error_code(db_session: AsyncSession):
    wamid = f"wamid.{uuid.uuid4()}"
    msg = await MessageLogService.create_outbound(
        db=db_session,
        recipient_phone="03001234567",
        message_type="template",
    )
    await MessageLogService.mark_sent(db_session, msg.id, wamid)
    await db_session.flush()

    res = await MessageLogService.apply_status_update(
        db=db_session,
        wamid=wamid,
        status="failed",
        error_code=131026,
        error_message="Message undeliverable",
    )
    await db_session.flush()

    assert res.status == WhatsAppMessageStatus.failed
    assert res.error_code == 131026
    assert res.error_message == "Message undeliverable"


async def test_failed_status_ignored_if_already_read(db_session: AsyncSession):
    """Failed is allowed from any non-read state. Once read, it cannot transition to failed."""
    wamid = f"wamid.{uuid.uuid4()}"
    msg = await MessageLogService.create_outbound(
        db=db_session,
        recipient_phone="03001234567",
        message_type="template",
    )
    await MessageLogService.mark_sent(db_session, msg.id, wamid)
    await MessageLogService.apply_status_update(db_session, wamid, "read")
    await db_session.flush()

    res = await MessageLogService.apply_status_update(
        db=db_session,
        wamid=wamid,
        status="failed",
        error_code=131047,
        error_message="Outside 24h window",
    )
    await db_session.flush()

    assert res.status == WhatsAppMessageStatus.read


async def test_inbound_idempotency_and_phone_tolerance(db_session: AsyncSession):
    inbound_wamid = f"wamid.inbound.{uuid.uuid4()}"

    # First delivery
    msg1 = await MessageLogService.log_inbound(
        db=db_session,
        wamid=inbound_wamid,
        phone="+92 300 1234567",
        message_type="text",
    )
    await db_session.flush()

    assert msg1.direction == WhatsAppMessageDirection.inbound
    assert msg1.recipient_phone == "923001234567"
    assert msg1.status == WhatsAppMessageStatus.delivered

    # Redelivery of same wamid
    msg2 = await MessageLogService.log_inbound(
        db=db_session,
        wamid=inbound_wamid,
        phone="+92 300 1234567",
        message_type="text",
    )
    assert msg2.id == msg1.id

    # Non-Pakistani phone number (e.g. US +1 415 555 2671) tolerated without crashing
    foreign_wamid = f"wamid.foreign.{uuid.uuid4()}"
    msg_foreign = await MessageLogService.log_inbound(
        db=db_session,
        wamid=foreign_wamid,
        phone="+1 415 555 2671",
        message_type="interactive",
    )
    await db_session.flush()
    assert msg_foreign.direction == WhatsAppMessageDirection.inbound
    assert "14155552671" in msg_foreign.recipient_phone


# ── 2. Webhook Endpoint Integration Tests ────────────────────────────────────

async def test_webhook_endpoint_signed_payload_integration(async_client: AsyncClient, db_session: AsyncSession):
    outbound_wamid = f"wamid.out.{uuid.uuid4()}"
    outbound_msg = await MessageLogService.create_outbound(
        db=db_session,
        recipient_phone="03001234567",
        message_type="template",
    )
    await MessageLogService.mark_sent(db_session, outbound_msg.id, outbound_wamid)
    await db_session.flush()

    inbound_wamid = f"wamid.in.{uuid.uuid4()}"
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": outbound_wamid,
                                    "status": "delivered",
                                    "recipient_id": "923001234567",
                                    "timestamp": "1729000500",
                                }
                            ],
                            "messages": [
                                {
                                    "id": inbound_wamid,
                                    "from": "03211234567",
                                    "type": "text",
                                    "text": {"body": "Prescription inquiry"},
                                    "timestamp": "1729000505",
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _sign(body),
        "Content-Type": "application/json",
    }

    response = await async_client.post("/api/v1/webhooks/whatsapp", content=body, headers=headers)
    assert response.status_code == 200

    # Verify database updates
    updated_out = await db_session.get(WhatsAppMessage, outbound_msg.id)
    assert updated_out.status == WhatsAppMessageStatus.delivered

    inbound_check = (
        await db_session.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.wamid == inbound_wamid)
        )
    ).scalar_one_or_none()
    assert inbound_check is not None
    assert inbound_check.direction == WhatsAppMessageDirection.inbound
    assert inbound_check.recipient_phone == "923211234567"


async def test_webhook_endpoint_invalid_signature_rejected(async_client: AsyncClient):
    body = b'{"test": 1}'
    headers = {
        "X-Hub-Signature-256": _sign(body, secret="wrong-secret"),
        "Content-Type": "application/json",
    }
    response = await async_client.post("/api/v1/webhooks/whatsapp", content=body, headers=headers)
    assert response.status_code == 403
