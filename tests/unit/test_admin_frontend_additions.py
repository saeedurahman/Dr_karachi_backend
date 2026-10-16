"""Unit and integration tests for Admin Frontend additions:
1. branch_id in UserResponse (GET /api/v1/auth/me)
2. GET /api/v1/admin/whatsapp/templates (roles: super_admin, branch_manager, marketing category only)
3. Staff create/update with branch_id assignment & branch existence validation
"""
from __future__ import annotations

import random
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.user import UserRole
from tests.conftest import create_user_helper


def _random_digits(n: int = 7) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(n))


async def _create_test_branch(db: AsyncSession, name: str = "Gulshan Clinic") -> uuid.UUID:
    branch = Branch(
        name=name,
        city="Karachi",
        address="Block 5, Gulshan-e-Iqbal",
        phone="+923001234500",
        working_hours={"mon": "09:00-18:00"},
        services=["consultations", "pharmacy"],
        is_active=True,
    )
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    return branch.id


# ── Requirement 1: branch_id in UserResponse ───────────────────────────────────

@pytest.mark.asyncio
async def test_auth_me_returns_branch_id_when_null(async_client: AsyncClient, db_session: AsyncSession):
    """GET /api/v1/auth/me returns branch_id as null when user is not linked to a branch."""
    _, patient_headers = await create_user_helper(db_session, role=UserRole.patient)

    resp = await async_client.get("/api/v1/auth/me", headers=patient_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "branch_id" in data
    assert data["branch_id"] is None


@pytest.mark.asyncio
async def test_auth_me_returns_branch_id_when_assigned(async_client: AsyncClient, db_session: AsyncSession):
    """GET /api/v1/auth/me returns branch_id UUID when user is assigned to a branch."""
    branch_id = await _create_test_branch(db_session, name="Defence Clinic")
    _, bm_headers = await create_user_helper(
        db_session, role=UserRole.branch_manager, branch_id=branch_id
    )

    resp = await async_client.get("/api/v1/auth/me", headers=bm_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "branch_id" in data
    assert data["branch_id"] == str(branch_id)


# ── Requirement 2: GET /api/v1/admin/whatsapp/templates ─────────────────────────

@pytest.mark.asyncio
async def test_whatsapp_templates_accessible_by_super_admin(async_client: AsyncClient, db_session: AsyncSession):
    """super_admin can fetch marketing WhatsApp templates."""
    _, admin_headers = await create_user_helper(db_session, role=UserRole.super_admin)

    resp = await async_client.get("/api/v1/admin/whatsapp/templates", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # Only marketing templates must be returned
    for item in data:
        assert item["category"] == "marketing"
        assert "key" in item
        assert "template_name" in item
        assert "languages" in item or "supported_languages" in item
        assert "parameter_names" in item or "ordered_parameter_names" in item
        # Secrets / Meta IDs must never be exposed
        for forbidden_key in ["waba_id", "phone_number_id", "access_token", "meta_template_id", "secret"]:
            assert forbidden_key not in item

    # Check promotional_offer contents
    promo = next((t for t in data if t["key"] == "promotional_offer"), None)
    assert promo is not None
    assert "en" in promo["languages"]
    assert "customer_name" in promo["parameter_names"]
    assert promo["opt_out_hint"] == "Reply STOP to unsubscribe"


@pytest.mark.asyncio
async def test_whatsapp_templates_accessible_by_branch_manager(async_client: AsyncClient, db_session: AsyncSession):
    """branch_manager can fetch marketing WhatsApp templates."""
    branch_id = await _create_test_branch(db_session)
    _, bm_headers = await create_user_helper(db_session, role=UserRole.branch_manager, branch_id=branch_id)

    resp = await async_client.get("/api/v1/admin/whatsapp/templates", headers=bm_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    # Verify no transactional templates returned
    keys = [t["key"] for t in data]
    assert "appointment_confirmed" not in keys
    assert "appointment_reminder_1h" not in keys
    assert "order_status" not in keys
    assert "lab_report_ready" not in keys


@pytest.mark.asyncio
async def test_whatsapp_templates_forbidden_for_other_roles(async_client: AsyncClient, db_session: AsyncSession):
    """patient, doctor, and pharmacy_staff are forbidden from accessing templates."""
    _, patient_headers = await create_user_helper(db_session, role=UserRole.patient)
    _, doctor_headers = await create_user_helper(db_session, role=UserRole.doctor)
    _, pharmacy_headers = await create_user_helper(db_session, role=UserRole.pharmacy_staff)

    for headers, role_name in [
        (patient_headers, "patient"),
        (doctor_headers, "doctor"),
        (pharmacy_headers, "pharmacy_staff"),
    ]:
        resp = await async_client.get("/api/v1/admin/whatsapp/templates", headers=headers)
        assert resp.status_code == 403, f"Expected 403 for {role_name}, got {resp.status_code}"


@pytest.mark.asyncio
async def test_whatsapp_templates_unauthorized_for_anonymous(async_client: AsyncClient):
    """Anonymous request to templates endpoint is denied (401 or 403)."""
    resp = await async_client.get("/api/v1/admin/whatsapp/templates")
    assert resp.status_code in (401, 403)


# ── Requirement 3: Staff create/update with branch_id & validation ─────────────

@pytest.mark.asyncio
async def test_staff_create_with_valid_branch_id(async_client: AsyncClient, db_session: AsyncSession):
    """super_admin can create a branch_manager linked to an existing branch."""
    branch_id = await _create_test_branch(db_session, name="Saddar Clinic")
    _, admin_headers = await create_user_helper(db_session, role=UserRole.super_admin)

    digits = _random_digits(7)
    payload = {
        "full_name": "Tariq Manager",
        "phone": f"+92311{digits}",
        "password": "Password123!",
        "email": f"tariq_{digits}@example.com",
        "role": "branch_manager",
        "branch_id": str(branch_id),
    }

    resp = await async_client.post("/api/v1/auth/admin/create-user", json=payload, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["role"] == "branch_manager"
    assert data["branch_id"] == str(branch_id)
    assert data["full_name"] == "Tariq Manager"


@pytest.mark.asyncio
async def test_staff_create_with_invalid_branch_id_rejected(async_client: AsyncClient, db_session: AsyncSession):
    """super_admin cannot create staff with a non-existent branch_id."""
    _, admin_headers = await create_user_helper(db_session, role=UserRole.super_admin)

    fake_branch_id = str(uuid.uuid4())
    digits = _random_digits(7)
    payload = {
        "full_name": "Invalid Branch Staff",
        "phone": f"+92312{digits}",
        "password": "Password123!",
        "email": f"invalid_{digits}@example.com",
        "role": "branch_manager",
        "branch_id": fake_branch_id,
    }

    resp = await async_client.post("/api/v1/auth/admin/create-user", json=payload, headers=admin_headers)
    assert resp.status_code == 400
    assert "Branch not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_staff_update_with_valid_branch_id(async_client: AsyncClient, db_session: AsyncSession):
    """super_admin can update existing staff to assign or change branch_id."""
    branch_id = await _create_test_branch(db_session, name="North Nazimabad Clinic")
    _, admin_headers = await create_user_helper(db_session, role=UserRole.super_admin)
    bm_user, _ = await create_user_helper(db_session, role=UserRole.branch_manager)
    bm_user_id = bm_user.id

    # Initially has no branch
    assert bm_user.branch_id is None

    # Update with branch_id
    update_payload = {"branch_id": str(branch_id)}
    resp = await async_client.put(
        f"/api/v1/auth/admin/users/{bm_user_id}", json=update_payload, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["branch_id"] == str(branch_id)


@pytest.mark.asyncio
async def test_staff_update_with_invalid_branch_id_rejected(async_client: AsyncClient, db_session: AsyncSession):
    """super_admin updating staff with non-existent branch_id returns 400."""
    _, admin_headers = await create_user_helper(db_session, role=UserRole.super_admin)
    bm_user, _ = await create_user_helper(db_session, role=UserRole.branch_manager)
    bm_user_id = bm_user.id

    fake_branch_id = str(uuid.uuid4())
    resp = await async_client.put(
        f"/api/v1/auth/admin/users/{bm_user_id}",
        json={"branch_id": fake_branch_id},
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert "Branch not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_staff_update_can_clear_branch_id(async_client: AsyncClient, db_session: AsyncSession):
    """super_admin can clear branch_id by passing null."""
    branch_id = await _create_test_branch(db_session)
    _, admin_headers = await create_user_helper(db_session, role=UserRole.super_admin)
    bm_user, _ = await create_user_helper(
        db_session, role=UserRole.branch_manager, branch_id=branch_id
    )
    bm_user_id = bm_user.id

    assert bm_user.branch_id == branch_id

    resp = await async_client.put(
        f"/api/v1/auth/admin/users/{bm_user_id}",
        json={"branch_id": None},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["branch_id"] is None


@pytest.mark.asyncio
async def test_staff_endpoints_restricted_to_super_admin(async_client: AsyncClient, db_session: AsyncSession):
    """branch_manager, patient, doctor cannot create or update staff users."""
    branch_id = await _create_test_branch(db_session)
    _, bm_headers = await create_user_helper(db_session, role=UserRole.branch_manager, branch_id=branch_id)
    _, patient_headers = await create_user_helper(db_session, role=UserRole.patient)
    target_user, _ = await create_user_helper(db_session, role=UserRole.branch_manager)
    target_user_id = target_user.id

    create_payload = {
        "full_name": "Rogue Staff",
        "phone": "+923009999999",
        "password": "Password123!",
        "role": "branch_manager",
    }
    update_payload = {"full_name": "Tampered Name"}

    # Attempt create
    for headers in [bm_headers, patient_headers]:
        resp = await async_client.post("/api/v1/auth/admin/create-user", json=create_payload, headers=headers)
        assert resp.status_code == 403

    # Attempt update
    for headers in [bm_headers, patient_headers]:
        resp = await async_client.put(f"/api/v1/auth/admin/users/{target_user_id}", json=update_payload, headers=headers)
        assert resp.status_code == 403

