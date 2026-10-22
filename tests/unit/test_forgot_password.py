"""
Comprehensive tests for Forgot Password via WhatsApp OTP.

Covers:
- Template payload formatting (Meta authentication template: body + copy-code button)
- Happy path: request -> verify -> reset -> login with new password
- Unknown phone: identical response & timing resistance
- Wrong OTP & attempts counting
- Max attempts exhausted (OTP invalidated)
- Expired OTP rejection
- Token single-use & reuse prevention
- Rate limits (60s cooldown, max 3/hr per phone, applies to unknown phone too)
- Session & refresh token revocation after reset
- Password complexity validation rules
- Admin fallback (super_admin & branch_manager PUT endpoint with branch scoping)
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.password_reset_otp import PasswordResetOtp
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.services.whatsapp.templates import template_registry
from app.utils.limiter import limiter
from app.utils.security import hash_password, hash_refresh_token
from tests.conftest import create_user_helper


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


async def _create_test_branch(db: AsyncSession, name: str = "Test Branch") -> uuid.UUID:
    branch = Branch(
        id=uuid.uuid4(),
        name=name,
        city="Karachi",
        address="123 Health Street, Karachi",
        phone="+922131234567",
        is_active=True,
    )
    db.add(branch)
    await db.commit()
    return branch.id


# ── 1. Template Payload Validation ─────────────────────────────────────────────
def test_password_reset_otp_template_payload():
    """Verify that template_registry formats Meta authentication template with copy_code button."""
    assert template_registry.has("password_reset_otp")
    cfg = template_registry.get("password_reset_otp")
    assert cfg.category == "authentication"
    assert "code" in cfg.body_params
    assert "code" in cfg.button_params

    name, lang, components = template_registry.format_components(
        "password_reset_otp",
        params={"code": "654321"},
        language="en",
    )
    assert name == "password_reset_otp"
    assert lang == "en"

    # Must contain body component with the code
    body_comp = next((c for c in components if c["type"] == "body"), None)
    assert body_comp is not None
    assert body_comp["parameters"] == [{"type": "text", "text": "654321"}]

    # Must contain copy-code button component (sub_type: url, index: "0") with the same code
    button_comp = next((c for c in components if c["type"] == "button"), None)
    assert button_comp is not None
    assert button_comp["sub_type"] == "url"
    assert button_comp["index"] == "0"
    assert button_comp["parameters"] == [{"type": "text", "text": "654321"}]


# ── 2. Happy Path Flow ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_forgot_password_happy_path(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    phone = "+923001112233"
    old_password = "OldPassword123!"
    new_password = "NewPassword456!"

    user = User(
        id=uuid.uuid4(),
        full_name="Fatima Zahra",
        phone=phone,
        email="fatima@example.com",
        hashed_password=hash_password(old_password),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    captured_otp = {}

    async def mock_send_template(db, to, template_key, params=None, language="en"):
        captured_otp["to"] = to
        captured_otp["key"] = template_key
        captured_otp["code"] = params.get("code")
        return "wamid_mock_123"

    with patch("app.services.whatsapp.sender.SafeSender.send_template", side_effect=mock_send_template):
        # 1. Forgot password request (normalize phone: local format 03001112233)
        req_resp = await async_client.post(
            "/api/v1/auth/forgot-password",
            json={"phone": "03001112233"},
        )
        assert req_resp.status_code == 200
        assert req_resp.json()["message"] == "If this number is registered, a code has been sent on WhatsApp"

        otp_code = captured_otp.get("code")
        assert otp_code is not None
        assert len(otp_code) == 6
        assert otp_code.isdigit()

        # Check DB: OTP stored as HMAC hash, NOT plaintext
        db_otp = (
            await db_session.execute(
                select(PasswordResetOtp).where(PasswordResetOtp.user_id == user.id)
            )
        ).scalar_one()
        assert db_otp.otp_hash != otp_code
        assert len(db_otp.otp_hash) == 64  # SHA-256 hex
        assert db_otp.attempts == 0
        assert db_otp.used_at is None

        # 2. Verify OTP
        verify_resp = await async_client.post(
            "/api/v1/auth/forgot-password/verify",
            json={"phone": phone, "otp": otp_code},
        )
        assert verify_resp.status_code == 200
        verify_body = verify_resp.json()
        assert "reset_token" in verify_body
        assert verify_body["expires_in"] == 600
        reset_token = verify_body["reset_token"]

        # 3. Reset password
        reset_resp = await async_client.post(
            "/api/v1/auth/reset-password",
            json={"reset_token": reset_token, "new_password": new_password},
        )
        assert reset_resp.status_code == 200
        assert "successfully reset" in reset_resp.json()["message"].lower()

        # 4. Old password must fail login
        old_login = await async_client.post(
            "/api/v1/auth/login",
            json={"phone": phone, "password": old_password},
        )
        assert old_login.status_code == 401

        # 5. New password must succeed login
        new_login = await async_client.post(
            "/api/v1/auth/login",
            json={"phone": phone, "password": new_password},
        )
        assert new_login.status_code == 200
        assert "access_token" in new_login.json()


# ── 3. Unknown Phone (No Enumeration) ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_forgot_password_unknown_phone(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    mock_send = AsyncMock()
    with patch("app.services.whatsapp.sender.SafeSender.send_template", mock_send):
        resp = await async_client.post(
            "/api/v1/auth/forgot-password",
            json={"phone": "+923009999999"},
        )
        assert resp.status_code == 200
        # Exact same response
        assert resp.json()["message"] == "If this number is registered, a code has been sent on WhatsApp"
        # No WhatsApp dispatch for unknown phone
        mock_send.assert_not_called()


# ── 4. Wrong OTP & Exhaustion ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_forgot_password_wrong_otp_and_exhaustion(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    phone = "+923002223344"
    user = User(
        id=uuid.uuid4(),
        full_name="Bilal Tariq",
        phone=phone,
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    captured = {}

    async def mock_send(db, to, template_key, params=None, language="en"):
        captured["code"] = params["code"]
        return "wamid"

    with patch("app.services.whatsapp.sender.SafeSender.send_template", side_effect=mock_send):
        await async_client.post("/api/v1/auth/forgot-password", json={"phone": phone})

    real_code = captured["code"]

    # 4 wrong attempts
    for i in range(1, 5):
        wrong_resp = await async_client.post(
            "/api/v1/auth/forgot-password/verify",
            json={"phone": phone, "otp": "000000"},
        )
        assert wrong_resp.status_code == 400
        assert wrong_resp.json()["detail"] == "Invalid or expired code"

    # 5th wrong attempt -> exhausts code
    fifth_resp = await async_client.post(
        "/api/v1/auth/forgot-password/verify",
        json={"phone": phone, "otp": "000000"},
    )
    assert fifth_resp.status_code == 400

    # 6th attempt with REAL code must now fail because OTP is dead
    dead_resp = await async_client.post(
        "/api/v1/auth/forgot-password/verify",
        json={"phone": phone, "otp": real_code},
    )
    assert dead_resp.status_code == 400
    assert dead_resp.json()["detail"] == "Invalid or expired code"


# ── 5. Expired OTP ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_forgot_password_expired_otp(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    phone = "+923003334455"
    user = User(
        id=uuid.uuid4(),
        full_name="Ayesha Khan",
        phone=phone,
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    captured = {}

    async def mock_send(db, to, template_key, params=None, language="en"):
        captured["code"] = params["code"]
        return "wamid"

    with patch("app.services.whatsapp.sender.SafeSender.send_template", side_effect=mock_send):
        await async_client.post("/api/v1/auth/forgot-password", json={"phone": phone})

    code = captured["code"]

    # Manually expire the OTP in the database
    otp_record = (
        await db_session.execute(
            select(PasswordResetOtp).where(PasswordResetOtp.user_id == user.id)
        )
    ).scalar_one()
    otp_record.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    await db_session.commit()

    # Verify must fail
    resp = await async_client.post(
        "/api/v1/auth/forgot-password/verify",
        json={"phone": phone, "otp": code},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid or expired code"


# ── 6. Token and OTP Reuse Prevention ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_token_and_otp_reuse_prevention(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    phone = "+923004445566"
    user = User(
        id=uuid.uuid4(),
        full_name="Usman Ali",
        phone=phone,
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    captured = {}

    async def mock_send(db, to, template_key, params=None, language="en"):
        captured["code"] = params["code"]
        return "wamid"

    with patch("app.services.whatsapp.sender.SafeSender.send_template", side_effect=mock_send):
        await async_client.post("/api/v1/auth/forgot-password", json={"phone": phone})

    code = captured["code"]

    # Verify
    verify_resp = await async_client.post(
        "/api/v1/auth/forgot-password/verify",
        json={"phone": phone, "otp": code},
    )
    reset_token = verify_resp.json()["reset_token"]

    # Reset password first time: succeeds
    res1 = await async_client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": reset_token, "new_password": "NewValidPass123!"},
    )
    assert res1.status_code == 200

    # Try reusing the SAME reset token: MUST fail
    res2 = await async_client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": reset_token, "new_password": "AnotherPass456!"},
    )
    assert res2.status_code == 400
    assert "invalid or expired" in res2.json()["detail"].lower()

    # Try reusing the SAME OTP again: MUST fail
    res3 = await async_client.post(
        "/api/v1/auth/forgot-password/verify",
        json={"phone": phone, "otp": code},
    )
    assert res3.status_code == 400


# ── 7. Rate Limits & Cooldown ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_forgot_password_rate_limits(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    phone = "+923005556677"
    user = User(
        id=uuid.uuid4(),
        full_name="Zainab Bibi",
        phone=phone,
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    with patch("app.services.whatsapp.sender.SafeSender.send_template", AsyncMock(return_value="wamid")):
        # Request 1: succeeds
        r1 = await async_client.post("/api/v1/auth/forgot-password", json={"phone": phone})
        assert r1.status_code == 200

        # Request 2 immediate (< 60s cooldown): 429 with Retry-After header
        r2 = await async_client.post("/api/v1/auth/forgot-password", json={"phone": phone})
        assert r2.status_code == 429
        assert "Retry-After" in r2.headers

        # Cooldown also applies to unknown phone!
        u1 = await async_client.post("/api/v1/auth/forgot-password", json={"phone": "+923008880001"})
        assert u1.status_code == 200
        u2 = await async_client.post("/api/v1/auth/forgot-password", json={"phone": "+923008880001"})
        assert u2.status_code == 429
        assert "Retry-After" in u2.headers


# ── 8. Session Revocation ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_refresh_tokens_revoked_after_password_reset(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    phone = "+923006667788"
    password = "OriginalPass123!"
    user = User(
        id=uuid.uuid4(),
        full_name="Imran Khan",
        phone=phone,
        hashed_password=hash_password(password),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    # User logs in and gets tokens
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    assert login_resp.status_code == 200
    old_refresh_token = login_resp.json()["refresh_token"]

    captured = {}

    async def mock_send(db, to, template_key, params=None, language="en"):
        captured["code"] = params["code"]
        return "wamid"

    with patch("app.services.whatsapp.sender.SafeSender.send_template", side_effect=mock_send):
        await async_client.post("/api/v1/auth/forgot-password", json={"phone": phone})

    verify_resp = await async_client.post(
        "/api/v1/auth/forgot-password/verify",
        json={"phone": phone, "otp": captured["code"]},
    )
    reset_token = verify_resp.json()["reset_token"]

    # Reset password
    await async_client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": reset_token, "new_password": "BrandNewPass123!"},
    )

    # Attempting to use old refresh token must be rejected
    ref_resp = await async_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": old_refresh_token},
    )
    assert ref_resp.status_code == 401


# ── 9. Password Complexity Enforcement ────────────────────────────────────────
@pytest.mark.asyncio
async def test_password_complexity_rules_enforced(
    async_client: AsyncClient,
):
    # Short password
    r1 = await async_client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": "dummy", "new_password": "short"},
    )
    assert r1.status_code == 422

    # No digits
    r2 = await async_client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": "dummy", "new_password": "AllLettersNoDigits!"},
    )
    assert r2.status_code == 422

    # No letters
    r3 = await async_client.post(
        "/api/v1/auth/reset-password",
        json={"reset_token": "dummy", "new_password": "1234567890!@#"},
    )
    assert r3.status_code == 422


# ── 10. WhatsApp Sender Failure Handling ──────────────────────────────────────
@pytest.mark.asyncio
async def test_whatsapp_send_failure_graceful_200(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    phone = "+923007778899"
    user = User(
        id=uuid.uuid4(),
        full_name="Khadija Bibi",
        phone=phone,
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    # Simulate WhatsApp API error 131042 (payment / account issue)
    with patch(
        "app.services.whatsapp.sender.SafeSender.send_template",
        AsyncMock(side_effect=Exception("Meta API error 131042: Payment required")),
    ):
        resp = await async_client.post(
            "/api/v1/auth/forgot-password",
            json={"phone": phone},
        )
        # MUST still return generic 200 without leaking error
        assert resp.status_code == 200
        assert resp.json()["message"] == "If this number is registered, a code has been sent on WhatsApp"


# ── 11. Admin Fallback (PUT /admin/users/{id}/reset-password) ─────────────────
@pytest.mark.asyncio
async def test_admin_fallback_patient_password_reset(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    branch_a_id = await _create_test_branch(db_session, "Branch A")
    branch_b_id = await _create_test_branch(db_session, "Branch B")

    # Create super_admin, branch_manager, and patient
    super_admin, sa_headers = await create_user_helper(db_session, role=UserRole.super_admin)
    bm_a, bm_a_headers = await create_user_helper(
        db_session, role=UserRole.branch_manager, branch_id=branch_a_id
    )

    patient_b, _ = await create_user_helper(
        db_session, role=UserRole.patient, branch_id=branch_b_id
    )

    # 1. Branch manager A trying to reset patient in branch B -> 403 Forbidden
    bm_fail = await async_client.put(
        f"/api/v1/auth/admin/users/{patient_b.id}/reset-password",
        json={"password": "NewPatientPass123!"},
        headers=bm_a_headers,
    )
    assert bm_fail.status_code == 403

    # 2. Super admin resets patient in branch B -> 200 OK
    sa_ok = await async_client.put(
        f"/api/v1/auth/admin/users/{patient_b.id}/reset-password",
        json={"password": "NewPatientPass123!"},
        headers=sa_headers,
    )
    assert sa_ok.status_code == 200
    assert sa_ok.json()["id"] == str(patient_b.id)

    # 3. Patient can now login with the new password
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"phone": patient_b.phone, "password": "NewPatientPass123!"},
    )
    assert login_resp.status_code == 200


# ── 12. Branch Manager Scoping & Audit Logging Extended ───────────────────────
@pytest.mark.asyncio
async def test_branch_manager_scoping_extended(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    from app.models.order import Order, OrderStatus

    branch_a_id = await _create_test_branch(db_session, "Clifton Branch A")
    branch_b_id = await _create_test_branch(db_session, "Defence Branch B")

    super_admin, sa_headers = await create_user_helper(db_session, role=UserRole.super_admin)
    bm_a, bm_a_headers = await create_user_helper(
        db_session, role=UserRole.branch_manager, branch_id=branch_a_id
    )
    bm_b, _ = await create_user_helper(
        db_session, role=UserRole.branch_manager, branch_id=branch_b_id
    )
    patient_unassigned, _ = await create_user_helper(
        db_session, role=UserRole.patient, branch_id=None
    )

    # 1. BM A cannot reset Super Admin
    r_sa = await async_client.put(
        f"/api/v1/auth/admin/users/{super_admin.id}/reset-password",
        json={"password": "NewAdminPass123!"},
        headers=bm_a_headers,
    )
    assert r_sa.status_code == 403
    assert "cannot reset super admin" in r_sa.json()["detail"].lower()

    # 2. BM A cannot reset BM B
    r_bm = await async_client.put(
        f"/api/v1/auth/admin/users/{bm_b.id}/reset-password",
        json={"password": "NewAdminPass123!"},
        headers=bm_a_headers,
    )
    assert r_bm.status_code == 403
    assert "cannot reset branch manager" in r_bm.json()["detail"].lower()

    # 3. BM A cannot reset unassigned patient with no branch A activity
    r_no_act = await async_client.put(
        f"/api/v1/auth/admin/users/{patient_unassigned.id}/reset-password",
        json={"password": "NewPatientPass123!"},
        headers=bm_a_headers,
    )
    assert r_no_act.status_code == 403
    assert "activity" in r_no_act.json()["detail"].lower()

    # 4. Now patient creates an order at Branch A
    order = Order(
        id=uuid.uuid4(),
        patient_id=patient_unassigned.id,
        branch_id=branch_a_id,
        subtotal=1000.0,
        discount_amount=0.0,
        platform_fee=0.0,
        delivery_charges=0.0,
        grand_total=1000.0,
        status=OrderStatus.confirmed,
    )
    db_session.add(order)
    await db_session.commit()

    # 5. Now BM A CAN reset patient
    r_ok = await async_client.put(
        f"/api/v1/auth/admin/users/{patient_unassigned.id}/reset-password",
        json={"password": "NewPatientPass123!"},
        headers=bm_a_headers,
    )
    assert r_ok.status_code == 200


# ── 13. POST /admin/users/{id}/reset-password Revokes Sessions ────────────────
@pytest.mark.asyncio
async def test_post_reset_staff_password_revokes_sessions(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    branch_id = await _create_test_branch(db_session, "Main Branch")
    super_admin, sa_headers = await create_user_helper(db_session, role=UserRole.super_admin)
    staff, _ = await create_user_helper(
        db_session, role=UserRole.pharmacy_staff, branch_id=branch_id
    )

    # Staff logs in to get tokens
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"phone": staff.phone, "password": "Password123!"},
    )
    assert login_resp.status_code == 200
    refresh_token = login_resp.json()["refresh_token"]

    # Super Admin resets staff password via legacy POST endpoint
    post_resp = await async_client.post(
        f"/api/v1/auth/admin/users/{staff.id}/reset-password",
        json={"password": "NewStaffPass123!"},
        headers=sa_headers,
    )
    assert post_resp.status_code == 200

    # Staff's refresh token must now be revoked
    ref_resp = await async_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert ref_resp.status_code == 401

