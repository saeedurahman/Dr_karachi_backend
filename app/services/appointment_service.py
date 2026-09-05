"""
Appointment service — handles slot generation and deadlock/race-free booking.

Key invariants:
1. Slot generation strictly scopes DoctorAvailability by BOTH doctor_id AND branch_id.
2. Booking acquires a SELECT ... FOR UPDATE lock on the Doctor row to serialize concurrent
   booking attempts for that doctor.
3. Explicit conflict check raises 409 Conflict if an active booking already exists.
4. Emits appointment_confirmed and appointment_cancelled notification events.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.appointment import Appointment, AppointmentStatus
from app.models.branch import Branch
from app.models.doctor import DayOfWeek, Doctor, DoctorAvailability
from app.models.notification import NotificationEventType
from app.models.user import User, UserRole
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentStatusUpdate,
    BranchSummary,
    DoctorSummary,
    SlotResponse,
)
from app.services.notification_service import emit_event


class AppointmentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── 1. Slot Generation ───────────────────────────────────────────────────────
    async def get_available_slots(
        self,
        doctor_id: uuid.UUID,
        branch_id: uuid.UUID,
        target_date: date,
    ) -> list[SlotResponse]:
        """
        Generate 30-min slots for a doctor on a given date at a specific branch.
        Availability is strictly filtered by BOTH doctor_id AND branch_id.
        """
        day_of_week = target_date.weekday()

        # 1. Fetch doctor availability windows strictly for this doctor AND this branch
        avail_result = await self.db.execute(
            select(DoctorAvailability).where(
                DoctorAvailability.doctor_id == doctor_id,
                DoctorAvailability.branch_id == branch_id,
                DoctorAvailability.day_of_week == day_of_week,
                DoctorAvailability.is_active.is_(True),
            )
        )
        windows = avail_result.scalars().all()
        if not windows:
            return []

        # 2. Fetch active booked appointments for this doctor on that target date
        day_start = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        booked_result = await self.db.execute(
            select(Appointment.slot_datetime).where(
                Appointment.doctor_id == doctor_id,
                Appointment.slot_datetime >= day_start,
                Appointment.slot_datetime < day_end,
                Appointment.status.notin_([
                    AppointmentStatus.cancelled,
                    AppointmentStatus.no_show,
                ]),
            )
        )
        booked_datetimes = {row[0] for row in booked_result.all()}

        # 3. Generate 30-min intervals
        slot_duration = timedelta(minutes=settings.APPOINTMENT_SLOT_DURATION_MINUTES)
        slots: list[SlotResponse] = []
        tz = timezone.utc

        for win in windows:
            current = datetime.combine(target_date, win.start_time).replace(tzinfo=tz)
            end = datetime.combine(target_date, win.end_time).replace(tzinfo=tz)
            while current + slot_duration <= end:
                slots.append(
                    SlotResponse(
                        slot_datetime=current,
                        is_available=(current not in booked_datetimes),
                    )
                )
                current += slot_duration

        # Sort slots chronologically
        slots.sort(key=lambda s: s.slot_datetime)
        return slots

    # ── 2. Book Appointment (Concurrency & Conflict Safe) ────────────────────────
    async def book_appointment(
        self,
        patient: User,
        body: AppointmentCreate,
    ) -> AppointmentResponse:
        """
        Book an appointment.
        Uses row-level locking on the Doctor record (SELECT ... FOR UPDATE) to prevent
        race conditions when two patients simultaneously try to book the exact same slot.
        """
        # Ensure slot_datetime is in UTC
        slot_dt = body.slot_datetime
        if slot_dt.tzinfo is None:
            slot_dt = slot_dt.replace(tzinfo=timezone.utc)

        # 1. Acquire row-level lock on the Doctor
        doc_result = await self.db.execute(
            select(Doctor)
            .where(Doctor.id == body.doctor_id, Doctor.deleted_at.is_(None))
            .options(selectinload(Doctor.user))
            .with_for_update()
        )
        doctor = doc_result.scalar_one_or_none()
        if not doctor or not doctor.is_active:
            raise HTTPException(status_code=404, detail="Doctor not found or inactive.")

        # 2. Verify branch exists
        branch_result = await self.db.execute(
            select(Branch).where(Branch.id == body.branch_id, Branch.is_active.is_(True))
        )
        branch = branch_result.scalar_one_or_none()
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found or inactive.")

        # 3. Verify doctor availability window covers this slot
        target_date = slot_dt.date()
        target_time = slot_dt.time()
        slot_duration = timedelta(minutes=settings.APPOINTMENT_SLOT_DURATION_MINUTES)
        slot_end_time = (slot_dt + slot_duration).time()

        avail_result = await self.db.execute(
            select(DoctorAvailability).where(
                DoctorAvailability.doctor_id == body.doctor_id,
                DoctorAvailability.branch_id == body.branch_id,
                DoctorAvailability.day_of_week == target_date.weekday(),
                DoctorAvailability.start_time <= target_time,
                DoctorAvailability.end_time >= slot_end_time,
                DoctorAvailability.is_active.is_(True),
            )
        )
        availability_match = avail_result.scalar_one_or_none()
        if not availability_match:
            raise HTTPException(
                status_code=400,
                detail="Doctor is not available at the selected branch during this time slot.",
            )

        # 4. Check for active existing appointment at this exact slot (Conflict Check)
        existing_result = await self.db.execute(
            select(Appointment)
            .where(
                Appointment.doctor_id == body.doctor_id,
                Appointment.slot_datetime == slot_dt,
                Appointment.status.notin_([
                    AppointmentStatus.cancelled,
                    AppointmentStatus.no_show,
                ]),
            )
            .with_for_update()
        )
        existing_appointment = existing_result.scalar_one_or_none()
        if existing_appointment:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This slot has already been booked. Please select a different slot.",
            )

        # 5. Create Appointment
        appointment = Appointment(
            patient_id=patient.id,
            doctor_id=body.doctor_id,
            branch_id=body.branch_id,
            slot_datetime=slot_dt,
            status=AppointmentStatus.confirmed,
            notes=body.notes,
        )
        self.db.add(appointment)
        await self.db.flush()

        # 6. Emit event
        await emit_event(
            db=self.db,
            event_type=NotificationEventType.appointment_confirmed,
            payload={
                "appointment_id": str(appointment.id),
                "patient_id": str(patient.id),
                "doctor_name": doctor.user.full_name if doctor.user else "Doctor",
                "branch_name": branch.name,
                "slot_datetime": slot_dt.isoformat(),
            },
            user_id=patient.id,
            delivery_channel="whatsapp",
        )

        return self._build_response(appointment, doctor, branch)

    # ── 3. Status Transition ───────────────────────────────────────────────────
    async def update_status(
        self,
        appointment_id: uuid.UUID,
        body: AppointmentStatusUpdate,
        actor: User,
    ) -> AppointmentResponse:
        result = await self.db.execute(
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

        # If patient, they can only cancel their own upcoming appointment
        if actor.role == UserRole.patient:
            if appointment.patient_id != actor.id:
                raise HTTPException(status_code=403, detail="Not authorized.")
            if body.status != AppointmentStatus.cancelled:
                raise HTTPException(
                    status_code=400,
                    detail="Patients can only cancel appointments.",
                )

        if body.status == AppointmentStatus.cancelled:
            if not body.cancellation_reason:
                raise HTTPException(
                    status_code=400,
                    detail="cancellation_reason is required when cancelling.",
                )
            appointment.cancellation_reason = body.cancellation_reason

        appointment.status = body.status
        await self.db.flush()

        if body.status == AppointmentStatus.cancelled:
            await emit_event(
                db=self.db,
                event_type=NotificationEventType.appointment_cancelled,
                payload={
                    "appointment_id": str(appointment.id),
                    "patient_id": str(appointment.patient_id),
                    "reason": appointment.cancellation_reason,
                },
                user_id=appointment.patient_id,
                delivery_channel="whatsapp",
            )

        return self._build_response(appointment, appointment.doctor, appointment.branch)

    # ── Helper ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _build_response(
        appointment: Appointment,
        doctor: Doctor | None,
        branch: Branch | None,
    ) -> AppointmentResponse:
        doc_summary = None
        if doctor and doctor.user:
            doc_summary = DoctorSummary(
                id=doctor.id,
                name=doctor.user.full_name,
                specialization=doctor.specialization,
                consultation_fee=str(doctor.consultation_fee),
            )

        branch_summary = None
        if branch:
            branch_summary = BranchSummary(
                id=branch.id,
                name=branch.name,
                city=branch.city,
                address=branch.address,
            )

        return AppointmentResponse(
            id=appointment.id,
            patient_id=appointment.patient_id,
            doctor_id=appointment.doctor_id,
            branch_id=appointment.branch_id,
            slot_datetime=appointment.slot_datetime,
            status=appointment.status,
            notes=appointment.notes,
            cancellation_reason=appointment.cancellation_reason,
            doctor=doc_summary,
            branch=branch_summary,
            created_at=appointment.created_at,
            updated_at=appointment.updated_at,
        )
