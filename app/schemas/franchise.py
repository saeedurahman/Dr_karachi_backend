"""
Pydantic schemas for Franchise Leads.
Strictly aligned with FranchiseLead model fields (full_name, email, message, status, notes).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.franchise import FranchiseLeadStatus, InvestmentRange


class FranchiseLeadCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., min_length=7, max_length=20)
    email: EmailStr | None = None
    city: str = Field(..., min_length=2, max_length=100)
    investment_range: InvestmentRange
    message: str | None = Field(None, max_length=1000)


class FranchiseLeadUpdate(BaseModel):
    status: FranchiseLeadStatus | None = None
    is_read: bool | None = None
    notes: str | None = Field(None, max_length=2000)


class FranchiseLeadResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str
    email: str | None
    city: str
    investment_range: InvestmentRange
    message: str | None
    status: FranchiseLeadStatus
    is_read: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FranchiseLeadListResponse(BaseModel):
    items: list[FranchiseLeadResponse]
    total: int
    page: int
    limit: int
    pages: int
