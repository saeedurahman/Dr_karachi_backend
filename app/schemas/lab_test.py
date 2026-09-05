"""
Pydantic schemas for Lab Test endpoints.
Includes home_sampling_available and home_sampling_fee support.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


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
