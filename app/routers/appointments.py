"""
Appointments router — /api/v1/appointments

Endpoints:
  GET  /slots          → Available 30-min slots for doctor+branch+date
  POST /               → Book appointment (with deadlock/race condition lock)
  GET  /               → List appointments (role-scoped)
  GET  /{id}           → Appointment detail
  PUT  /{id}/status    → Update appointment status (confirm / complete / cancel)
"""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.dependencies import CurrentUser, DBSession
from app.models.appointment import Appointment, AppointmentStatus
from app.models.doctor import Doctor
from app.models.user import UserRole
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentListResponse,
    AppointmentResponse,
    AppointmentStatusUpdate,
    SlotResponse,
)
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/appointments", tags=["Appointments"])


@router.get(
    "/slots",
    response_model=list[SlotResponse],
    summary="Get available 30-minute slots for a doctor at a branch on a date",
)
async def get_doctor_slots(
    doctor_id: uuid.UUID,
    branch_id: uuid.UUID,
    slot_date: str = Query(..., regex=r"^\d{4}-\d{2}-\d{2}$", description="Target date (YYYY-MM-DD)"),
    db: DBSession = None,
):
    try:
        target_date = datetime.strptime(slot_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    service = AppointmentService(db)
    return await service.get_available_slots(doctor_id, branch_id, target_date)


@router.post(
    "",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Book a doctor appointment",
)
async def book_appointment(
    body: AppointmentCreate,
    current_user: CurrentUser,
    db: DBSession,
):
    service = AppointmentService(db)
    return await service.book_appointment(patient=current_user, body=body)


@router.get(
    "",
    response_model=AppointmentListResponse,
    summary="List appointments (patients see own; doctors see schedule; staff see all)",
)
async def list_appointments(
    current_user: CurrentUser,
    db: DBSession,
    status_filter: AppointmentStatus | None = Query(None, alias="status"),
    branch_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = (
        select(Appointment)
        .options(
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.branch),
        )
    )

    # Role scoping
    if current_user.role == UserRole.patient:
        query = query.where(Appointment.patient_id == current_user.id)
    elif current_user.role == UserRole.doctor:
        # Match doctor profile
        doc_res = await db.execute(select(Doctor.id).where(Doctor.user_id == current_user.id))
        user_doctor_id = doc_res.scalar_one_or_none()
        if user_doctor_id:
            query = query.where(Appointment.doctor_id == user_doctor_id)
        else:
            return AppointmentListResponse(items=[], total=0, page=page, limit=limit, pages=0)

    # Optional query filters for staff/admin
    if status_filter:
        query = query.where(Appointment.status == status_filter)
    if branch_id:
        query = query.where(Appointment.branch_id == branch_id)
    if doctor_id and current_user.role != UserRole.doctor:
        query = query.where(Appointment.doctor_id == doctor_id)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * limit
    items_query = query.order_by(Appointment.slot_datetime.desc()).offset(offset).limit(limit)
    items_result = await db.execute(items_query)
    appointments = items_result.scalars().all()

    service = AppointmentService(db)
    items = [service._build_response(a, a.doctor, a.branch) for a in appointments]
    pages = math.ceil(total / limit) if limit > 0 else 1

    return AppointmentListResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/{appointment_id}",
    response_model=AppointmentResponse,
    summary="Get appointment details",
)
async def get_appointment(
    appointment_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
):
    result = await db.execute(
        select(Appointment)
        .where(Appointment.id == appointment_id)
        .options(
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.branch),
        )
    )
    appointment = result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    # Access check
    if current_user.role == UserRole.patient and appointment.patient_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized.")

    service = AppointmentService(db)
    return service._build_response(appointment, appointment.doctor, appointment.branch)


@router.put(
    "/{appointment_id}/status",
    response_model=AppointmentResponse,
    summary="Update appointment status (confirm, complete, cancel)",
)
async def update_appointment_status(
    appointment_id: uuid.UUID,
    body: AppointmentStatusUpdate,
    current_user: CurrentUser,
    db: DBSession,
):
    service = AppointmentService(db)
    return await service.update_status(
        appointment_id=appointment_id,
        body=body,
        actor=current_user,
    )
