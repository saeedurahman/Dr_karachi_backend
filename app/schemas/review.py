"""
Pydantic schemas for Reviews.
Supports polymorphic target (doctor/service), ratings 1-5,
moderation approval, and rating summaries.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.review import ReviewTargetType


class ReviewCreate(BaseModel):
    target_type: ReviewTargetType
    target_id: uuid.UUID
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")
    comment: str | None = Field(None, max_length=2000)


class ReviewAuthorSummary(BaseModel):
    id: uuid.UUID
    full_name: str

    class Config:
        from_attributes = True


class ReviewResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    target_type: ReviewTargetType
    target_id: uuid.UUID
    rating: int
    comment: str | None
    is_approved: bool
    author: ReviewAuthorSummary | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReviewApprovalUpdate(BaseModel):
    is_approved: bool


class ReviewSummaryResponse(BaseModel):
    target_type: ReviewTargetType
    target_id: uuid.UUID
    avg_rating: float = Field(..., description="Average rating rounded to 1 decimal place")
    total_count: int
    distribution: dict[int, int] = Field(
        ...,
        description="Rating count breakdown from 1 to 5 stars, e.g. {1: 0, 2: 1, 3: 4, 4: 10, 5: 25}",
    )


class ReviewListResponse(BaseModel):
    items: list[ReviewResponse]
    total: int
    page: int
    limit: int
    pages: int
