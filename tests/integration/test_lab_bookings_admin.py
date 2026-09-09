"""
Integration tests for the new admin-facing lab endpoints:
  GET /lab-tests/bookings                    (staff-wide bookings list)
  PUT /lab-tests/bookings/{id}/status         (status transition, validated)
  GET /lab-tests/patients/search              (patient selector for report upload)
  GET /lab-tests?include_inactive=true        (catalog include_inactive toggle)
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.models.branch import Branch
from app.models.lab_booking import CollectionType, LabBooking, LabBookingStatus
from app.models.lab_test import LabTest
from app.models.user import UserRole
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import create_user_helper


async def _setup_booking(db_session: AsyncSession):
    branch = Branch(
        id=uuid.uuid4(),
        name="Bookings Test Branch",
        city="Karachi",
        address="Test Address",
        phone="+923000000001",
        working_hours={"mon": {"open": "09:00", "close": "21:00"}},
        services=["lab"],
        is_active=True,
    )
    db_session.add(branch)

    test = LabTest(
        id=uuid.uuid4(),
        name="Lipid Profile",
        code=f"LIPID-{uuid.uuid4().hex[:6]}",
        price=Decimal("2000.00"),
        branch_id=branch.id,
        is_active=True,
    )
    db_session.add(test)

    patient, patient_headers = await create_user_helper(db_session, role=UserRole.patient)

    booking = LabBooking(
        id=uuid.uuid4(),
        patient_id=patient.id,
        test_id=test.id,
        branch_id=branch.id,
        collection_type=CollectionType.clinic_visit,
        preferred_date=date.today() + timedelta(days=1),
        time_slot="morning",
        status=LabBookingStatus.pending,
        total_price=Decimal("2000.00"),
    )
    db_session.add(booking)
    await db_session.flush()

    return booking, branch, patient, patient_headers


@pytest.mark.asyncio
async def test_admin_bookings_list_is_role_gated_and_joins_patient(
    async_client: AsyncClient, db_session: AsyncSession
):
    booking, branch, patient, patient_headers = await _setup_booking(db_session)

    # Patient (owner) cannot use the admin-wide listing -- /bookings/my covers that case.
    resp = await async_client.get("/api/v1/lab-tests/bookings", headers=patient_headers)
    assert resp.status_code == 403

    for role in (UserRole.pharmacy_staff, UserRole.doctor):
        _, headers = await create_user_helper(db_session, role=role)
        resp = await async_client.get("/api/v1/lab-tests/bookings", headers=headers)
        assert resp.status_code == 403, f"{role} should be blocked, got {resp.status_code}"

    _, lab_staff_headers = await create_user_helper(db_session, role=UserRole.lab_staff)
    resp = await async_client.get("/api/v1/lab-tests/bookings", headers=lab_staff_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    match = next(item for item in body["items"] if item["id"] == str(booking.id))
    assert match["patient_name"] == patient.full_name
    assert match["patient_phone"] == patient.phone
    assert match["branch_name"] == branch.name

    # No trailing slash on this route -- confirm it isn't accidentally
    # collided with GET /{test_id} (would 422 on UUID parsing if broken).
    resp_no_slash = await async_client.get(
        "/api/v1/lab-tests/bookings", headers=lab_staff_headers, follow_redirects=False
    )
    assert resp_no_slash.status_code == 200


@pytest.mark.asyncio
async def test_admin_bookings_status_filter(async_client: AsyncClient, db_session: AsyncSession):
    booking, _branch, _patient, _patient_headers = await _setup_booking(db_session)
    _, lab_staff_headers = await create_user_helper(db_session, role=UserRole.lab_staff)

    resp = await async_client.get(
        "/api/v1/lab-tests/bookings",
        params={"status": "confirmed"},
        headers=lab_staff_headers,
    )
    assert resp.status_code == 200
    assert all(item["id"] != str(booking.id) for item in resp.json()["items"])

    resp = await async_client.get(
        "/api/v1/lab-tests/bookings",
        params={"status": "pending"},
        headers=lab_staff_headers,
    )
    assert resp.status_code == 200
    assert any(item["id"] == str(booking.id) for item in resp.json()["items"])


@pytest.mark.asyncio
async def test_booking_status_transition_valid_and_invalid(
    async_client: AsyncClient, db_session: AsyncSession
):
    booking, _branch, _patient, patient_headers = await _setup_booking(db_session)
    _, lab_staff_headers = await create_user_helper(db_session, role=UserRole.lab_staff)

    # Non-staff cannot update status.
    resp = await async_client.put(
        f"/api/v1/lab-tests/bookings/{booking.id}/status",
        json={"status": "confirmed"},
        headers=patient_headers,
    )
    assert resp.status_code == 403

    # pending -> completed is not a valid direct transition.
    resp = await async_client.put(
        f"/api/v1/lab-tests/bookings/{booking.id}/status",
        json={"status": "completed"},
        headers=lab_staff_headers,
    )
    assert resp.status_code == 400
    assert "confirmed" in resp.json()["detail"] or "cancelled" in resp.json()["detail"]

    # pending -> confirmed is valid.
    resp = await async_client.put(
        f"/api/v1/lab-tests/bookings/{booking.id}/status",
        json={"status": "confirmed"},
        headers=lab_staff_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"

    # confirmed -> confirmed (no-op transition) is not allowed either.
    resp = await async_client.put(
        f"/api/v1/lab-tests/bookings/{booking.id}/status",
        json={"status": "confirmed"},
        headers=lab_staff_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patient_search_role_gated_and_matches(
    async_client: AsyncClient, db_session: AsyncSession
):
    _booking, _branch, patient, _patient_headers = await _setup_booking(db_session)
    _, lab_staff_headers = await create_user_helper(db_session, role=UserRole.lab_staff)
    _, pharmacy_headers = await create_user_helper(db_session, role=UserRole.pharmacy_staff)

    resp = await async_client.get(
        "/api/v1/lab-tests/patients/search",
        params={"q": patient.full_name[:4]},
        headers=pharmacy_headers,
    )
    assert resp.status_code == 403

    resp = await async_client.get(
        "/api/v1/lab-tests/patients/search",
        params={"q": patient.full_name[:4]},
        headers=lab_staff_headers,
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()
    assert any(r["id"] == str(patient.id) for r in results)

    # Query below min_length is rejected.
    resp = await async_client.get(
        "/api/v1/lab-tests/patients/search", params={"q": "a"}, headers=lab_staff_headers
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_lab_tests_include_inactive_toggle(
    async_client: AsyncClient, db_session: AsyncSession
):
    branch = Branch(
        id=uuid.uuid4(),
        name="Inactive Test Branch",
        city="Karachi",
        address="Test Address",
        phone="+923000000002",
        working_hours={"mon": {"open": "09:00", "close": "21:00"}},
        services=["lab"],
        is_active=True,
    )
    db_session.add(branch)

    inactive_test = LabTest(
        id=uuid.uuid4(),
        name="Discontinued Test",
        code=f"DISC-{uuid.uuid4().hex[:6]}",
        price=Decimal("500.00"),
        branch_id=branch.id,
        is_active=False,
    )
    db_session.add(inactive_test)
    await db_session.flush()

    resp = await async_client.get("/api/v1/lab-tests")
    assert resp.status_code == 200
    codes = [item["code"] for item in resp.json()["items"]]
    assert inactive_test.code not in codes

    resp = await async_client.get("/api/v1/lab-tests", params={"include_inactive": "true"})
    assert resp.status_code == 200
    codes = [item["code"] for item in resp.json()["items"]]
    assert inactive_test.code in codes
