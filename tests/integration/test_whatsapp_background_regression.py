"""Regression tests for WhatsApp background dispatching, strong-reference task registry,
and router-level BackgroundTasks integration.
"""
from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.appointment import Appointment, AppointmentStatus
from app.models.branch import Branch
from app.models.cart import CartItem
from app.models.category import Category
from app.models.doctor import DayOfWeek, Doctor, DoctorAvailability
from app.models.lab_report import LabReport, ReportFileType
from app.models.lab_test import LabTest
from app.models.order import Order, OrderStatus, PaymentMethod
from app.models.product import BranchStock, Product
from app.models.user import User, UserRole
from app.models.whatsapp_message import WhatsAppMessage
from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.transactional_handlers import (
    _background_tasks,
    trigger_appointment_confirmed,
)
from tests.conftest import TestSessionLocal, create_user_helper


@pytest.fixture(autouse=True)
def _setup_regression_env(monkeypatch):
    _background_tasks.clear()
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "REPORT_LINK_SECRET", "test-secret-regression-32chars-min")
    monkeypatch.setattr(settings, "API_PUBLIC_URL", "http://test")

    # Ensure background runner uses TestSessionLocal
    import app.services.whatsapp.transactional_handlers as th
    monkeypatch.setattr(th, "AsyncSessionLocal", TestSessionLocal)

    # Mock WhatsAppClient HTTP transport
    orig_init = WhatsAppClient.__init__

    def mock_init(self, *args, **kwargs):
        if "http_client" not in kwargs or kwargs["http_client"] is None:
            def default_handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"messages": [{"id": f"wamid.mock.{uuid.uuid4().hex[:8]}"}]})
            kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(default_handler))
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(WhatsAppClient, "__init__", mock_init)
    yield
    _background_tasks.clear()


async def _drain_background_tasks():
    loop = asyncio.get_running_loop()
    tasks = [t for t in list(_background_tasks) if t.get_loop() is loop and not t.done()]
    if tasks:
        await asyncio.gather(*tasks)
    await asyncio.sleep(0.05)


import random


def _random_phone() -> str:
    return f"+92300{random.randint(1000000, 9999999)}"


# ── Helper Data Setup ──────────────────────────────────────────────────────────

async def _setup_doctor_and_branch(session: AsyncSession):
    branch = Branch(
        id=uuid.uuid4(),
        name=f"Clifton Centre {uuid.uuid4().hex[:4]}",
        city="Karachi",
        address="Clifton Block 2",
        phone="02135000001",
        is_active=True,
    )
    session.add(branch)
    await session.commit()

    doc_user, _ = await create_user_helper(
        session,
        role=UserRole.doctor,
        phone=_random_phone(),
    )
    doctor = Doctor(
        id=uuid.uuid4(),
        user_id=doc_user.id,
        specialization="Cardiology",
        qualification="MBBS, FCPS (Cardiology)",
        experience_years=10,
        consultation_fee=Decimal("2500.00"),
        is_active=True,
    )
    session.add(doctor)
    await session.commit()

    # Add availability covering Monday through Sunday 08:00 - 18:00
    for dow in DayOfWeek:
        avail = DoctorAvailability(
            id=uuid.uuid4(),
            doctor_id=doctor.id,
            branch_id=branch.id,
            day_of_week=dow,
            start_time=time(8, 0),
            end_time=time(18, 0),
            is_active=True,
        )
        session.add(avail)
    await session.commit()
    return branch, doctor, doc_user


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_fire_and_forget_fallback_retained_in_task_set(db_session: AsyncSession):
    """Verify fallback task with background_tasks=None is tracked in _background_tasks until completion."""
    branch, doctor, _ = await _setup_doctor_and_branch(db_session)
    patient, _ = await create_user_helper(
        db_session,
        role=UserRole.patient,
        phone=_random_phone(),
    )

    slot_dt = datetime.now(UTC).replace(hour=11, minute=0, second=0, microsecond=0) + timedelta(days=2)
    appt = Appointment(
        id=uuid.uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        branch_id=branch.id,
        slot_datetime=slot_dt,
        status=AppointmentStatus.confirmed,
    )
    db_session.add(appt)
    await db_session.commit()

    # Trigger with background_tasks=None (fallback mode)
    trigger_appointment_confirmed(appt.id, background_tasks=None)

    # The task must be added to the strong reference set
    assert len(_background_tasks) > 0

    # Wait for background task to complete
    await asyncio.gather(*list(_background_tasks))
    await asyncio.sleep(0.05)

    # Task must be cleaned up from set after done callback
    assert len(_background_tasks) == 0

    # WhatsApp message row must have been created
    stmt = select(WhatsAppMessage).where(WhatsAppMessage.related_entity_id == appt.id)
    msg = (await db_session.execute(stmt)).scalar_one_or_none()
    assert msg is not None
    assert msg.template_name == "appointment_confirmed"
    assert msg.status.value in ("sent", "delivered", "read", "queued")


async def test_post_appointment_creates_whatsapp_message_via_router(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """POST /api/v1/appointments creates whatsapp_messages row even when client returns immediately."""
    branch, doctor, _ = await _setup_doctor_and_branch(db_session)
    patient, patient_headers = await create_user_helper(
        db_session,
        role=UserRole.patient,
        phone=_random_phone(),
    )

    # Schedule slot for target date at 10:00 AM
    target_dt = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=3)
    payload = {
        "doctor_id": str(doctor.id),
        "branch_id": str(branch.id),
        "slot_datetime": target_dt.isoformat(),
        "notes": "Post booking test",
        "marketing_opt_in": True,
    }

    resp = await async_client.post("/api/v1/appointments", json=payload, headers=patient_headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    appt_id = uuid.UUID(data["id"])

    # Wait for any scheduled fallback tasks if any
    await _drain_background_tasks()

    # Verify whatsapp_messages row created
    stmt = select(WhatsAppMessage).where(WhatsAppMessage.related_entity_id == appt_id)
    msg = (await db_session.execute(stmt)).scalar_one_or_none()
    assert msg is not None
    assert msg.template_name == "appointment_confirmed"


async def test_put_appointment_status_works_without_name_error(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """PUT /api/v1/appointments/{id}/status works for confirmed and cancelled with no NameError."""
    branch, doctor, doc_user = await _setup_doctor_and_branch(db_session)
    doc_token_headers = {"Authorization": f"Bearer {create_user_helper.__globals__['create_access_token'](str(doc_user.id), doc_user.role.value)[0]}"}

    patient, patient_headers = await create_user_helper(
        db_session,
        role=UserRole.patient,
        phone=_random_phone(),
    )

    # 1. Test Cancellation by Patient (Previously hit NameError at line 327)
    slot_dt_1 = datetime.now(UTC).replace(hour=14, minute=0, second=0, microsecond=0) + timedelta(days=2)
    appt1 = Appointment(
        id=uuid.uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        branch_id=branch.id,
        slot_datetime=slot_dt_1,
        status=AppointmentStatus.confirmed,
    )
    db_session.add(appt1)
    await db_session.commit()

    cancel_payload = {
        "status": "cancelled",
        "cancellation_reason": "Patient conflict",
    }
    resp1 = await async_client.put(
        f"/api/v1/appointments/{appt1.id}/status",
        json=cancel_payload,
        headers=patient_headers,
    )
    assert resp1.status_code == 200, resp1.text
    assert resp1.json()["status"] == "cancelled"

    if _background_tasks:
        await asyncio.gather(*list(_background_tasks))
    await asyncio.sleep(0.05)

    stmt1 = select(WhatsAppMessage).where(
        WhatsAppMessage.related_entity_id == appt1.id,
        WhatsAppMessage.template_name == "appointment_cancelled",
    )
    msg1 = (await db_session.execute(stmt1)).scalar_one_or_none()
    assert msg1 is not None

    # 2. Test Confirmation by Doctor (Previously hit NameError at line 327)
    slot_dt_2 = datetime.now(UTC).replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=2)
    appt2 = Appointment(
        id=uuid.uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        branch_id=branch.id,
        slot_datetime=slot_dt_2,
        status=AppointmentStatus.pending,
    )
    db_session.add(appt2)
    await db_session.commit()

    confirm_payload = {"status": "confirmed"}
    resp2 = await async_client.put(
        f"/api/v1/appointments/{appt2.id}/status",
        json=confirm_payload,
        headers=doc_token_headers,
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["status"] == "confirmed"

    await _drain_background_tasks()

    stmt2 = select(WhatsAppMessage).where(
        WhatsAppMessage.related_entity_id == appt2.id,
        WhatsAppMessage.template_name == "appointment_confirmed",
    )
    msg2 = (await db_session.execute(stmt2)).scalar_one_or_none()
    assert msg2 is not None


async def test_order_placed_and_status_change_creates_whatsapp_messages(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """POST /api/v1/pharmacy/orders and PUT status creates whatsapp_messages rows."""
    branch = Branch(
        id=uuid.uuid4(),
        name="Pharmacy Dispatch Branch",
        city="Karachi",
        address="Gulshan Block 5",
        phone="02134000001",
        is_active=True,
    )
    category = Category(id=uuid.uuid4(), name="Pain Relief", slug=f"pain-{uuid.uuid4().hex[:6]}")
    product = Product(
        id=uuid.uuid4(),
        name="Ibuprofen 400mg",
        sku=f"IBU-{uuid.uuid4().hex[:6]}",
        price=Decimal("150.00"),
        discount_percent=Decimal("0.00"),
        category_id=category.id,
        is_active=True,
    )
    db_session.add_all([branch, category, product])
    await db_session.commit()

    patient, patient_headers = await create_user_helper(
        db_session,
        role=UserRole.patient,
        phone="+923003322119",
        email="patient_order@example.com",
    )
    staff, staff_headers = await create_user_helper(
        db_session,
        role=UserRole.pharmacy_staff,
        phone="+923003322110",
        email="staff_pharmacy@example.com",
        branch_id=branch.id,
    )

    branch_stock = BranchStock(
        product_id=product.id,
        branch_id=branch.id,
        stock=50,
    )
    cart_item = CartItem(
        user_id=patient.id,
        product_id=product.id,
        branch_id=branch.id,
        quantity=3,
    )
    db_session.add_all([branch_stock, cart_item])
    await db_session.commit()

    # 1. Place order via POST /api/v1/pharmacy/orders
    order_payload = {
        "payment_method": "cod",
        "delivery_type": "delivery",
        "delivery_address": "House 10, St 2, Gulshan, Karachi",
    }
    order_resp = await async_client.post(
        "/api/v1/pharmacy/orders",
        json=order_payload,
        headers=patient_headers,
    )
    assert order_resp.status_code == 201, order_resp.text
    order_data = order_resp.json()
    order_id = uuid.UUID(order_data["id"])

    if _background_tasks:
        await asyncio.gather(*list(_background_tasks))
    await asyncio.sleep(0.05)

    stmt_order = select(WhatsAppMessage).where(
        WhatsAppMessage.related_entity_id == order_id,
        WhatsAppMessage.template_name == "order_confirmed",
    )
    msg_order = (await db_session.execute(stmt_order)).scalar_one_or_none()
    assert msg_order is not None

    # 2. Update status via PUT /api/v1/pharmacy/orders/{id}/status
    status_payload = {"status": "confirmed"}
    status_resp = await async_client.put(
        f"/api/v1/pharmacy/orders/{order_id}/status",
        json=status_payload,
        headers=staff_headers,
    )
    assert status_resp.status_code == 200, status_resp.text

    await _drain_background_tasks()

    stmt_status = select(WhatsAppMessage).where(
        WhatsAppMessage.related_entity_id == order_id,
        WhatsAppMessage.template_name == "order_status",
    )
    msg_status = (await db_session.execute(stmt_status)).scalar_one_or_none()
    assert msg_status is not None


async def test_lab_report_visibility_creates_whatsapp_message(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """PUT /api/v1/lab-reports/{id} setting is_visible=true dispatches lab_report_ready WhatsApp message."""
    branch = Branch(
        id=uuid.uuid4(),
        name="Lab Diagnostic Center",
        city="Karachi",
        address="Saddar",
        phone="02132000001",
        is_active=True,
    )
    test = LabTest(
        id=uuid.uuid4(),
        name="HbA1c Blood Test",
        code=f"HBA-{uuid.uuid4().hex[:6]}",
        price=Decimal("1200.00"),
        branch_id=branch.id,
        is_active=True,
    )
    db_session.add_all([branch, test])
    await db_session.commit()

    patient, _ = await create_user_helper(
        db_session,
        role=UserRole.patient,
        phone="+923001122334",
        email="patient_lab@example.com",
    )
    staff, staff_headers = await create_user_helper(
        db_session,
        role=UserRole.lab_staff,
        phone="+923001122335",
        email="staff_lab@example.com",
        branch_id=branch.id,
    )

    report = LabReport(
        id=uuid.uuid4(),
        patient_id=patient.id,
        test_id=test.id,
        file_path=f"reports/{patient.id}/test_report.pdf",
        file_type=ReportFileType.pdf,
        file_name="test_report.pdf",
        is_visible=False,
    )
    db_session.add(report)
    await db_session.commit()

    # Update visibility via PUT /api/v1/lab-reports/{id}
    vis_resp = await async_client.put(
        f"/api/v1/lab-reports/{report.id}",
        json={"is_visible": True},
        headers=staff_headers,
    )
    assert vis_resp.status_code == 200, vis_resp.text

    await _drain_background_tasks()

    stmt = select(WhatsAppMessage).where(
        WhatsAppMessage.related_entity_id == report.id,
        WhatsAppMessage.template_name == "lab_report_ready",
    )
    msg = (await db_session.execute(stmt)).scalar_one_or_none()
    assert msg is not None
