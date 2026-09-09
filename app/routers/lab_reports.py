"""
Lab reports router — /api/v1/lab-reports

Endpoints:
  POST   /upload         → [Staff/Admin/Lab Staff] Upload report (PDF/JPEG/PNG, max 10MB)
  GET    /               → List reports (patient-scoped or lab-staff-wide)
  GET    /admin           → [Staff/Admin] List reports with patient name/phone joined
  GET    /{id}           → Report metadata (no public URL exposed)
  GET    /{id}/download  → Short-lived presigned download URL (10 min expiry)
  PUT    /{id}           → [Staff/Admin] Update notes / visibility
  DELETE /{id}           → [Staff/Admin] Delete report record and storage file

Access to any report's metadata/download (list, detail, or download) beyond a
patient's own visible reports is restricted to super_admin/branch_manager/lab_staff.
"""
from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.lab_report import LabReport
from app.models.user import User, UserRole
from app.schemas.lab_report import (
    LabReportAdminListResponse,
    LabReportAdminResponse,
    LabReportDownloadResponse,
    LabReportListResponse,
    LabReportResponse,
    LabReportUpdate,
)
from app.services.report_service import ReportService
from app.utils.storage import get_storage

router = APIRouter(prefix="/lab-reports", tags=["Lab Reports"])


def _build_report_response(r: LabReport) -> LabReportResponse:
    return LabReportResponse(
        id=r.id,
        patient_id=r.patient_id,
        test_id=r.test_id,
        test_name=r.test.name if r.test else None,
        test_code=r.test.code if r.test else None,
        appointment_id=r.appointment_id,
        file_name=r.file_name,
        file_type=r.file_type,
        is_visible=r.is_visible,
        uploaded_by=r.uploaded_by,
        notes=r.notes,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.post(
    "/upload",
    response_model=LabReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Staff/Admin/Lab Staff] Upload a lab report file",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def upload_lab_report(
    patient_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    test_id: uuid.UUID | None = Form(None),
    appointment_id: uuid.UUID | None = Form(None),
    notes: str | None = Form(None),
    current_user: CurrentUser = None,
    db: DBSession = None,
):
    service = ReportService(db)
    return await service.upload_report(
        patient_id=patient_id,
        file=file,
        uploaded_by=current_user,
        test_id=test_id,
        appointment_id=appointment_id,
        notes=notes,
    )


@router.get(
    "",
    response_model=LabReportListResponse,
    summary="List lab reports (patients see own visible reports; staff see all)",
)
async def list_lab_reports(
    current_user: CurrentUser,
    db: DBSession,
    patient_id: uuid.UUID | None = None,
    test_id: uuid.UUID | None = None,
    appointment_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = select(LabReport).options(selectinload(LabReport.test))

    if current_user.role == UserRole.patient:
        query = query.where(
            LabReport.patient_id == current_user.id,
            LabReport.is_visible.is_(True),
        )
    elif current_user.role in (UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff):
        if patient_id:
            query = query.where(LabReport.patient_id == patient_id)
    else:
        raise HTTPException(status_code=403, detail="Not authorized.")

    if test_id:
        query = query.where(LabReport.test_id == test_id)
    if appointment_id:
        query = query.where(LabReport.appointment_id == appointment_id)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * limit
    items_query = query.order_by(LabReport.created_at.desc()).offset(offset).limit(limit)
    items_result = await db.execute(items_query)
    reports = items_result.scalars().all()

    pages = math.ceil(total / limit) if limit > 0 else 1

    return LabReportListResponse(
        items=[_build_report_response(r) for r in reports],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/admin",
    response_model=LabReportAdminListResponse,
    summary="[Admin/Lab Staff] List lab reports with patient name/phone joined",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def list_lab_reports_admin(
    db: DBSession,
    patient_id: uuid.UUID | None = None,
    test_id: uuid.UUID | None = None,
    appointment_id: uuid.UUID | None = None,
    is_visible: bool | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = (
        select(LabReport, User)
        .join(User, LabReport.patient_id == User.id)
        .options(selectinload(LabReport.test))
    )
    if patient_id:
        query = query.where(LabReport.patient_id == patient_id)
    if test_id:
        query = query.where(LabReport.test_id == test_id)
    if appointment_id:
        query = query.where(LabReport.appointment_id == appointment_id)
    if is_visible is not None:
        query = query.where(LabReport.is_visible == is_visible)

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * limit
    result = await db.execute(
        query.order_by(LabReport.created_at.desc()).offset(offset).limit(limit)
    )
    rows = result.all()  # (LabReport, User) tuples

    pages = math.ceil(total / limit) if limit > 0 else 1

    return LabReportAdminListResponse(
        items=[
            LabReportAdminResponse(
                **_build_report_response(r).model_dump(),
                patient_name=u.full_name,
                patient_phone=u.phone,
            )
            for r, u in rows
        ],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/{report_id}",
    response_model=LabReportResponse,
    summary="Get report metadata",
)
async def get_lab_report(
    report_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
):
    result = await db.execute(
        select(LabReport)
        .options(selectinload(LabReport.test))
        .where(LabReport.id == report_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Lab report not found.")

    if current_user.role == UserRole.patient:
        if report.patient_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized.")
        if not report.is_visible:
            raise HTTPException(status_code=403, detail="Report is not available.")
    elif current_user.role not in (UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff):
        raise HTTPException(status_code=403, detail="Not authorized.")

    return _build_report_response(report)


@router.get(
    "/{report_id}/download",
    response_model=LabReportDownloadResponse,
    summary="Generate short-lived signed URL (10 min expiry) for report download",
)
async def download_lab_report(
    report_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
):
    service = ReportService(db)
    return await service.get_download_url(report_id=report_id, user=current_user, expires_in=600)


@router.put(
    "/{report_id}",
    response_model=LabReportResponse,
    summary="[Staff/Admin] Update report visibility or notes",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def update_lab_report(
    report_id: uuid.UUID,
    body: LabReportUpdate,
    db: DBSession,
):
    result = await db.execute(select(LabReport).where(LabReport.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Lab report not found.")

    if body.is_visible is not None:
        report.is_visible = body.is_visible
    if body.notes is not None:
        report.notes = body.notes

    await db.flush()
    return LabReportResponse.model_validate(report)


@router.delete(
    "/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Staff/Admin] Delete report record and underlying stored file",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def delete_lab_report(
    report_id: uuid.UUID,
    db: DBSession,
):
    result = await db.execute(select(LabReport).where(LabReport.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Lab report not found.")

    # Delete from storage backend
    storage = get_storage()
    await storage.delete(report.file_path)

    # Delete from database
    await db.delete(report)
    await db.flush()
