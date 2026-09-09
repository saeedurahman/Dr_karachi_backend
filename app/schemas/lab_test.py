"""
Pydantic schemas for Lab Test endpoints.
Includes home_sampling_available and home_sampling_fee support.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.lab_booking import CollectionType, LabBookingStatus


class LabTestBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=500)
    code: str = Field(..., min_length=1, max_length=50)
    description: str | None = None
    price: Decimal = Field(..., ge=0, decimal_places=2)
    branch_id: uuid.UUID | None = Field(
        None,
        description="Branch ID for branch-specific test, or None if available at all branches",
    )
    home_sampling_available: bool = False
    home_sampling_fee: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        decimal_places=2,
        description="Separate service fee for home sampling collection",
    )
    turnaround_hours: int | None = Field(None, ge=1)
    is_active: bool = True


class LabTestCreate(LabTestBase):
    pass


class LabTestUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=500)
    code: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = None
    price: Decimal | None = Field(None, ge=0, decimal_places=2)
    branch_id: uuid.UUID | None = None
    home_sampling_available: bool | None = None
    home_sampling_fee: Decimal | None = Field(None, ge=0, decimal_places=2)
    turnaround_hours: int | None = Field(None, ge=1)
    is_active: bool | None = None


class LabTestResponse(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    description: str | None
    price: Decimal
    branch_id: uuid.UUID | None
    home_sampling_available: bool
    home_sampling_fee: Decimal
    turnaround_hours: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LabTestListResponse(BaseModel):
    items: list[LabTestResponse]
    total: int
    page: int
    limit: int
    pages: int


class LabBookingCreate(BaseModel):
    branch_id: uuid.UUID
    collection_type: CollectionType = Field(
        default=CollectionType.clinic_visit,
        description="'clinic_visit' or 'home_sampling'",
    )
    preferred_date: date = Field(..., description="Target date for sample collection")
    time_slot: str = Field(..., max_length=50, description="'morning', 'afternoon', or 'evening'")
    collection_address: str | None = Field(None, description="Required if collection_type is 'home_sampling'")
    notes: str | None = Field(None, max_length=1000)


class LabBookingResponse(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    test_id: uuid.UUID
    branch_id: uuid.UUID
    collection_type: CollectionType
    preferred_date: date
    time_slot: str
    collection_address: str | None
    notes: str | None
    status: LabBookingStatus
    total_price: Decimal
    test: LabTestResponse | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LabBookingListResponse(BaseModel):
    items: list[LabBookingResponse]
    total: int
    page: int
    limit: int
    pages: int


class LabBookingAdminResponse(LabBookingResponse):
    patient_name: str | None = None
    patient_phone: str | None = None
    branch_name: str | None = None


class LabBookingAdminListResponse(BaseModel):
    items: list[LabBookingAdminResponse]
    total: int
    page: int
    limit: int
    pages: int


class LabBookingStatusUpdate(BaseModel):
    status: LabBookingStatus


class PatientSearchResult(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str

    class Config:
        from_attributes = True
