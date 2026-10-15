"""Unit and integration tests for WhatsApp transactional event handlers."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.appointment import Appointment, AppointmentStatus
from app.models.branch import Branch
from app.models.doctor import DayOfWeek, Doctor, DoctorAvailability
from app.models.lab_report import LabReport, ReportFileType
from app.models.lab_test import LabTest
from app.models.order import Order, OrderStatus, PaymentMethod
from app.models.user import User, UserRole
from app.models.whatsapp_message import WhatsAppMessage, WhatsAppMessageStatus
from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.consent_service import ConsentService
from app.services.whatsapp.sender import SafeSender
from app.services.whatsapp.transactional_handlers import (
    dispatch_transactional_message,
    format_pkt_datetime,
    handle_appointment_cancelled,
    handle_appointment_confirmed,
    handle_lab_report_ready,
    handle_order_placed,
    handle_order_status_changed,
)
from app.utils.security import hash_password

SECRET = "test-secret-trans-handlers-32chars"


@pytest.fixture(autouse=True)
def _wa_trans_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "REPORT_LINK_SECRET", SECRET)
    monkeypatch.setattr(settings, "API_PUBLIC_URL", "http://test")

    # Mock WhatsAppClient default transport to prevent any real network requests
    orig_init = WhatsAppClient.__init__

    def mock_init(self, *args, **kwargs):
        if "http_client" not in kwargs or kwargs["http_client"] is None:
            def default_handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"messages": [{"id": "wamid.default.mock"}]})
            kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(default_handler))
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(WhatsAppClient, "__init__", mock_init)


# ── 1. PKT Datetime Formatting ───────────────────────────────────────────────

def test_pkt_datetime_formatting():
    # 05:30 UTC is 10:30 PKT (UTC+5)
    utc_dt = datetime(2026, 10, 18, 5, 30, tzinfo=UTC)
    formatted = format_pkt_datetime(utc_dt)
    assert formatted == "18 Oct 2026, 10:30 AM"

    # Naive datetime should be treated as UTC
    naive_dt = datetime(2026, 9, 7, 4, 15)
    formatted_naive = format_pkt_datetime(naive_dt)
    assert formatted_naive == "7 Sep 2026, 09:15 AM"


# ── 2. Appointment Confirmed & Cancelled Handlers ────────────────────────────

async def test_appointment_confirmed_event_triggers_exactly_one_message(db_session: AsyncSession):
    phone = "03001230001"
    recorded_requests = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.appt.conf.1"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    # Setup patient, doctor, branch, appointment
    patient = User(
        id=uuid.uuid4(),
        full_name="Ahmad Raza",
        phone=phone,
        email="ahmad@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    user_doc = User(
        id=uuid.uuid4(),
        full_name="Dr. Sarah Tariq",
        phone="+923001112222",
        email="doc_sarah@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.doctor,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="Clifton Clinic",
        city="Karachi",
        address="Block 5, Clifton",
        phone="02135800000",
        is_active=True,
    )
    db_session.add_all([patient, user_doc, branch])
    await db_session.flush()

    doctor = Doctor(
        id=uuid.uuid4(),
        user_id=user_doc.id,
        specialization="Cardiology",
        qualification="MBBS, FCPS",
        consultation_fee=2000.0,
        is_active=True,
    )
    db_session.add(doctor)
    await db_session.flush()

    slot_time = datetime(2026, 10, 18, 5, 30, tzinfo=UTC)
    appointment = Appointment(
        id=uuid.uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        branch_id=branch.id,
        slot_datetime=slot_time,
        status=AppointmentStatus.confirmed,
    )
    db_session.add(appointment)
    await db_session.commit()

    msg = await handle_appointment_confirmed(db_session, appointment.id, sender=sender)
    assert msg is not None
    assert msg.status == WhatsAppMessageStatus.sent
    assert msg.wamid == "wamid.appt.conf.1"
    assert msg.dedupe_key == f"appointment:{appointment.id}:confirmed"
    assert len(recorded_requests) == 1

    # Verify parameters in recorded request payload
    sent_payload = recorded_requests[0].read().decode("utf-8")
    assert "Ahmad Raza" in sent_payload
    assert "Dr. Sarah Tariq" in sent_payload
    assert "18 Oct 2026, 10:30 AM" in sent_payload
    assert "Clifton Clinic" in sent_payload


async def test_appointment_cancelled_event(db_session: AsyncSession):
    phone = "03001230002"
    recorded_requests = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.appt.canc.1"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    patient = User(
        id=uuid.uuid4(),
        full_name="Babar Azam",
        phone=phone,
        email="babar@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    user_doc = User(
        id=uuid.uuid4(),
        full_name="Dr. Tariq Khan",
        phone="+923001113333",
        email="doc_tariq@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.doctor,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="DHA Clinic",
        city="Karachi",
        address="Phase 6, DHA",
        phone="02135800001",
        is_active=True,
    )
    db_session.add_all([patient, user_doc, branch])
    await db_session.flush()

    doctor = Doctor(
        id=uuid.uuid4(),
        user_id=user_doc.id,
        specialization="Dermatology",
        qualification="MBBS, MCPS",
        consultation_fee=1500.0,
        is_active=True,
    )
    db_session.add(doctor)
    await db_session.flush()

    slot_time = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)
    appointment = Appointment(
        id=uuid.uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        branch_id=branch.id,
        slot_datetime=slot_time,
        status=AppointmentStatus.cancelled,
        cancellation_reason="Doctor emergency",
    )
    db_session.add(appointment)
    await db_session.commit()

    msg = await handle_appointment_cancelled(db_session, appointment.id, sender=sender)
    assert msg is not None
    assert msg.wamid == "wamid.appt.canc.1"
    assert msg.dedupe_key == f"appointment:{appointment.id}:cancelled"

    sent_payload = recorded_requests[0].read().decode("utf-8")
    assert "Babar Azam" in sent_payload
    assert "Doctor emergency" in sent_payload


# ── 3. Order Placed & Status Changed Handlers ───────────────────────────────

async def test_order_placed_event(db_session: AsyncSession):
    phone = "03001230003"
    recorded = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ord.placed.1"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    patient = User(
        id=uuid.uuid4(),
        full_name="Fatima Ali",
        phone=phone,
        email="fatima@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="Main Pharmacy",
        city="Karachi",
        address="Saddar",
        phone="02132220000",
        is_active=True,
    )
    db_session.add_all([patient, branch])
    await db_session.flush()

    order = Order(
        id=uuid.uuid4(),
        patient_id=patient.id,
        branch_id=branch.id,
        status=OrderStatus.pending,
        subtotal=Decimal("1500.00"),
        grand_total=Decimal("1650.00"),
        payment_method=PaymentMethod.cod,
        delivery_address="House 12, Street 3, Karachi",
    )
    db_session.add(order)
    await db_session.commit()

    msg = await handle_order_placed(db_session, order.id, sender=sender)
    assert msg is not None
    assert msg.wamid == "wamid.ord.placed.1"
    assert msg.dedupe_key == f"order:{order.id}:placed"

    payload = recorded[0].read().decode("utf-8")
    assert "Fatima Ali" in payload
    assert f"ORD-{str(order.id)[:8].upper()}" in payload
    assert "1650.00" in payload


async def test_order_status_changed_event(db_session: AsyncSession):
    phone = "03001230004"
    recorded = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ord.stat.1"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    patient = User(
        id=uuid.uuid4(),
        full_name="Zubair Khan",
        phone=phone,
        email="zubair@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="Main Pharmacy",
        city="Karachi",
        address="Saddar",
        phone="02132220000",
        is_active=True,
    )
    db_session.add_all([patient, branch])
    await db_session.flush()

    order = Order(
        id=uuid.uuid4(),
        patient_id=patient.id,
        branch_id=branch.id,
        status=OrderStatus.out_for_delivery,
        subtotal=Decimal("800.00"),
        grand_total=Decimal("950.00"),
        payment_method=PaymentMethod.cod,
        delivery_address="Flat 4B, Gulshan, Karachi",
    )
    db_session.add(order)
    await db_session.commit()

    msg = await handle_order_status_changed(
        db_session,
        order.id,
        new_status="out_for_delivery",
        sender=sender,
    )
    assert msg is not None
    assert msg.dedupe_key == f"order:{order.id}:status:out_for_delivery"

    payload = recorded[0].read().decode("utf-8")
    assert "Zubair Khan" in payload
    assert f"ORD-{str(order.id)[:8].upper()}" in payload
    assert "Out for delivery" in payload


# ── 4. Lab Report Ready & Medical Privacy Guard ──────────────────────────────

async def test_lab_report_ready_no_medical_details(db_session: AsyncSession):
    phone = "03001230005"
    recorded = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.rep.ready.1"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    patient = User(
        id=uuid.uuid4(),
        full_name="Maryam Tariq",
        phone=phone,
        email="maryam@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    # Test has a highly sensitive medical test name
    sensitive_test = LabTest(
        id=uuid.uuid4(),
        name="Highly Sensitive Oncology Marker Panel",
        code="ONCO-999",
        price=5000.0,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="Gulshan Diagnostic Center",
        city="Karachi",
        address="Gulshan Block 13",
        phone="02134900000",
        is_active=True,
    )
    db_session.add_all([patient, sensitive_test, branch])
    await db_session.flush()

    report = LabReport(
        id=uuid.uuid4(),
        patient_id=patient.id,
        test_id=sensitive_test.id,
        file_path="reports/test/onco.pdf",
        file_type=ReportFileType.pdf,
        file_name="onco.pdf",
        is_visible=True,
        uploaded_by=patient.id,
    )
    db_session.add(report)
    await db_session.commit()

    msg = await handle_lab_report_ready(db_session, report.id, sender=sender)
    assert msg is not None
    assert msg.wamid == "wamid.rep.ready.1"
    assert msg.dedupe_key == f"lab_report:{report.id}:ready"

    # CRITICAL PRIVACY ASSERTION:
    # Test name, test code, or file name must NOT appear in the outbound WhatsApp payload!
    sent_payload = recorded[0].read().decode("utf-8")
    assert "Highly Sensitive" not in sent_payload
    assert "Oncology" not in sent_payload
    assert "ONCO-999" not in sent_payload
    assert "onco.pdf" not in sent_payload

    # Valid parameters must appear
    assert "Maryam Tariq" in sent_payload
    assert "/api/v1/lab/reports/access/" in sent_payload


# ── 5. Deduplication, Consent Blocked & Fault Tolerance ──────────────────────

async def test_duplicate_event_is_a_no_op(db_session: AsyncSession):
    phone = "03001230006"
    recorded = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"messages": [{"id": f"wamid.dup.{uuid.uuid4().hex}"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    patient = User(
        id=uuid.uuid4(),
        full_name="Duplicate Test User",
        phone=phone,
        email="dup@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="Test Branch",
        city="Karachi",
        address="Test",
        phone="02130000000",
        is_active=True,
    )
    db_session.add_all([patient, branch])
    await db_session.flush()

    order = Order(
        id=uuid.uuid4(),
        patient_id=patient.id,
        branch_id=branch.id,
        status=OrderStatus.pending,
        subtotal=Decimal("500.00"),
        grand_total=Decimal("650.00"),
        payment_method=PaymentMethod.cod,
        delivery_address="Address",
    )
    db_session.add(order)
    await db_session.commit()

    # First dispatch triggers send
    msg1 = await handle_order_placed(db_session, order.id, sender=sender)
    assert msg1 is not None
    assert len(recorded) == 1

    # Second dispatch with identical event is a no-op
    msg2 = await handle_order_placed(db_session, order.id, sender=sender)
    assert msg2 is None
    # No additional outbound request made!
    assert len(recorded) == 1


async def test_consent_blocked_skip(db_session: AsyncSession):
    phone = "03001230007"
    recorded = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.never"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    # Opt OUT of transactional messages explicitly
    await ConsentService.set_transactional(db_session, phone, value=False, source="user_optout")
    await db_session.commit()

    patient = User(
        id=uuid.uuid4(),
        full_name="Blocked Patient",
        phone=phone,
        email="blocked@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="Branch",
        city="Karachi",
        address="Address",
        phone="02130000000",
        is_active=True,
    )
    db_session.add_all([patient, branch])
    await db_session.flush()

    order = Order(
        id=uuid.uuid4(),
        patient_id=patient.id,
        branch_id=branch.id,
        status=OrderStatus.pending,
        subtotal=Decimal("500.00"),
        grand_total=Decimal("650.00"),
        payment_method=PaymentMethod.cod,
        delivery_address="Address",
    )
    db_session.add(order)
    await db_session.commit()

    msg = await handle_order_placed(db_session, order.id, sender=sender)
    assert msg is None
    assert len(recorded) == 0


async def test_whatsapp_failure_marks_failed_without_crashing(db_session: AsyncSession):
    phone = "03001230008"

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "Internal Meta Server Error", "code": 500}})

    client = WhatsAppClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)),
        max_retries=1,
        base_delay=0.01,
    )
    sender = SafeSender(client=client)

    patient = User(
        id=uuid.uuid4(),
        full_name="Failing Patient",
        phone=phone,
        email="failing@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="Branch",
        city="Karachi",
        address="Address",
        phone="02130000000",
        is_active=True,
    )
    db_session.add_all([patient, branch])
    await db_session.flush()

    order = Order(
        id=uuid.uuid4(),
        patient_id=patient.id,
        branch_id=branch.id,
        status=OrderStatus.pending,
        subtotal=Decimal("500.00"),
        grand_total=Decimal("650.00"),
        payment_method=PaymentMethod.cod,
        delivery_address="Address",
    )
    db_session.add(order)
    await db_session.commit()

    msg = await handle_order_placed(db_session, order.id, sender=sender)
    assert msg is not None
    assert msg.status == WhatsAppMessageStatus.failed
    assert msg.error_message is not None


async def test_whatsapp_failure_does_not_break_appointment_booking(
    db_session: AsyncSession,
    monkeypatch,
):
    from app.schemas.appointment import AppointmentCreate
    from app.services.appointment_service import AppointmentService

    # Make SafeSender always raise an exception
    async def failing_send(*args, **kwargs):
        raise RuntimeError("Meta WhatsApp API unreachable")

    monkeypatch.setattr(SafeSender, "send_template", failing_send)

    user_doc = User(
        id=uuid.uuid4(),
        full_name="Dr. Robust",
        phone="+923001230010",
        email="doc_robust@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.doctor,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="Clifton Branch",
        city="Karachi",
        address="Clifton Block 2",
        phone="02135800002",
        is_active=True,
    )
    db_session.add_all([user_doc, branch])
    await db_session.flush()

    doctor = Doctor(
        id=uuid.uuid4(),
        user_id=user_doc.id,
        specialization="Cardiology",
        qualification="MBBS, FCPS",
        consultation_fee=1500.0,
        is_active=True,
    )
    db_session.add(doctor)
    await db_session.flush()

    avail = DoctorAvailability(
        id=uuid.uuid4(),
        doctor_id=doctor.id,
        branch_id=branch.id,
        day_of_week=DayOfWeek.monday,
        start_time=datetime(2026, 9, 7, 9, 0).time(),
        end_time=datetime(2026, 9, 7, 12, 0).time(),
        is_active=True,
    )
    patient = User(
        id=uuid.uuid4(),
        full_name="Booking Robust Patient",
        phone="+923001230011",
        email="patient_robust@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add_all([avail, patient])
    await db_session.flush()

    slot_time = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    body = AppointmentCreate(
        doctor_id=doctor.id,
        branch_id=branch.id,
        slot_datetime=slot_time,
        notes="Checkup under failure",
    )

    svc = AppointmentService(db_session)
    # Booking must NOT raise even though WhatsApp fails
    resp = await svc.book_appointment(patient=patient, body=body)
    assert resp.status == "confirmed"


async def test_whatsapp_failure_does_not_break_order_checkout(
    db_session: AsyncSession,
    monkeypatch,
):
    from app.models.cart import CartItem
    from app.models.category import Category
    from app.models.product import BranchStock, Product
    from app.schemas.cart_order import CheckoutRequest
    from app.services.checkout_service import CheckoutService

    async def failing_send(*args, **kwargs):
        raise RuntimeError("Meta WhatsApp API down during checkout")

    monkeypatch.setattr(SafeSender, "send_template", failing_send)

    branch = Branch(
        id=uuid.uuid4(),
        name="Pharmacy Robust Branch",
        city="Karachi",
        address="DHA Phase 6",
        phone="02135800003",
        is_active=True,
    )
    category = Category(id=uuid.uuid4(), name="Medicines", slug=f"med-{uuid.uuid4().hex[:6]}")
    product = Product(
        id=uuid.uuid4(),
        name="Paracetamol",
        sku=f"PCM-{uuid.uuid4().hex[:6]}",
        price=Decimal("100.00"),
        discount_percent=Decimal("0.00"),
        category_id=category.id,
        is_active=True,
    )
    patient = User(
        id=uuid.uuid4(),
        full_name="Checkout Robust User",
        phone="+923001230012",
        email="checkout_robust@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add_all([branch, category, product, patient])
    await db_session.flush()

    branch_stock = BranchStock(
        product_id=product.id,
        branch_id=branch.id,
        stock=20,
    )
    cart_item = CartItem(
        user_id=patient.id,
        product_id=product.id,
        branch_id=branch.id,
        quantity=2,
    )
    db_session.add_all([branch_stock, cart_item])
    await db_session.flush()

    checkout_payload = CheckoutRequest(
        payment_method=PaymentMethod.cod,
        delivery_method="delivery",
        delivery_address="Robust St, Karachi",
    )

    svc = CheckoutService(db_session)
    # Checkout must NOT raise even though WhatsApp fails
    order_resp = await svc.checkout(user=patient, body=checkout_payload)
    assert order_resp is not None
    assert order_resp.status == OrderStatus.pending
