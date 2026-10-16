"""Pydantic schemas for WhatsApp broadcast campaigns and recipient statistics."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.whatsapp_broadcast import BroadcastRecipientStatus, BroadcastStatus


class BroadcastCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Broadcast campaign name")
    template_key: str = Field(..., description="Registered WhatsApp template key (must be marketing category)")
    language: str = Field(default="en", max_length=10, description="Template language code")
    params: dict[str, Any] = Field(default_factory=dict, description="Static template parameters")
    segment_filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Segmentation criteria (e.g. branch_id, doctor_id, specialty, active_within_days, has_placed_order)",
    )
    scheduled_at: datetime | None = Field(
        default=None,
        description="Optional future datetime to automatically start the broadcast",
    )


class BroadcastStats(BaseModel):
    total: int = 0
    pending: int = 0
    sent: int = 0
    delivered: int = 0
    read: int = 0
    failed: int = 0
    skipped: int = 0


class BroadcastResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    template_key: str
    language: str
    params: dict[str, Any]
    segment_filters: dict[str, Any]
    branch_id: uuid.UUID | None
    status: BroadcastStatus
    scheduled_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    total_recipients: int
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    stats: BroadcastStats | None = None


class BroadcastPreviewResponse(BaseModel):
    broadcast_id: uuid.UUID | None = None
    total_candidates: int
    eligible_count: int
    skipped_count: int
    skip_reasons: dict[str, int] = Field(default_factory=dict)
