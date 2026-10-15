"""Unit and integration tests for secure signed lab report access links."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.branch import Branch
from app.models.lab_report import LabReport, ReportFileType
from app.models.lab_test import LabTest
from app.models.user import User, UserRole
from app.utils.report_security import (
    TokenStatus,
    generate_report_access_token,
    generate_report_access_url,
    inspect_report_access_token,
)
from app.utils.security import hash_password

SECRET = "test-secret-report-link-key-min-32chars"


@pytest.fixture(autouse=True)
def _report_security_env(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_LINK_SECRET", SECRET)
    monkeypatch.setattr(settings, "REPORT_LINK_TTL_HOURS", 48)
    monkeypatch.setattr(settings, "API_PUBLIC_URL", "http://test")
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    import app.utils.storage as st_mod
    monkeypatch.setattr(st_mod, "_storage_instance", None)


def test_token_valid_and_inspect():
    report_id = uuid.uuid4()
    patient_id = uuid.uuid4()

    token = generate_report_access_token(report_id, patient_id, ttl_hours=24)
    status, extracted_report_id, extracted_patient_id = inspect_report_access_token(token)

    assert status == TokenStatus.VALID
    assert extracted_report_id == report_id
    assert extracted_patient_id == patient_id


def test_token_tampered_signature():
    report_id = uuid.uuid4()
    patient_id = uuid.uuid4()

    token = generate_report_access_token(report_id, patient_id)
    # Tamper with signature part
    parts = token.split(".")
    tampered_sig = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
    tampered_token = f"{parts[0]}.{tampered_sig}"

    status, r_id, p_id = inspect_report_access_token(tampered_token)
    assert status == TokenStatus.INVALID
    assert r_id is None


def test_token_expired():
    report_id = uuid.uuid4()
    patient_id = uuid.uuid4()

    # Expired 1 hour ago
    token = generate_report_access_token(report_id, patient_id, ttl_hours=-1)
    status, r_id, p_id = inspect_report_access_token(token)

    assert status == TokenStatus.EXPIRED
    assert r_id == report_id
    assert p_id == patient_id


async def test_endpoint_valid_token_redirect_and_headers(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    patient = User(
        id=uuid.uuid4(),
        full_name="Report Patient",
        phone="+923001239999",
        email="report_patient@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    test = LabTest(
        id=uuid.uuid4(),
        name="Complete Blood Count",
        code=f"CBC-{uuid.uuid4().hex[:4]}",
        price=1200.0,
        is_active=True,
    )
    db_session.add_all([patient, test])
    await db_session.flush()

    report = LabReport(
        id=uuid.uuid4(),
        patient_id=patient.id,
        test_id=test.id,
        file_path="reports/test/cbc.pdf",
        file_type=ReportFileType.pdf,
        file_name="cbc.pdf",
        is_visible=True,
        uploaded_by=patient.id,
    )
    db_session.add(report)
    await db_session.flush()

    token = generate_report_access_token(report.id, patient.id, ttl_hours=48)

    # 1. Access via /api/v1/lab/reports/access/{token}
    res = await async_client.get(
        f"/api/v1/lab/reports/access/{token}",
        follow_redirects=False,
    )
    assert res.status_code == 307
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert "location" in res.headers
    assert "cbc.pdf" in res.headers["location"] or res.headers["location"] != ""

    # 2. Access via alias /api/v1/lab-reports/access/{token}
    res_alias = await async_client.get(
        f"/api/v1/lab-reports/access/{token}",
        follow_redirects=False,
    )
    assert res_alias.status_code == 307

    # 3. Multiple views allowed (not one-time)
    res_again = await async_client.get(
        f"/api/v1/lab/reports/access/{token}",
        follow_redirects=False,
    )
    assert res_again.status_code == 307


async def test_endpoint_expired_token_returns_410(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    report_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    expired_token = generate_report_access_token(report_id, patient_id, ttl_hours=-2)

    res = await async_client.get(
        f"/api/v1/lab/reports/access/{expired_token}",
        follow_redirects=False,
    )
    assert res.status_code == 410
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["referrer-policy"] == "no-referrer"


async def test_endpoint_tampered_token_returns_404(async_client: AsyncClient):
    res = await async_client.get(
        "/api/v1/lab/reports/access/bad_payload.bad_signature",
        follow_redirects=False,
    )
    assert res.status_code == 404
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["referrer-policy"] == "no-referrer"


async def test_endpoint_hidden_report_returns_404(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    patient = User(
        id=uuid.uuid4(),
        full_name="Hidden Report Patient",
        phone="+923001238888",
        email="hidden_patient@example.com",
        hashed_password=hash_password("Pass123!"),
        role=UserRole.patient,
        is_active=True,
    )
    test = LabTest(
        id=uuid.uuid4(),
        name="Lipid Profile",
        code=f"LIP-{uuid.uuid4().hex[:4]}",
        price=1800.0,
        is_active=True,
    )
    db_session.add_all([patient, test])
    await db_session.flush()

    hidden_report = LabReport(
        id=uuid.uuid4(),
        patient_id=patient.id,
        test_id=test.id,
        file_path="reports/test/hidden.pdf",
        file_type=ReportFileType.pdf,
        file_name="hidden.pdf",
        is_visible=False,  # Hidden!
        uploaded_by=patient.id,
    )
    db_session.add(hidden_report)
    await db_session.flush()

    token = generate_report_access_token(hidden_report.id, patient.id, ttl_hours=24)

    res = await async_client.get(
        f"/api/v1/lab/reports/access/{token}",
        follow_redirects=False,
    )
    assert res.status_code == 404
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["referrer-policy"] == "no-referrer"
