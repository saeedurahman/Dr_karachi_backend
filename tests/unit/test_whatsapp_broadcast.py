"""Comprehensive unit and integration tests for WhatsApp broadcast campaign engine (Phase 7)."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.appointment import Appointment, AppointmentStatus
from app.models.branch import Branch
from app.models.doctor import Doctor
from app.models.order import Order, OrderStatus
from app.models.user import User, UserRole
from app.models.whatsapp_broadcast import (
    BroadcastRecipientStatus,
    BroadcastStatus,
    WhatsAppBroadcast,
    WhatsAppBroadcastRecipient,
)
from app.models.whatsapp_consent import WhatsAppConsent
from app.models.whatsapp_message import (
    WhatsAppMessage,
    WhatsAppMessageDirection,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from app.services.whatsapp.broadcast_service import BroadcastService
from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.consent_service import ConsentService
from app.services.whatsapp.exceptions import (
    ConsentBlockedError,
    WhatsAppRateLimitError,
    WhatsAppSpamRateLimitError,
)
from app.services.whatsapp.sender import SafeSender
from app.utils.security import hash_password
from tests.conftest import create_user_helper


@pytest.fixture(autouse=True)
def _wa_broadcast_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_BROADCAST_POLL_SECONDS", 10)
    monkeypatch.setattr(settings, "WHATSAPP_BROADCAST_BATCH_SIZE", 50)
    monkeypatch.setattr(settings, "WHATSAPP_BROADCAST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(settings, "WHATSAPP_DAILY_MARKETING_LIMIT", 250)


async def _create_test_branch(db: AsyncSession, name: str = "Test Branch") -> uuid.UUID:
    branch = Branch(
        name=name,
        city="Karachi",
        address="123 Main St",
        phone="03001234567",
        is_active=True,
    )
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    return branch.id


def _mock_success_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "messaging_product": "whatsapp",
                "contacts": [{"input": "923001234567", "wa_id": "923001234567"}],
                "messages": [{"id": f"wamid.test.{uuid.uuid4().hex[:8]}"}],
            },
        )
    return httpx.MockTransport(handler)


# ── 1. Permission Matrix ───────────────────────────────────────────────────────

async def test_permission_matrix(async_client: httpx.AsyncClient, db_session: AsyncSession):
    """Test permission matrix: super_admin and branch_manager allowed; others forbidden (403)."""
    branch_id = await _create_test_branch(db_session, "Permission Branch")

    # 1. Non-permitted roles receive 403 Forbidden
    for role in (UserRole.patient, UserRole.doctor, UserRole.pharmacy_staff, UserRole.lab_staff):
        user, headers = await create_user_helper(db_session, role=role)
        resp = await async_client.post(
            "/api/v1/admin/whatsapp/broadcasts",
            headers=headers,
            json={
                "name": "Spring Sale",
                "template_key": "promotional_offer",
                "params": {"offer_title": "Sale", "discount_details": "20% off", "expiry_date": "Tomorrow"},
            },
        )
        assert resp.status_code == 403, f"Expected 403 for role {role}, got {resp.status_code}"

    # 2. Super admin succeeds
    super_admin, sa_headers = await create_user_helper(db_session, role=UserRole.super_admin)
    sa_resp = await async_client.post(
        "/api/v1/admin/whatsapp/broadcasts",
        headers=sa_headers,
        json={
            "name": "Super Admin Global Promo",
            "template_key": "promotional_offer",
            "params": {"offer_title": "Grand Opening", "discount_details": "30% off", "expiry_date": "Sunday"},
        },
    )
    assert sa_resp.status_code == 201
    sa_data = sa_resp.json()
    assert sa_data["name"] == "Super Admin Global Promo"
    assert sa_data["branch_id"] is None

    # 3. Branch manager succeeds with branch scoping
    bm, bm_headers = await create_user_helper(
        db_session, role=UserRole.branch_manager, branch_id=branch_id
    )

    bm_resp = await async_client.post(
        "/api/v1/admin/whatsapp/broadcasts",
        headers=bm_headers,
        json={
            "name": "Branch Manager Promo",
            "template_key": "promotional_offer",
            "params": {"offer_title": "Branch Exclusive", "discount_details": "15% off", "expiry_date": "Friday"},
        },
    )
    assert bm_resp.status_code == 201
    bm_data = bm_resp.json()
    assert bm_data["branch_id"] == str(branch_id)


# ── 2. Non-marketing Template Rejected ─────────────────────────────────────────

async def test_non_marketing_template_rejected(async_client: httpx.AsyncClient, db_session: AsyncSession):
    """Attempting to create a broadcast with non-marketing template returns 400 Bad Request."""
    admin, headers = await create_user_helper(db_session, role=UserRole.super_admin)

    resp = await async_client.post(
        "/api/v1/admin/whatsapp/broadcasts",
        headers=headers,
        json={
            "name": "Invalid Template Broadcast",
            "template_key": "appointment_confirmed",  # transactional template!
            "params": {
                "patient_name": "Ali",
                "doctor_name": "Dr. Khan",
                "slot_datetime": "Tomorrow",
                "clinic_branch": "Gulshan",
            },
        },
    )
    assert resp.status_code == 400
    assert "Only marketing templates are allowed" in resp.json()["detail"]


# ── 3. Segmentation Always Excludes Non-Opted-In ───────────────────────────────

async def test_segment_always_excludes_non_opted_in(db_session: AsyncSession):
    """Marketing opt-in is non-overridable: candidates without marketing_opt_in=True are excluded."""
    branch_id = await _create_test_branch(db_session, "Opt-in Segment Branch")

    # User 1: Opted-in
    phone1 = "+923001111111"
    await create_user_helper(db_session, phone=phone1, branch_id=branch_id)
    await ConsentService.set_marketing_opt_in(db_session, phone1, value=True, source="registration")

    # User 2: Opted-out
    phone2 = "+923002222222"
    await create_user_helper(db_session, phone=phone2, branch_id=branch_id)
    await ConsentService.set_marketing_opt_in(db_session, phone2, value=False, source="opt_out")

    # User 3: No consent record
    phone3 = "+923003333333"
    await create_user_helper(db_session, phone=phone3, branch_id=branch_id)

    # Resolve with explicit attempt to bypass marketing_opt_in
    eligible, skipped = await BroadcastService.resolve_segment_recipients(
        db=db_session,
        segment_filters={"marketing_opt_in": False},  # Should be ignored/forced
        branch_id=branch_id,
    )

    eligible_phones = [e["phone"] for e in eligible]
    assert "923001111111" in eligible_phones
    assert "923002222222" not in eligible_phones
    assert "923003333333" not in eligible_phones

    skipped_reasons = {s["phone"]: s["skip_reason"] for s in skipped}
    assert skipped_reasons["923002222222"] == "no_marketing_opt_in"
    assert skipped_reasons["923003333333"] == "no_marketing_opt_in"


# ── 4. Branch Manager Forced to Own Branch ─────────────────────────────────────

async def test_branch_manager_forced_to_own_branch(
    async_client: httpx.AsyncClient, db_session: AsyncSession
):
    """Branch manager cannot broadcast platform-wide or to other branches."""
    branch_a_id = await _create_test_branch(db_session, name="Branch A")
    branch_b_id = await _create_test_branch(db_session, name="Branch B")

    bm, bm_headers = await create_user_helper(
        db_session, role=UserRole.branch_manager, branch_id=branch_a_id
    )

    # BM tries to specify branch_b in segment_filters: forced to branch_a
    resp = await async_client.post(
        "/api/v1/admin/whatsapp/broadcasts",
        headers=bm_headers,
        json={
            "name": "Branch A Promo",
            "template_key": "promotional_offer",
            "params": {"offer_title": "Deal", "discount_details": "10% off", "expiry_date": "Sunday"},
            "segment_filters": {"branch_id": str(branch_b_id)},
        },
    )
    assert resp.status_code == 201
    created_broadcast = resp.json()
    assert created_broadcast["branch_id"] == str(branch_a_id)

    # BM cannot access broadcast belonging to branch_b
    sa, sa_headers = await create_user_helper(db_session, role=UserRole.super_admin)
    b_broadcast = await BroadcastService.create_broadcast(
        db=db_session,
        name="Branch B Broadcast",
        template_key="promotional_offer",
        created_by=sa.id,
        branch_id=branch_b_id,
        params={"offer_title": "B Deal", "discount_details": "5% off", "expiry_date": "Monday"},
    )
    b_broadcast_id = b_broadcast.id

    get_resp = await async_client.get(
        f"/api/v1/admin/whatsapp/broadcasts/{b_broadcast_id}",
        headers=bm_headers,
    )
    assert get_resp.status_code == 403


# ── 5. Preview Counts ──────────────────────────────────────────────────────────

async def test_preview_counts(async_client: httpx.AsyncClient, db_session: AsyncSession):
    """Preview returns accurate total_candidates, eligible_count, and skip reasons."""
    branch_id = await _create_test_branch(db_session, "Preview Branch")
    admin, headers = await create_user_helper(db_session, role=UserRole.super_admin)
    admin_id = admin.id

    # 1 eligible
    p1 = "+923004444441"
    await create_user_helper(db_session, phone=p1, branch_id=branch_id)
    await ConsentService.set_marketing_opt_in(db_session, p1, value=True, source="test")

    # 1 no consent
    await create_user_helper(db_session, phone="+923004444442", branch_id=branch_id)

    # 1 invalid phone
    await create_user_helper(db_session, phone="invalid123", branch_id=branch_id)

    broadcast = await BroadcastService.create_broadcast(
        db=db_session,
        name="Preview Test",
        template_key="promotional_offer",
        created_by=admin_id,
        branch_id=branch_id,
        params={"offer_title": "Flash Sale", "discount_details": "50%", "expiry_date": "Tonight"},
    )
    broadcast_id = broadcast.id

    resp = await async_client.post(
        f"/api/v1/admin/whatsapp/broadcasts/{broadcast_id}/preview",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["eligible_count"] == 1
    assert data["skipped_count"] == 2
    assert "no_marketing_opt_in" in data["skip_reasons"]
    assert "invalid_phone" in data["skip_reasons"]


# ── 6. Batch Sending with Delay Configuration ──────────────────────────────────

async def test_batch_sending_with_delay_config(db_session: AsyncSession, monkeypatch):
    """Worker processes recipients in batches and updates status to sent."""
    branch_id = await _create_test_branch(db_session, "Batch Branch")
    admin, _ = await create_user_helper(db_session, role=UserRole.super_admin)
    admin_id = admin.id

    phones = ["+923005555551", "+923005555552", "+923005555553"]
    for p in phones:
        await create_user_helper(db_session, phone=p, branch_id=branch_id)
        await ConsentService.set_marketing_opt_in(db_session, p, value=True, source="test")

    broadcast = await BroadcastService.create_broadcast(
        db=db_session,
        name="Batch Send Promo",
        template_key="promotional_offer",
        created_by=admin_id,
        branch_id=branch_id,
        params={"offer_title": "Summer Deal", "discount_details": "20%", "expiry_date": "Friday"},
    )
    broadcast_id = broadcast.id
    await BroadcastService.start_broadcast(db_session, broadcast_id)

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=_mock_success_transport()))
    sender = SafeSender(client=client)

    # Process first batch of 2 with small delay
    delay_called = False
    original_sleep = asyncio.sleep

    async def mock_sleep(secs):
        nonlocal delay_called
        delay_called = True
        await original_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    sent = await BroadcastService.process_broadcast_batch(
        db=db_session,
        broadcast_id=broadcast_id,
        sender=sender,
        batch_size=2,
        delay_seconds=0.01,
    )
    assert sent == 2
    assert delay_called is True

    # Check recipient statuses: 2 sent, 1 pending
    recipients = (
        await db_session.execute(
            select(WhatsAppBroadcastRecipient)
            .where(WhatsAppBroadcastRecipient.broadcast_id == broadcast_id)
            .order_by(WhatsAppBroadcastRecipient.created_at.asc())
        )
    ).scalars().all()

    sent_count = sum(1 for r in recipients if r.status == BroadcastRecipientStatus.sent)
    pending_count = sum(1 for r in recipients if r.status == BroadcastRecipientStatus.pending)
    assert sent_count == 2
    assert pending_count == 1

    # Second batch finishes the remaining 1
    sent_second = await BroadcastService.process_broadcast_batch(
        db=db_session,
        broadcast_id=broadcast_id,
        sender=sender,
        batch_size=2,
    )
    assert sent_second == 1

    b_record = await db_session.get(WhatsAppBroadcast, broadcast_id)
    assert b_record.status == BroadcastStatus.completed


# ── 7. Daily Cap Pause and Resume ──────────────────────────────────────────────

async def test_daily_cap_pause_resume(db_session: AsyncSession):
    """Broadcast pauses when daily marketing limit is reached, resumes when limit is reset."""
    branch_id = await _create_test_branch(db_session, "Daily Cap Branch")
    admin, _ = await create_user_helper(db_session, role=UserRole.super_admin)
    admin_id = admin.id

    for i in range(4):
        p = f"+92300666666{i}"
        await create_user_helper(db_session, phone=p, branch_id=branch_id)
        await ConsentService.set_marketing_opt_in(db_session, p, value=True, source="test")

    broadcast = await BroadcastService.create_broadcast(
        db=db_session,
        name="Daily Cap Promo",
        template_key="promotional_offer",
        created_by=admin_id,
        branch_id=branch_id,
        params={"offer_title": "Daily Cap Deal", "discount_details": "10%", "expiry_date": "Tomorrow"},
    )
    broadcast_id = broadcast.id
    await BroadcastService.start_broadcast(db_session, broadcast_id)

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=_mock_success_transport()))
    sender = SafeSender(client=client)

    # Calculate current daily marketing sent count
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    initial_sent = (
        await db_session.scalar(
            select(func.count())
            .select_from(WhatsAppMessage)
            .where(
                WhatsAppMessage.direction == WhatsAppMessageDirection.outbound,
                WhatsAppMessage.related_entity_type == "broadcast",
                WhatsAppMessage.created_at >= today_start,
            )
        )
    ) or 0

    daily_cap = initial_sent + 2

    # Process with limit = initial_sent + 2: sends 2, then hits cap
    sent_today = await BroadcastService.process_broadcast_batch(
        db=db_session,
        broadcast_id=broadcast_id,
        sender=sender,
        batch_size=10,
        daily_limit=daily_cap,
    )
    assert sent_today == 2

    # Another tick today should send 0 because cap is reached
    sent_capped = await BroadcastService.process_broadcast_batch(
        db=db_session,
        broadcast_id=broadcast_id,
        sender=sender,
        batch_size=10,
        daily_limit=daily_cap,
    )
    assert sent_capped == 0

    b_sending = await db_session.get(WhatsAppBroadcast, broadcast_id)
    assert b_sending.status == BroadcastStatus.sending  # Still sending (paused)

    # Next day / quota reset: limit is raised/reset, resumes and finishes remaining 2
    sent_resumed = await BroadcastService.process_broadcast_batch(
        db=db_session,
        broadcast_id=broadcast_id,
        sender=sender,
        batch_size=10,
        daily_limit=daily_cap + 10,
    )
    assert sent_resumed == 2

    b_completed = await db_session.get(WhatsAppBroadcast, broadcast_id)
    assert b_completed.status == BroadcastStatus.completed


# ── 8. Opt-out Mid-broadcast is Respected ───────────────────────────────────────

async def test_opt_out_mid_broadcast_is_respected(db_session: AsyncSession):
    """Recipient who revokes consent while broadcast is running is skipped right before send."""
    branch_id = await _create_test_branch(db_session, "Opt-out Branch")
    admin, _ = await create_user_helper(db_session, role=UserRole.super_admin)
    admin_id = admin.id

    p = "+923007777777"
    await create_user_helper(db_session, phone=p, branch_id=branch_id)
    await ConsentService.set_marketing_opt_in(db_session, p, value=True, source="test")

    broadcast = await BroadcastService.create_broadcast(
        db=db_session,
        name="Mid-broadcast Opt-out Promo",
        template_key="promotional_offer",
        created_by=admin_id,
        branch_id=branch_id,
        params={"offer_title": "Opt-out Deal", "discount_details": "10%", "expiry_date": "Sunday"},
    )
    broadcast_id = broadcast.id
    await BroadcastService.start_broadcast(db_session, broadcast_id)

    # User opts out BEFORE worker batch executes
    await ConsentService.set_marketing_opt_in(db_session, p, value=False, source="whatsapp_keyword")

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=_mock_success_transport()))
    sender = SafeSender(client=client)

    sent = await BroadcastService.process_broadcast_batch(
        db=db_session, broadcast_id=broadcast_id, sender=sender
    )
    assert sent == 0

    # Recipient status must be skipped with skip_reason="opted_out_before_send"
    stmt = select(WhatsAppBroadcastRecipient).where(
        WhatsAppBroadcastRecipient.broadcast_id == broadcast_id,
        WhatsAppBroadcastRecipient.phone == "923007777777",
    )
    recip = (await db_session.execute(stmt)).scalar_one()
    assert recip.status == BroadcastRecipientStatus.skipped
    assert recip.skip_reason == "opted_out_before_send"


# ── 9. Cancel Stops Sending Immediately ────────────────────────────────────────

async def test_cancel_stops_sending(db_session: AsyncSession):
    """Cancelling a broadcast immediately stops worker dispatch."""
    branch_id = await _create_test_branch(db_session, "Cancel Branch")
    admin, _ = await create_user_helper(db_session, role=UserRole.super_admin)
    admin_id = admin.id

    for i in range(2):
        p = f"+92300888888{i}"
        await create_user_helper(db_session, phone=p, branch_id=branch_id)
        await ConsentService.set_marketing_opt_in(db_session, p, value=True, source="test")

    broadcast = await BroadcastService.create_broadcast(
        db=db_session,
        name="Cancel Test Promo",
        template_key="promotional_offer",
        created_by=admin_id,
        branch_id=branch_id,
        params={"offer_title": "Cancel Deal", "discount_details": "5%", "expiry_date": "Friday"},
    )
    broadcast_id = broadcast.id
    await BroadcastService.start_broadcast(db_session, broadcast_id)

    # Cancel the broadcast
    await BroadcastService.cancel_broadcast(db_session, broadcast_id)

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=_mock_success_transport()))
    sender = SafeSender(client=client)

    sent = await BroadcastService.process_broadcast_batch(
        db=db_session, broadcast_id=broadcast_id, sender=sender
    )
    assert sent == 0

    # Pending recipients remain untouched
    recips = (
        await db_session.execute(
            select(WhatsAppBroadcastRecipient).where(
                WhatsAppBroadcastRecipient.broadcast_id == broadcast_id
            )
        )
    ).scalars().all()
    for r in recips:
        assert r.status != BroadcastRecipientStatus.sent


# ── 10. Duplicate Start Idempotency ────────────────────────────────────────────

async def test_duplicate_start_does_not_double_send(db_session: AsyncSession):
    """Starting an already-running broadcast does not duplicate recipients."""
    branch_id = await _create_test_branch(db_session, "Idempotent Branch")
    admin, _ = await create_user_helper(db_session, role=UserRole.super_admin)
    admin_id = admin.id

    p = "+923009999991"
    await create_user_helper(db_session, phone=p, branch_id=branch_id)
    await ConsentService.set_marketing_opt_in(db_session, p, value=True, source="test")

    broadcast = await BroadcastService.create_broadcast(
        db=db_session,
        name="Idempotent Start Promo",
        template_key="promotional_offer",
        created_by=admin_id,
        branch_id=branch_id,
        params={"offer_title": "Idempotent Deal", "discount_details": "10%", "expiry_date": "Saturday"},
    )
    broadcast_id = broadcast.id

    # Start 1
    await BroadcastService.start_broadcast(db_session, broadcast_id)
    count1 = await db_session.scalar(
        select(func.count()).where(
            WhatsAppBroadcastRecipient.broadcast_id == broadcast_id
        )
    )

    # Start 2 (duplicate)
    await BroadcastService.start_broadcast(db_session, broadcast_id)
    count2 = await db_session.scalar(
        select(func.count()).where(
            WhatsAppBroadcastRecipient.broadcast_id == broadcast_id
        )
    )

    # Verify recipient count is exactly 1 and does not double
    assert count1 == 1
    assert count2 == 1


# ── 11. Rate-Limit / Quality Error Pauses Broadcast ────────────────────────────

async def test_rate_limit_error_pauses_broadcast(db_session: AsyncSession):
    """WhatsApp error 131048 or 130429 pauses broadcast dispatch without hammering API."""
    branch_id = await _create_test_branch(db_session, "RateLimit Branch")
    admin, _ = await create_user_helper(db_session, role=UserRole.super_admin)
    admin_id = admin.id

    p = "+923009999992"
    await create_user_helper(db_session, phone=p, branch_id=branch_id)
    await ConsentService.set_marketing_opt_in(db_session, p, value=True, source="test")

    broadcast = await BroadcastService.create_broadcast(
        db=db_session,
        name="Rate Limit Promo",
        template_key="promotional_offer",
        created_by=admin_id,
        branch_id=branch_id,
        params={"offer_title": "Rate Limit Deal", "discount_details": "15%", "expiry_date": "Sunday"},
    )
    broadcast_id = broadcast.id
    await BroadcastService.start_broadcast(db_session, broadcast_id)

    # Mock rate limit response (131048 Spam Rate Limit)
    def rate_limit_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Message failed to send because of spam rate limits.",
                    "type": "OAuthException",
                    "code": 131048,
                    "error_subcode": 0,
                    "fbtrace_id": "test_fb_trace",
                }
            },
        )

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(rate_limit_handler)))
    sender = SafeSender(client=client)

    sent = await BroadcastService.process_broadcast_batch(
        db=db_session, broadcast_id=broadcast_id, sender=sender
    )
    assert sent == 0

    # Recipient must NOT be marked permanently failed; left pending so it can resume
    recip = await db_session.scalar(
        select(WhatsAppBroadcastRecipient).where(
            WhatsAppBroadcastRecipient.broadcast_id == broadcast_id
        )
    )
    assert recip.status == BroadcastRecipientStatus.pending


# ── 12. Stats Endpoint Correctness ─────────────────────────────────────────────

async def test_stats_endpoint_correctness(async_client: httpx.AsyncClient, db_session: AsyncSession):
    """Stats endpoint computes pending, sent, delivered, read, failed, and skipped accurately."""
    branch_id = await _create_test_branch(db_session, "Stats Branch")
    admin, headers = await create_user_helper(db_session, role=UserRole.super_admin)
    admin_id = admin.id

    broadcast = await BroadcastService.create_broadcast(
        db=db_session,
        name="Stats Promo",
        template_key="promotional_offer",
        created_by=admin_id,
        branch_id=branch_id,
        params={"offer_title": "Stats Deal", "discount_details": "20%", "expiry_date": "Sunday"},
    )
    broadcast_id = broadcast.id

    # 1. Pending recipient
    r_pending = WhatsAppBroadcastRecipient(
        broadcast_id=broadcast_id,
        phone="923000000001",
        status=BroadcastRecipientStatus.pending,
    )

    # 2. Skipped recipient
    r_skipped = WhatsAppBroadcastRecipient(
        broadcast_id=broadcast_id,
        phone="923000000002",
        status=BroadcastRecipientStatus.skipped,
        skip_reason="invalid_phone",
    )

    # 3. Sent (not delivered yet)
    msg_sent = WhatsAppMessage(
        recipient_phone="923000000003",
        direction=WhatsAppMessageDirection.outbound,
        message_type=WhatsAppMessageType.template,
        status=WhatsAppMessageStatus.sent,
    )
    db_session.add(msg_sent)
    await db_session.flush()

    r_sent = WhatsAppBroadcastRecipient(
        broadcast_id=broadcast_id,
        phone="923000000003",
        status=BroadcastRecipientStatus.sent,
        message_id=msg_sent.id,
    )

    # 4. Delivered
    msg_delivered = WhatsAppMessage(
        recipient_phone="923000000004",
        direction=WhatsAppMessageDirection.outbound,
        message_type=WhatsAppMessageType.template,
        status=WhatsAppMessageStatus.delivered,
    )
    db_session.add(msg_delivered)
    await db_session.flush()

    r_delivered = WhatsAppBroadcastRecipient(
        broadcast_id=broadcast_id,
        phone="923000000004",
        status=BroadcastRecipientStatus.sent,
        message_id=msg_delivered.id,
    )

    # 5. Read
    msg_read = WhatsAppMessage(
        recipient_phone="923000000005",
        direction=WhatsAppMessageDirection.outbound,
        message_type=WhatsAppMessageType.template,
        status=WhatsAppMessageStatus.read,
    )
    db_session.add(msg_read)
    await db_session.flush()

    r_read = WhatsAppBroadcastRecipient(
        broadcast_id=broadcast_id,
        phone="923000000005",
        status=BroadcastRecipientStatus.sent,
        message_id=msg_read.id,
    )

    # 6. Failed
    r_failed = WhatsAppBroadcastRecipient(
        broadcast_id=broadcast_id,
        phone="923000000006",
        status=BroadcastRecipientStatus.failed,
        skip_reason="Meta 131026 Undeliverable",
    )

    db_session.add_all([r_pending, r_skipped, r_sent, r_delivered, r_read, r_failed])
    await db_session.commit()

    resp = await async_client.get(
        f"/api/v1/admin/whatsapp/broadcasts/{broadcast_id}",
        headers=headers,
    )
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    assert stats["total"] == 6
    assert stats["pending"] == 1
    assert stats["skipped"] == 1
    assert stats["sent"] == 1
    assert stats["delivered"] == 1
    assert stats["read"] == 1
    assert stats["failed"] == 1
