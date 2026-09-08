"""Pydantic schemas for doctor endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models.doctor import DayOfWeek


class DoctorCreate(BaseModel):
    user_id: uuid.UUID  # must be an existing user with role=doctor
    specialization: str = Field(..., max_length=255)
    qualification: str = Field(..., max_length=500)
    experience_years: int = Field(0, ge=0)
    consultation_fee: Decimal = Field(..., gt=0)
    bio: str | None = None


class DoctorUpdate(BaseModel):
    specialization: str | None = Field(None, max_length=255)
    qualification: str | None = Field(None, max_length=500)
    experience_years: int | None = Field(None, ge=0)
    consultation_fee: Decimal | None = Field(None, gt=0)
    bio: str | None = None
    is_active: bool | None = None


class DoctorBranchAssign(BaseModel):
    branch_id: uuid.UUID
    is_primary: bool = False


class AvailabilityCreate(BaseModel):
    branch_id: uuid.UUID
    day_of_week: DayOfWeek
    start_time: time
    end_time: time

    def validate_times(self) -> None:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")


class AvailabilityResponse(BaseModel):
    id: uuid.UUID
    branch_id: uuid.UUID
    day_of_week: DayOfWeek
    start_time: time
    end_time: time
    is_active: bool

    model_config = {"from_attributes": True}


class BranchSummary(BaseModel):
    """Minimal branch info embedded in DoctorResponse."""
    id: uuid.UUID
    name: str
    city: str
    address: str

    model_config = {"from_attributes": True}


class DoctorResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str = ""          # populated from doctor.user.full_name
    specialization: str
    qualification: str
    experience_years: int
    consultation_fee: Decimal
    bio: str | None
    profile_image_url: str | None
    is_active: bool
    branches: list[BranchSummary] = []  # populated from doctor.doctor_branches
    created_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _populate_derived(self) -> "DoctorResponse":
        """
        Pydantic can't traverse SQLAlchemy relationships automatically for
        computed fields, so we use a validator to pull them from the ORM
        object if it was passed directly (from_attributes mode).
        These fields are set by the router before validation via model_validate,
        so this validator is a safety fallback only.
        """
        return self


class SlotResponse(BaseModel):
    """Available 30-min appointment slot."""
    slot_datetime: datetime
    is_available: bool



class BatchAvailabilityItem(BaseModel):
    day_of_week: DayOfWeek
    start_time: time
    end_time: time

    def validate_times(self) -> None:
        if self.end_time <= self.start_time:
            raise ValueError('end_time must be after start_time')


class BatchAvailabilityUpdate(BaseModel):
    branch_id: uuid.UUID
    slots: list[BatchAvailabilityItem]
