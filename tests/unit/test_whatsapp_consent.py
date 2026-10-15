"""Unit and integration tests for WhatsApp consent, SafeSender guard, and STOP keywords."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.appointment import Appointment
from app.models.branch import Branch
from app.models.doctor import DayOfWeek, Doctor, DoctorAvailability
from app.models.order import Order
from app.models.user import User, UserRole
from app.models.whatsapp_consent import WhatsAppConsent
from app.services.appointment_service import AppointmentService
from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.consent_service import ConsentService
from app.services.whatsapp.constants import (
    START_CONFIRMATION_EN,
    STOP_ALL_CONFIRMATION_EN,
    STOP_CONFIRMATION_EN,
)
from app.services.whatsapp.exceptions import ConsentBlockedError
from app.services.whatsapp.keyword_handler import handle_inbound_keyword
from app.services.whatsapp.sender import SafeSender
from app.utils.security import hash_password

SECRET = "test-secret-consent-webhook-32chars"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _wa_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "verify-token-123")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token-123")

    orig_init = WhatsAppClient.__init__

    def mock_init(self, *args, **kwargs):
        if "http_client" not in kwargs or kwargs["http_client"] is None:
            def default_handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"messages": [{"id": "wamid.default.mock"}]})
            kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(default_handler))
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(WhatsAppClient, "__init__", mock_init)


# ── 1. ConsentService Matrix & Edge Cases ────────────────────────────────────

async def test_unknown_phone_defaults(db_session: AsyncSession):
    phone = "03001112233"
    # Unknown phone: transactional allowed, marketing blocked
    assert await ConsentService.can_send(db_session, phone, category="transactional") is True
    assert await ConsentService.can_send(db_session, phone, category="marketing") is False


async def test_can_send_matrix(db_session: AsyncSession):
    phone = "03002223344"

    # 1. Opt in to marketing
    await ConsentService.set_marketing_opt_in(db_session, phone, value=True, source="test")
    await db_session.flush()

    assert await ConsentService.can_send(db_session, phone, "marketing") is True
    assert await ConsentService.can_send(db_session, phone, "transactional") is True

    # 2. Opt out of marketing (transactional stays on)
    await ConsentService.set_marketing_opt_in(db_session, phone, value=False, source="test")
    await db_session.flush()

    assert await ConsentService.can_send(db_session, phone, "marketing") is False
    assert await ConsentService.can_send(db_session, phone, "transactional") is True

    # 3. Opt out of transactional as well
    await ConsentService.set_transactional(db_session, phone, value=False, source="test")
    await db_session.flush()

    assert await ConsentService.can_send(db_session, phone, "marketing") is False
    assert await ConsentService.can_send(db_session, phone, "transactional") is False


# ── 2. Inbound Keywords (STOP, STOP ALL, START) ──────────────────────────────

async def test_keyword_stop_opt_out_marketing_only(db_session: AsyncSession):
    phone = "03003334455"
    # Start with marketing opt-in
    await ConsentService.set_marketing_opt_in(db_session, phone, value=True, source="seed")
    await db_session.flush()

    recorded = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.conf.1"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    handled = await handle_inbound_keyword(db_session, phone, "STOP", client=client)
    await db_session.flush()

    assert handled is True
    assert await ConsentService.can_send(db_session, phone, "marketing") is False
    assert await ConsentService.can_send(db_session, phone, "transactional") is True
    assert len(recorded) == 1


async def test_keyword_stop_all_opt_out_both(db_session: AsyncSession):
    phone = "03004445566"
    recorded = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.conf.2"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    handled = await handle_inbound_keyword(db_session, phone, "  stop  all  ", client=client)
    await db_session.flush()

    assert handled is True
    assert await ConsentService.can_send(db_session, phone, "marketing") is False
    assert await ConsentService.can_send(db_session, phone, "transactional") is False
    assert len(recorded) == 1


async def test_keyword_start_resumes_all(db_session: AsyncSession):
    phone = "03005556677"
    # First turn everything off
    await ConsentService.set_marketing_opt_in(db_session, phone, value=False, source="seed")
    await ConsentService.set_transactional(db_session, phone, value=False, source="seed")
    await db_session.flush()

    recorded = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.conf.3"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    handled = await handle_inbound_keyword(db_session, phone, "START", client=client)
    await db_session.flush()

    assert handled is True
    assert await ConsentService.can_send(db_session, phone, "marketing") is True
    assert await ConsentService.can_send(db_session, phone, "transactional") is True


@pytest.mark.parametrize(
    ("text", "expected_handled"),
    [
        ("unsubscribe", True),
        ("  UNSUBSCRIBE  ", True),
        ("Unstop", True),
        ("stopall", True),
        ("Hello doctor", False),
        ("appointment details", False),
        ("", False),
    ],
)
async def test_keyword_variants_and_unmatched(db_session: AsyncSession, text: str, expected_handled: bool):
    phone = f"0300666{abs(hash(text)) % 9000 + 1000:04d}"
    handled = await handle_inbound_keyword(db_session, phone, text)
    assert handled is expected_handled


# ── 3. SafeSender Guard Tests ────────────────────────────────────────────────

async def test_safe_sender_guard_blocks_marketing_without_opt_in(db_session: AsyncSession):
    phone = "03007778899"
    recorded = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.send.1"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    sender = SafeSender(client=client)

    # 1. Marketing template blocked for unknown phone
    with pytest.raises(ConsentBlockedError) as exc_info:
        await sender.send_template(
            db=db_session,
            to=phone,
            template_key="promotional_offer",
            params={
                "customer_name": "Hamza",
                "offer_title": "Free Checkup",
                "discount_details": "50% off",
                "expiry_date": "31 Oct",
            },
        )
    assert "blocked by user consent" in str(exc_info.value)
    assert len(recorded) == 0

    # 2. Marketing free-form text blocked
    with pytest.raises(ConsentBlockedError):
        await sender.send_text(db=db_session, to=phone, text="Sale now!", category="marketing")
    assert len(recorded) == 0

    # 3. Transactional template allowed for unknown phone
    wamid = await sender.send_template(
        db=db_session,
        to=phone,
        template_key="appointment_confirmed",
        params={
            "patient_name": "Hamza",
            "doctor_name": "Dr. Sarah",
            "slot_datetime": "15 Oct",
            "clinic_branch": "Clifton",
        },
    )
    assert wamid == "wamid.send.1"
    assert len(recorded) == 1

    # 4. Once opted in, marketing template succeeds
    await ConsentService.set_marketing_opt_in(db_session, phone, value=True, source="promo_signup")
    await db_session.flush()

    wamid_mkt = await sender.send_template(
        db=db_session,
        to=phone,
        template_key="promotional_offer",
        params={
            "customer_name": "Hamza",
            "offer_title": "Free Checkup",
            "discount_details": "50% off",
            "expiry_date": "31 Oct",
        },
    )
    assert wamid_mkt == "wamid.send.1"
    assert len(recorded) == 2


# ── 4. Webhook Inbound Integration ───────────────────────────────────────────

async def test_webhook_inbound_keyword_signed_payload(async_client: AsyncClient, db_session: AsyncSession):
    phone = "03009998877"
    normalized_phone = "923009998877"

    # Pre-opt in to marketing
    await ConsentService.set_marketing_opt_in(db_session, phone, value=True, source="seed")
    await db_session.flush()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.kw.test.1",
                                    "from": phone,
                                    "type": "text",
                                    "text": {"body": "STOP"},
                                    "timestamp": "1729000800",
                                }
                            ]
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

    # Verify marketing consent is now False
    consent = (
        await db_session.execute(
            select(WhatsAppConsent).where(WhatsAppConsent.phone == normalized_phone)
        )
    ).scalar_one_or_none()

    assert consent is not None
    assert consent.marketing_opt_in is False
    assert consent.transactional_opt_in is True
    assert consent.opt_out_at is not None


# ── 5. Website Opt-in Flows (Registration, Appointment, Checkout) ───────────

async def test_patient_registration_with_marketing_opt_in(async_client: AsyncClient, db_session: AsyncSession):
    reg_phone = "+923009876543"
    norm_phone = "923009876543"

    reg_payload = {
        "full_name": "Consent Test User",
        "phone": reg_phone,
        "password": "SecurePassword123!",
        "marketing_opt_in": True,
    }

    res = await async_client.post("/api/v1/auth/register", json=reg_payload)
    assert res.status_code == 201

    consent = (
        await db_session.execute(
            select(WhatsAppConsent).where(WhatsAppConsent.phone == norm_phone)
        )
    ).scalar_one_or_none()

    assert consent is not None
    assert consent.marketing_opt_in is True
    assert consent.opt_in_source == "patient_registration"
    assert consent.user_id is not None


async def test_appointment_booking_with_marketing_opt_in(db_session: AsyncSession):
    # Setup test doctor, branch, availability, patient
    user_doc = User(
        id=uuid.uuid4(),
        full_name="Doctor Test",
        phone="+923001110001",
        email="doc@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.doctor,
        is_active=True,
    )
    branch = Branch(
        id=uuid.uuid4(),
        name="Test Branch",
        city="Karachi",
        address="Clifton Block 2, Karachi",
        phone="02131234567",
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
        full_name="Patient With Consent",
        phone="+923002220002",
        email="pat@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add_all([avail, patient])
    await db_session.flush()

    from app.schemas.appointment import AppointmentCreate

    slot_time = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    body = AppointmentCreate(
        doctor_id=doctor.id,
        branch_id=branch.id,
        slot_datetime=slot_time,
        notes="Checkup",
        marketing_opt_in=True,
    )

    svc = AppointmentService(db_session)
    resp = await svc.book_appointment(patient=patient, body=body)
    assert resp.status == "confirmed"

    # Verify marketing consent was set
    norm_phone = "923002220002"
    consent = (
        await db_session.execute(
            select(WhatsAppConsent).where(WhatsAppConsent.phone == norm_phone)
        )
    ).scalar_one_or_none()

    assert consent is not None
    assert consent.marketing_opt_in is True
    assert consent.opt_in_source == "appointment_booking"


async def test_checkout_with_marketing_opt_in(db_session: AsyncSession):
    from decimal import Decimal
    from app.models.category import Category
    from app.models.product import BranchStock, Product
    from app.models.cart import CartItem
    from app.models.order import PaymentMethod
    from app.schemas.cart_order import CheckoutRequest
    from app.services.checkout_service import CheckoutService

    branch = Branch(
        id=uuid.uuid4(),
        name="Checkout Branch",
        city="Karachi",
        address="DHA Phase 5",
        phone="02139876543",
        is_active=True,
    )
    category = Category(id=uuid.uuid4(), name="Wellness", slug=f"well-{uuid.uuid4().hex[:6]}")
    product = Product(
        id=uuid.uuid4(),
        name="Vitamin C",
        sku=f"VITC-{uuid.uuid4().hex[:6]}",
        price=Decimal("500.00"),
        discount_percent=Decimal("0.00"),
        category_id=category.id,
        is_active=True,
    )
    patient = User(
        id=uuid.uuid4(),
        full_name="Checkout Consent User",
        phone="+923003330003",
        email="checkout_consent@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add_all([branch, category, product, patient])
    await db_session.flush()

    branch_stock = BranchStock(
        product_id=product.id,
        branch_id=branch.id,
        stock=10,
    )
    cart_item = CartItem(
        user_id=patient.id,
        product_id=product.id,
        branch_id=branch.id,
        quantity=1,
    )
    db_session.add_all([branch_stock, cart_item])
    await db_session.flush()

    checkout_payload = CheckoutRequest(
        payment_method=PaymentMethod.cod,
        delivery_method="delivery",
        delivery_address="123 Test Street, Karachi",
        marketing_opt_in=True,
    )

    svc = CheckoutService(db_session)
    order_resp = await svc.checkout(user=patient, body=checkout_payload)
    assert order_resp is not None

    norm_phone = "923003330003"
    consent = (
        await db_session.execute(
            select(WhatsAppConsent).where(WhatsAppConsent.phone == norm_phone)
        )
    ).scalar_one_or_none()

    assert consent is not None
    assert consent.marketing_opt_in is True
    assert consent.opt_in_source == "checkout"
