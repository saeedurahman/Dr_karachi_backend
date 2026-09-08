"""Doctors router — /api/v1/doctors"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from datetime import date as Date

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.appointment import Appointment, AppointmentStatus
from app.models.doctor import DayOfWeek, Doctor, DoctorAvailability, DoctorBranch
from app.models.user import UserRole
from app.schemas.doctor import (
    BatchAvailabilityUpdate,
    AvailabilityCreate,
    AvailabilityResponse,
    BranchSummary,
    DoctorBranchAssign,
    DoctorCreate,
    DoctorResponse,
    DoctorUpdate,
    SlotResponse,
)


def _build_doctor_response(doctor: Doctor) -> DoctorResponse:
    """Build a DoctorResponse with derived full_name and branches fields."""
    full_name = doctor.user.full_name if doctor.user else ""
    branches = [
        BranchSummary.model_validate(doc_branch.branch)
        for doc_branch in (doctor.doctor_branches or [])
        if doc_branch.branch is not None
    ]
    return DoctorResponse(
        id=doctor.id,
        user_id=doctor.user_id,
        full_name=full_name,
        specialization=doctor.specialization,
        qualification=doctor.qualification,
        experience_years=doctor.experience_years,
        consultation_fee=doctor.consultation_fee,
        bio=doctor.bio,
        profile_image_url=doctor.profile_image_url,
        is_active=doctor.is_active,
        branches=branches,
        created_at=doctor.created_at,
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
    include_inactive: bool = False,
):
    query = (
        select(Doctor)
        .where(Doctor.deleted_at.is_(None))
        .options(
            selectinload(Doctor.user),
            selectinload(Doctor.doctor_branches).selectinload(DoctorBranch.branch),
        )
    )
    if not include_inactive:
        query = query.where(Doctor.is_active == True)
    if specialization:
        query = query.where(Doctor.specialization.ilike(f"%{specialization}%"))
    if branch_id:
        query = query.join(DoctorBranch).where(DoctorBranch.branch_id == branch_id)

    result = await db.execute(query)
    doctors = result.scalars().all()
    return [_build_doctor_response(d) for d in doctors]


@router.get(
    "/{doctor_id}",
    response_model=DoctorResponse,
    summary="Get doctor profile",
)
async def get_doctor(doctor_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Doctor)
        .where(Doctor.id == doctor_id, Doctor.deleted_at.is_(None))
        .options(
            selectinload(Doctor.user),
            selectinload(Doctor.doctor_branches).selectinload(DoctorBranch.branch),
        )
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    return _build_doctor_response(doctor)


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
        target_date = Date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    day_of_week = DayOfWeek(target_date.weekday())

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
    day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=UTC)
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
    tz = UTC

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
    res = await db.execute(
        select(Doctor)
        .where(Doctor.id == doctor.id)
        .options(
            selectinload(Doctor.user),
            selectinload(Doctor.doctor_branches).selectinload(DoctorBranch.branch),
        )
    )
    loaded = res.scalar_one()
    return _build_doctor_response(loaded)


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
    await db.flush()
    res = await db.execute(
        select(Doctor)
        .where(Doctor.id == doctor.id)
        .options(
            selectinload(Doctor.user),
            selectinload(Doctor.doctor_branches).selectinload(DoctorBranch.branch),
        )
    )
    loaded = res.scalar_one()
    return _build_doctor_response(loaded)


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


@router.get(
    "/{doctor_id}/availability",
    response_model=list[AvailabilityResponse],
    summary="[Admin/Doctor] Get doctor availability schedule",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.doctor)],
)
async def get_doctor_availability(
    doctor_id: uuid.UUID,
    db: DBSession,
    branch_id: uuid.UUID | None = None,
):
    query = select(DoctorAvailability).where(
        DoctorAvailability.doctor_id == doctor_id,
        DoctorAvailability.is_active == True,
    )
    if branch_id:
        query = query.where(DoctorAvailability.branch_id == branch_id)
    result = await db.execute(
        query.order_by(DoctorAvailability.day_of_week, DoctorAvailability.start_time)
    )
    return [AvailabilityResponse.model_validate(a) for a in result.scalars().all()]


@router.put(
    "/{doctor_id}/availability",
    response_model=list[AvailabilityResponse],
    summary="[Admin/Doctor] Set weekly availability schedule for a branch",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.doctor)],
)
async def update_doctor_availability(
    doctor_id: uuid.UUID,
    body: BatchAvailabilityUpdate,
    current_user: CurrentUser,
    db: DBSession,
):
    # Role check: doctor can only update own availability
    if current_user.role == UserRole.doctor:
        doc_res = await db.execute(select(Doctor.id).where(Doctor.user_id == current_user.id))
        if doc_res.scalar_one_or_none() != doctor_id:
            raise HTTPException(status_code=403, detail="Forbidden.")

    # Delete existing active availability for this doctor and branch
    await db.execute(
        delete(DoctorAvailability).where(
            DoctorAvailability.doctor_id == doctor_id,
            DoctorAvailability.branch_id == body.branch_id,
        )
    )

    new_slots = []
    for slot in body.slots:
        slot.validate_times()
        avail = DoctorAvailability(
            doctor_id=doctor_id,
            branch_id=body.branch_id,
            day_of_week=slot.day_of_week,
            start_time=slot.start_time,
            end_time=slot.end_time,
        )
        db.add(avail)
        new_slots.append(avail)

    await db.flush()
    return [AvailabilityResponse.model_validate(a) for a in new_slots]
