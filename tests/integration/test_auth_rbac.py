"""
Integration tests for Auth flow and RBAC authorization guards.
"""
import pytest
from app.models.user import UserRole
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import create_user_helper


@pytest.mark.asyncio
async def test_auth_registration_and_login_flow(async_client: AsyncClient, db_session: AsyncSession):
    phone = "+923001234567"
    password = "SecurePassword123!"

    # 1. Register
    reg_payload = {
        "phone": phone,
        "full_name": "Hamza Ali",
        "email": "hamza@example.com",
        "password": password,
    }
    reg_resp = await async_client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_resp.status_code == 201, reg_resp.text
    body = reg_resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    user_data = body["user"]
    assert user_data["phone"] == phone
    assert user_data["full_name"] == "Hamza Ali"

    # 2. Login
    login_payload = {
        "phone": phone,
        "password": password,
    }
    login_resp = await async_client.post("/api/v1/auth/login", json=login_payload)
    assert login_resp.status_code == 200, login_resp.text
    tokens = login_resp.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]

    # 3. Access Protected Route /auth/me
    auth_headers = {"Authorization": f"Bearer {access_token}"}
    me_resp = await async_client.get("/api/v1/auth/me", headers=auth_headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["phone"] == phone

    # 4. Refresh Token
    refresh_resp = await async_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_resp.status_code == 200
    new_tokens = refresh_resp.json()
    assert "access_token" in new_tokens
    assert "refresh_token" in new_tokens


@pytest.mark.asyncio
async def test_rbac_guard_patient_cannot_create_branch(async_client: AsyncClient, db_session: AsyncSession):
    # Create patient user
    _, patient_headers = await create_user_helper(db_session, role=UserRole.patient)

    # Attempt to create branch (requires super_admin)
    branch_payload = {
        "name": "Clifton Medical Center",
        "city": "Karachi",
        "address": "Block 5, Clifton",
        "phone": "+922131234567",
        "working_hours": {"mon": {"open": "08:00", "close": "22:00"}},
        "services": ["lab", "pharmacy", "clinics"],
    }
    resp = await async_client.post("/api/v1/branches", json=branch_payload, headers=patient_headers)
    assert resp.status_code == 403, f"Expected 403 Forbidden but got {resp.status_code}"


@pytest.mark.asyncio
async def test_rbac_guard_admin_can_create_branch(async_client: AsyncClient, db_session: AsyncSession):
    # Create admin user
    _, admin_headers = await create_user_helper(db_session, role=UserRole.super_admin)

    branch_payload = {
        "name": "Gulshan Diagnostic Center",
        "city": "Karachi",
        "address": "Block 13, Gulshan-e-Iqbal",
        "phone": "+922139876543",
        "working_hours": {"mon": {"open": "00:00", "close": "23:59"}},
        "services": ["lab", "pharmacy"],
    }
    resp = await async_client.post("/api/v1/branches", json=branch_payload, headers=admin_headers)
    assert resp.status_code == 201
    assert resp.json()["name"] == "Gulshan Diagnostic Center"
