"""Doctors router — /api/v1/doctors"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select

from app.config import settings
from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.appointment import Appointment, AppointmentStatus
from app.models.doctor import Doctor, DoctorAvailability, DoctorBranch, DayOfWeek
from app.models.user import UserRole
from app.schemas.doctor import (
    AvailabilityCreate,
    AvailabilityResponse,
    DoctorBranchAssign,
    DoctorCreate,
    DoctorResponse,
    DoctorUpdate,
    SlotResponse,
)

router = APIRouter(prefix="/doctors", tags=["Doctors"])


@router.get(
    "/",
    response_model=list[DoctorResponse],
    summary="List active doctors (filter by branch, specialization)",
)
async def list_doctors(
    db: DBSession,
    branch_id: uuid.UUID | None = None,
    specialization: str | None = None,
):
    query = select(Doctor).where(Doctor.deleted_at.is_(None), Doctor.is_active == True)
    if specialization:
        query = query.where(Doctor.specialization.ilike(f"%{specialization}%"))
    if branch_id:
        query = query.join(DoctorBranch).where(DoctorBranch.branch_id == branch_id)

    result = await db.execute(query)
    doctors = result.scalars().all()
    return [DoctorResponse.model_validate(d) for d in doctors]


@router.get(
    "/{doctor_id}",
    response_model=DoctorResponse,
    summary="Get doctor profile",
)
async def get_doctor(doctor_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Doctor).where(Doctor.id == doctor_id, Doctor.deleted_at.is_(None))
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    return DoctorResponse.model_validate(doctor)


@router.get(
    "/{doctor_id}/slots",
    response_model=list[SlotResponse],
    summary="Get available 30-min slots for a doctor on a given date",
)
async def get_doctor_slots(
    doctor_id: uuid.UUID,
    date: str,  # YYYY-MM-DD
    branch_id: uuid.UUID,
    db: DBSession,
):
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    day_of_week = target_date.weekday()

    # Fetch availability windows for this doctor+branch+day
    avail_result = await db.execute(
        select(DoctorAvailability).where(
            DoctorAvailability.doctor_id == doctor_id,
            DoctorAvailability.branch_id == branch_id,
            DoctorAvailability.day_of_week == day_of_week,
            DoctorAvailability.is_active == True,
        )
    )
    availability_windows = avail_result.scalars().all()
    if not availability_windows:
        return []

    # Fetch existing booked slots for this doctor on this date
    day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    booked_result = await db.execute(
        select(Appointment.slot_datetime).where(
            Appointment.doctor_id == doctor_id,
            Appointment.slot_datetime >= day_start,
            Appointment.slot_datetime < day_end,
            Appointment.status.notin_([AppointmentStatus.cancelled, AppointmentStatus.no_show]),
        )
    )
    booked_slots = {row[0] for row in booked_result.all()}

    # Generate 30-min slots from each availability window
    slot_duration = timedelta(minutes=settings.APPOINTMENT_SLOT_DURATION_MINUTES)
    slots: list[SlotResponse] = []
    tz = timezone.utc

    for window in availability_windows:
        current = datetime.combine(target_date, window.start_time).replace(tzinfo=tz)
        end = datetime.combine(target_date, window.end_time).replace(tzinfo=tz)
        while current + slot_duration <= end:
            slots.append(
                SlotResponse(
                    slot_datetime=current,
                    is_available=current not in booked_slots,
                )
            )
            current += slot_duration

    return slots


@router.post(
    "/",
    response_model=DoctorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Create doctor profile",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def create_doctor(body: DoctorCreate, db: DBSession):
    doctor = Doctor(**body.model_dump())
    db.add(doctor)
    await db.flush()
    return DoctorResponse.model_validate(doctor)


@router.put(
    "/{doctor_id}",
    response_model=DoctorResponse,
    summary="[Admin/Doctor] Update doctor profile",
)
async def update_doctor(
    doctor_id: uuid.UUID,
    body: DoctorUpdate,
    current_user: CurrentUser,
    db: DBSession,
):
    result = await db.execute(
        select(Doctor).where(Doctor.id == doctor_id, Doctor.deleted_at.is_(None))
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    # Doctor can only update own profile; admins can update any
    if current_user.role == UserRole.doctor and doctor.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden.")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(doctor, field, value)
    return DoctorResponse.model_validate(doctor)


@router.delete(
    "/{doctor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Admin] Soft-delete doctor",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def delete_doctor(doctor_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Doctor).where(Doctor.id == doctor_id, Doctor.deleted_at.is_(None))
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    doctor.soft_delete()


@router.post(
    "/{doctor_id}/branches",
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Assign doctor to a branch",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def assign_branch(doctor_id: uuid.UUID, body: DoctorBranchAssign, db: DBSession):
    assoc = DoctorBranch(doctor_id=doctor_id, branch_id=body.branch_id, is_primary=body.is_primary)
    db.add(assoc)
    await db.flush()
    return {"doctor_id": doctor_id, "branch_id": body.branch_id, "is_primary": body.is_primary}


@router.post(
    "/{doctor_id}/availability",
    response_model=AvailabilityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin/Doctor] Set availability slot",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.doctor)],
)
async def set_availability(
    doctor_id: uuid.UUID, body: AvailabilityCreate, db: DBSession
):
    body.validate_times()
    avail = DoctorAvailability(
        doctor_id=doctor_id,
        branch_id=body.branch_id,
        day_of_week=body.day_of_week,
        start_time=body.start_time,
        end_time=body.end_time,
    )
    db.add(avail)
    await db.flush()
    return AvailabilityResponse.model_validate(avail)
