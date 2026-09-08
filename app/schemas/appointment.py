"""
Pydantic schemas for Doctor Appointments and Slot generation.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.appointment import AppointmentStatus


class SlotResponse(BaseModel):
    slot_datetime: datetime
    is_available: bool


class AppointmentCreate(BaseModel):
    doctor_id: uuid.UUID
    branch_id: uuid.UUID
    slot_datetime: datetime = Field(
        ...,
        description="Exact start datetime of the 30-minute slot (e.g. 2026-09-10T10:00:00Z)",
    )
    notes: str | None = Field(None, max_length=1000)


class AppointmentStatusUpdate(BaseModel):
    status: AppointmentStatus
    cancellation_reason: str | None = Field(
        default=None,
        max_length=500,
        description="Optional reason if status is 'cancelled'",
    )


class DoctorSummary(BaseModel):
    id: uuid.UUID
    name: str
    specialization: str
    consultation_fee: str

    class Config:
        from_attributes = True


class BranchSummary(BaseModel):
    id: uuid.UUID
    name: str
    city: str
    address: str

    class Config:
        from_attributes = True


class PatientSummary(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str
    email: str | None = None

    class Config:
        from_attributes = True


class AppointmentResponse(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    branch_id: uuid.UUID
    slot_datetime: datetime
    status: AppointmentStatus
    notes: str | None
    cancellation_reason: str | None
    patient: PatientSummary | None = None
    doctor: DoctorSummary | None = None
    branch: BranchSummary | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AppointmentListResponse(BaseModel):
    items: list[AppointmentResponse]
    total: int
    page: int
    limit: int
    pages: int
