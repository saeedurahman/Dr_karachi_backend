"""Pydantic schemas for branch endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BranchCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    city: str = Field(..., max_length=100)
    address: str
    phone: str = Field(..., pattern=r"^\+?[0-9]{10,15}$")
    email: str | None = None
    google_maps_url: str | None = None
    working_hours: dict[str, Any] = Field(
        default_factory=dict,
        description='e.g. {"mon": {"open": "09:00", "close": "21:00"}}',
    )
    services: list[str] = Field(default_factory=list)


class BranchUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    city: str | None = Field(None, max_length=100)
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    google_maps_url: str | None = None
    working_hours: dict[str, Any] | None = None
    services: list[str] | None = None
    is_active: bool | None = None


class BranchResponse(BaseModel):
    id: uuid.UUID
    name: str
    city: str
    address: str
    phone: str
    email: str | None
    google_maps_url: str | None
    working_hours: dict[str, Any]
    services: list[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
