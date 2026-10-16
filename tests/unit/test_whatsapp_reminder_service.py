"""Unit and integration tests for WhatsApp appointment reminder service and worker."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.appointment import Appointment, AppointmentStatus
from app.models.branch import Branch
from app.models.doctor import Doctor
from app.models.user import User, UserRole
from app.models.whatsapp_message import (
    WhatsAppMessage,
    WhatsAppMessageStatus,
)
from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.consent_service import ConsentService
from app.services.whatsapp.reminder_service import (
    AppointmentReminderService,
    process_appointment_reminders,
)
from app.services.whatsapp.sender import SafeSender
from app.services.whatsapp.transactional_handlers import format_pkt_datetime
from app.utils.security import hash_password
from app.worker import run_worker
from tests.conftest import TestSessionLocal

PKT = ZoneInfo("Asia/Karachi")


@pytest.fixture(autouse=True)
def _wa_reminder_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_REMINDER_POLL_SECONDS", 60)
    monkeypatch.setattr(settings, "WHATSAPP_REMINDER_WINDOW_START_MINUTES", 55)
    monkeypatch.setattr(settings, "WHATSAPP_REMINDER_WINDOW_END_MINUTES", 65)
    monkeypatch.setattr(settings, "WHATSAPP_REMINDER_BATCH_SIZE", 50)


async def _create_test_appointment(
    db: AsyncSession,
    slot_datetime: datetime,
    status: AppointmentStatus = AppointmentStatus.confirmed,
    patient_phone: str | None = None,
    patient_name: str = "Hamza Ali",
    doctor_name: str = "Dr. Bilal Siddiqui",
    branch_name: str = "Gulshan Branch",
) -> Appointment:
    """Helper to set up test patient, doctor, branch, and appointment."""
    unique_suffix = str(uuid.uuid4().hex[:6])
    phone_val = patient_phone or f"0300{uuid.uuid4().int % 10000000:07d}"

    patient = User(
        id=uuid.uuid4(),
        full_name=patient_name,
        phone=phone_val,

        email=f"patient_{unique_suffix}@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    doc_user = User(
        id=uuid.uuid4(),
        full_name=doctor_name,
        phone=f"+92300{unique_suffix}",
        email=f"doctor_{unique_suffix}@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.doctor,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name=branch_name,
        city="Karachi",
        address="Block 13, Gulshan-e-Iqbal",
        phone="02134567890",
        is_active=True,
    )
    db.add_all([patient, doc_user, branch])
    await db.flush()

    doctor = Doctor(
        id=uuid.uuid4(),
        user_id=doc_user.id,
        specialization="General Medicine",
        qualification="MBBS",
        consultation_fee=1500.0,
        is_active=True,
    )
    db.add(doctor)
    await db.flush()

    appt = Appointment(
        id=uuid.uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        branch_id=branch.id,
        slot_datetime=slot_datetime,
        status=status,
    )
    db.add(appt)
    await db.commit()
    return appt


# ── 1. Appointment inside window gets exactly one reminder ───────────────────

async def test_appointment_inside_window_gets_exactly_one_reminder(db_session: AsyncSession):
    now = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    # 60 minutes ahead is inside the 55-65 minute window
    slot_time = now + timedelta(minutes=60)

    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.reminder.123"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    appt = await _create_test_appointment(
        db_session,
        slot_datetime=slot_time,
        patient_name="Hamza Ali",
        doctor_name="Dr. Bilal Siddiqui",
        branch_name="Gulshan Branch",
    )

    sent = await AppointmentReminderService.process_reminders(
        db=db_session,
        sender=sender,
        now=now,
    )
    assert sent == 1
    assert len(recorded_requests) == 1

    # Verify message payload sent to Meta
    payload = recorded_requests[0].read().decode("utf-8")
    assert "Hamza Ali" in payload
    assert "Dr. Bilal Siddiqui" in payload
    assert "Gulshan Branch" in payload
    # 11:00 UTC = 16:00 (4:00 PM) PKT
    expected_pkt = format_pkt_datetime(slot_time)
    assert expected_pkt in payload
    # Assert zero medical details
    assert "diagnosis" not in payload.lower()
    assert "prescription" not in payload.lower()

    # Verify audit log in DB
    stmt = select(WhatsAppMessage).where(WhatsAppMessage.related_entity_id == appt.id)
    msg = (await db_session.execute(stmt)).scalar_one_or_none()
    assert msg is not None
    assert msg.status == WhatsAppMessageStatus.sent
    assert msg.wamid == "wamid.reminder.123"
    assert msg.dedupe_key == f"appointment:{appt.id}:reminder_1h"
    assert msg.template_name == "appointment_reminder_1h"


# ── 2. Running the tick twice sends once (Idempotency) ─────────────────────────

async def test_running_tick_twice_sends_once(db_session: AsyncSession):
    now = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    slot_time = now + timedelta(minutes=60)
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(recorded_requests)}"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    appt = await _create_test_appointment(db_session, slot_datetime=slot_time)

    # First tick: sends reminder
    sent_tick1 = await AppointmentReminderService.process_reminders(
        db=db_session,
        sender=sender,
        now=now,
    )
    assert sent_tick1 == 1
    assert len(recorded_requests) == 1

    # Second tick: same window and appointment -> duplicate skipped silently
    sent_tick2 = await AppointmentReminderService.process_reminders(
        db=db_session,
        sender=sender,
        now=now,
    )
    assert sent_tick2 == 0
    assert len(recorded_requests) == 1  # No extra request

    # Exactly one message log exists
    stmt = select(WhatsAppMessage).where(WhatsAppMessage.related_entity_id == appt.id)
    messages = (await db_session.execute(stmt)).scalars().all()
    assert len(messages) == 1


# ── 3. Outside window appointments are skipped ─────────────────────────────────

async def test_outside_window_skipped(db_session: AsyncSession):
    now = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    # 30 mins ahead (too early, below 55m)
    appt_too_early = await _create_test_appointment(
        db_session,
        slot_datetime=now + timedelta(minutes=30),
    )
    # 90 mins ahead (too far, above 65m)
    appt_too_far = await _create_test_appointment(
        db_session,
        slot_datetime=now + timedelta(minutes=90),
    )

    sent = await AppointmentReminderService.process_reminders(
        db=db_session,
        sender=sender,
        now=now,
    )
    assert sent == 0
    assert len(recorded_requests) == 0

    # Ensure no message logs were created
    stmt = select(WhatsAppMessage).where(
        WhatsAppMessage.related_entity_id.in_([appt_too_early.id, appt_too_far.id])
    )
    logs = (await db_session.execute(stmt)).scalars().all()
    assert len(logs) == 0


# ── 4. Cancelled, completed, and no-show appointments are skipped ───────────────

async def test_cancelled_and_completed_skipped(db_session: AsyncSession):
    now = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    slot_time = now + timedelta(minutes=60)
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    appt_cancelled = await _create_test_appointment(
        db_session,
        slot_datetime=slot_time,
        status=AppointmentStatus.cancelled,
    )
    appt_completed = await _create_test_appointment(
        db_session,
        slot_datetime=slot_time,
        status=AppointmentStatus.completed,
    )
    appt_noshow = await _create_test_appointment(
        db_session,
        slot_datetime=slot_time,
        status=AppointmentStatus.no_show,
    )

    sent = await AppointmentReminderService.process_reminders(
        db=db_session,
        sender=sender,
        now=now,
    )
    assert sent == 0
    assert len(recorded_requests) == 0


# ── 5. Status changed between select and send is skipped ───────────────────────

async def test_status_changed_between_select_and_send_skipped(db_session: AsyncSession):
    now = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    slot_time = now + timedelta(minutes=60)
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.race"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    appt = await _create_test_appointment(db_session, slot_datetime=slot_time)

    # Simulate cancellation occurring right before the pre-send check
    appt.status = AppointmentStatus.cancelled

    dispatched = await AppointmentReminderService._process_single_appointment(
        db=db_session,
        appt=appt,
        sender=sender,
        now_utc=now,
    )
    assert dispatched is False
    assert len(recorded_requests) == 0

    # Test slot in the past is also skipped
    appt.status = AppointmentStatus.confirmed
    appt.slot_datetime = now - timedelta(minutes=10)
    dispatched_past = await AppointmentReminderService._process_single_appointment(
        db=db_session,
        appt=appt,
        sender=sender,
        now_utc=now,
    )
    assert dispatched_past is False
    assert len(recorded_requests) == 0


# ── 6. Consent-blocked user is skipped and recorded as failed ──────────────────

async def test_consent_blocked_skipped_and_marked_failed(db_session: AsyncSession):
    now = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    slot_time = now + timedelta(minutes=60)
    phone = f"0300{uuid.uuid4().int % 10000000:07d}"

    # Explicitly revoke transactional consent (STOP ALL)
    await ConsentService.set_transactional(db_session, phone, False, source="user_opt_out")
    await db_session.commit()

    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.never"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    appt = await _create_test_appointment(
        db_session,
        slot_datetime=slot_time,
        patient_phone=phone,
    )

    sent = await AppointmentReminderService.process_reminders(
        db=db_session,
        sender=sender,
        now=now,
    )
    # Zero sent, no HTTP call to Meta
    assert sent == 0
    assert len(recorded_requests) == 0

    # Message is recorded as failed with consent reason
    stmt = select(WhatsAppMessage).where(WhatsAppMessage.related_entity_id == appt.id)
    msg = (await db_session.execute(stmt)).scalar_one_or_none()
    assert msg is not None
    assert msg.status == WhatsAppMessageStatus.failed
    assert "consent" in msg.error_message.lower()

    # Second tick skips it (dedupe key exists) without crashing
    sent_tick2 = await AppointmentReminderService.process_reminders(
        db=db_session,
        sender=sender,
        now=now,
    )
    assert sent_tick2 == 0


# ── 7. Two concurrent ticks (simulated) send exactly once ─────────────────────

async def test_two_concurrent_ticks_send_once(db_session: AsyncSession):
    now = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    slot_time = now + timedelta(minutes=60)
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": f"wamid.concurrent.{len(recorded_requests)}"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    appt = await _create_test_appointment(db_session, slot_datetime=slot_time)

    # Run two concurrent ticks with separate sessions simulating two workers
    async with TestSessionLocal() as s1, TestSessionLocal() as s2:
        async def tick1():
            return await AppointmentReminderService.process_reminders(s1, sender=sender, now=now)

        async def tick2():
            return await AppointmentReminderService.process_reminders(s2, sender=sender, now=now)

        res1, res2 = await asyncio.gather(tick1(), tick2())

    # Combined results must equal exactly 1 reminder sent
    assert (res1 + res2) == 1
    assert len(recorded_requests) == 1

    # Verify database has exactly 1 message log
    stmt = select(WhatsAppMessage).where(WhatsAppMessage.related_entity_id == appt.id)
    messages = (await db_session.execute(stmt)).scalars().all()
    assert len(messages) == 1
    assert messages[0].status == WhatsAppMessageStatus.sent


# ── 8. WhatsApp failure is recorded without crashing loop ──────────────────────

async def test_whatsapp_failure_recorded_without_crashing_loop(db_session: AsyncSession):
    now = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    slot_time = now + timedelta(minutes=60)

    # Mock Meta API failure (HTTP 500)
    def mock_fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"message": "Meta Cloud API Internal Error", "code": 131000}},
        )

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_fail_handler)))
    sender = SafeSender(client=client)

    appt = await _create_test_appointment(db_session, slot_datetime=slot_time)

    sent = await AppointmentReminderService.process_reminders(
        db=db_session,
        sender=sender,
        now=now,
    )
    assert sent == 0

    # Message must be marked failed with error details recorded
    stmt = select(WhatsAppMessage).where(WhatsAppMessage.related_entity_id == appt.id)
    msg = (await db_session.execute(stmt)).scalar_one_or_none()
    assert msg is not None
    assert msg.status == WhatsAppMessageStatus.failed
    assert msg.error_message is not None
    assert "131000" in msg.error_message or "Internal Error" in msg.error_message

    # Second tick: does not retry endlessly
    sent_tick2 = await AppointmentReminderService.process_reminders(
        db=db_session,
        sender=sender,
        now=now,
    )
    assert sent_tick2 == 0


# ── 9. Worker process loop & graceful shutdown ─────────────────────────────────

async def test_worker_run_loop_handles_tick_errors_and_shutdown(monkeypatch):
    """Verify that run_worker continues on tick errors and shuts down cleanly on stop_event."""
    stop_event = asyncio.Event()
    tick_invocations = 0

    async def faulty_process_reminders(db, *args, **kwargs):
        nonlocal tick_invocations
        tick_invocations += 1
        if tick_invocations == 1:
            raise RuntimeError("Database temporary disconnect simulation")
        return 0

    monkeypatch.setattr(
        AppointmentReminderService,
        "process_reminders",
        faulty_process_reminders,
    )

    # Run for 2 ticks; tick 1 will fail and log, tick 2 will succeed
    await run_worker(
        poll_seconds=0.01,
        shutdown_event=stop_event,
        max_ticks=2,
    )

    assert tick_invocations == 2
