"""
Integration tests for lab report access control.

Regression coverage for the fix closing an authorization gap: previously
GET /lab-reports, GET /lab-reports/{id}, and GET /lab-reports/{id}/download
only checked ownership for the `patient` role, letting any other
authenticated role (e.g. pharmacy_staff, doctor) read or download ANY
patient's report by ID with no check at all. Access to another patient's
report metadata/download is now restricted to super_admin, branch_manager,
and lab_staff; a patient may only see their own visible reports.
"""
from __future__ import annotations

import uuid

import pytest
from app.models.branch import Branch
from app.models.lab_report import LabReport, ReportFileType
from app.models.lab_test import LabTest
from app.models.user import UserRole
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import create_user_helper


async def _setup_report(db_session: AsyncSession):
    """Create a branch, lab test, owning patient, and one visible report."""
    branch = Branch(
        id=uuid.uuid4(),
        name="Lab Reports Test Branch",
        city="Karachi",
        address="Test Address",
        phone="+923000000000",
        working_hours={"mon": {"open": "09:00", "close": "21:00"}},
        services=["lab"],
        is_active=True,
    )
    db_session.add(branch)

    test = LabTest(
        id=uuid.uuid4(),
        name="Complete Blood Count",
        code=f"CBC-{uuid.uuid4().hex[:6]}",
        price=1500,
        branch_id=branch.id,
        is_active=True,
    )
    db_session.add(test)

    owner, owner_headers = await create_user_helper(db_session, role=UserRole.patient)

    report = LabReport(
        id=uuid.uuid4(),
        patient_id=owner.id,
        test_id=test.id,
        file_path=f"reports/{owner.id}/test-report.pdf",
        file_type=ReportFileType.pdf,
        file_name="test-report.pdf",
        is_visible=True,
    )
    db_session.add(report)
    await db_session.flush()

    return report, owner, owner_headers


@pytest.mark.asyncio
async def test_download_url_blocked_for_unrelated_roles(
    async_client: AsyncClient, db_session: AsyncSession
):
    report, _owner, owner_headers = await _setup_report(db_session)

    # Owning patient can download their own visible report.
    resp = await async_client.get(
        f"/api/v1/lab-reports/{report.id}/download", headers=owner_headers
    )
    assert resp.status_code == 200, resp.text

    # An unrelated patient cannot.
    _, other_patient_headers = await create_user_helper(db_session, role=UserRole.patient)
    resp = await async_client.get(
        f"/api/v1/lab-reports/{report.id}/download", headers=other_patient_headers
    )
    assert resp.status_code == 403

    # Roles with no legitimate reason to see lab reports are blocked (this is
    # the fix -- previously these two returned 200 for ANY report id).
    for role in (UserRole.pharmacy_staff, UserRole.doctor):
        _, headers = await create_user_helper(db_session, role=role)
        resp = await async_client.get(
            f"/api/v1/lab-reports/{report.id}/download", headers=headers
        )
        assert resp.status_code == 403, f"{role} should be blocked, got {resp.status_code}"

    # Lab-authorized staff roles must NOT regress.
    for role in (UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff):
        _, headers = await create_user_helper(db_session, role=role)
        resp = await async_client.get(
            f"/api/v1/lab-reports/{report.id}/download", headers=headers
        )
        assert resp.status_code == 200, f"{role} should be allowed, got {resp.status_code}"


@pytest.mark.asyncio
async def test_report_detail_blocked_for_unrelated_roles(
    async_client: AsyncClient, db_session: AsyncSession
):
    report, _owner, owner_headers = await _setup_report(db_session)

    resp = await async_client.get(f"/api/v1/lab-reports/{report.id}", headers=owner_headers)
    assert resp.status_code == 200

    for role in (UserRole.pharmacy_staff, UserRole.doctor):
        _, headers = await create_user_helper(db_session, role=role)
        resp = await async_client.get(f"/api/v1/lab-reports/{report.id}", headers=headers)
        assert resp.status_code == 403, f"{role} should be blocked, got {resp.status_code}"

    _, lab_staff_headers = await create_user_helper(db_session, role=UserRole.lab_staff)
    resp = await async_client.get(f"/api/v1/lab-reports/{report.id}", headers=lab_staff_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_report_list_blocked_for_unrelated_roles(
    async_client: AsyncClient, db_session: AsyncSession
):
    _report, _owner, owner_headers = await _setup_report(db_session)

    # Patient sees only their own visible reports.
    resp = await async_client.get("/api/v1/lab-reports", headers=owner_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1

    # pharmacy_staff/doctor previously got every patient's reports back
    # (no role gate at all on the non-patient branch) -- now blocked.
    for role in (UserRole.pharmacy_staff, UserRole.doctor):
        _, headers = await create_user_helper(db_session, role=role)
        resp = await async_client.get("/api/v1/lab-reports", headers=headers)
        assert resp.status_code == 403, f"{role} should be blocked, got {resp.status_code}"

    _, lab_staff_headers = await create_user_helper(db_session, role=UserRole.lab_staff)
    resp = await async_client.get("/api/v1/lab-reports", headers=lab_staff_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_admin_report_list_joins_patient_and_is_role_gated(
    async_client: AsyncClient, db_session: AsyncSession
):
    _report, owner, _owner_headers = await _setup_report(db_session)

    _, patient_headers = await create_user_helper(db_session, role=UserRole.patient)
    resp = await async_client.get("/api/v1/lab-reports/admin", headers=patient_headers)
    assert resp.status_code == 403

    _, lab_staff_headers = await create_user_helper(db_session, role=UserRole.lab_staff)
    resp = await async_client.get(
        "/api/v1/lab-reports/admin",
        params={"patient_id": str(owner.id)},
        headers=lab_staff_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["patient_name"] == owner.full_name
    assert body["items"][0]["patient_phone"] == owner.phone
