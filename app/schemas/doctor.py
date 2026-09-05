"""Pydantic schemas for doctor endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field

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


class DoctorResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    specialization: str
    qualification: str
    experience_years: int
    consultation_fee: Decimal
    bio: str | None
    profile_image_url: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SlotResponse(BaseModel):
    """Available 30-min appointment slot."""
    slot_datetime: datetime
    is_available: bool
