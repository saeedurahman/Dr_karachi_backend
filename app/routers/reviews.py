"""
Reviews router — /api/v1/reviews

Endpoints:
  POST /                → Submit or update review (atomic upsert)
  GET  /                → List reviews (public sees only approved; staff sees all)
  GET  /summary         → Rating summary (average + 1-5 star breakdown)
  PUT  /{id}/approval   → [Staff/Admin] Moderate review approval
"""
from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.review import Review, ReviewTargetType
from app.models.user import UserRole
from app.schemas.review import (
    ReviewApprovalUpdate,
    ReviewCreate,
    ReviewListResponse,
    ReviewResponse,
    ReviewSummaryResponse,
)
from app.services.review_service import ReviewService

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.post(
    "",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit or update a review (Patient)",
)
async def submit_review(
    body: ReviewCreate,
    current_user: CurrentUser,
    db: DBSession,
):
    service = ReviewService(db)
    return await service.submit_review(user=current_user, body=body)


@router.get(
    "",
    response_model=ReviewListResponse,
    summary="List reviews (public sees only approved; staff can filter by status)",
)
async def list_reviews(
    db: DBSession,
    target_type: ReviewTargetType | None = None,
    target_id: uuid.UUID | None = None,
    is_approved: bool | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = None,
):
    query = select(Review).options(selectinload(Review.author))

    # Public/patient users can only view approved reviews
    is_staff = current_user and current_user.role in {
        UserRole.super_admin,
        UserRole.branch_manager,
    }

    if not is_staff:
        query = query.where(Review.is_approved.is_(True))
    elif is_approved is not None:
        query = query.where(Review.is_approved == is_approved)

    if target_type:
        query = query.where(Review.target_type == target_type)
    if target_id:
        query = query.where(Review.target_id == target_id)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * limit
    items_query = query.order_by(Review.created_at.desc()).offset(offset).limit(limit)
    items_result = await db.execute(items_query)
    reviews = items_result.scalars().all()

    service = ReviewService(db)
    pages = math.ceil(total / limit) if limit > 0 else 1

    return ReviewListResponse(
        items=[service._build_response(r) for r in reviews],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/summary",
    response_model=ReviewSummaryResponse,
    summary="Get rating summary (average score and distribution) for a doctor or service",
)
async def get_review_summary(
    target_type: ReviewTargetType,
    target_id: uuid.UUID,
    db: DBSession,
):
    service = ReviewService(db)
    return await service.get_summary(target_type=target_type, target_id=target_id)


@router.put(
    "/{review_id}/approval",
    response_model=ReviewResponse,
    summary="[Staff/Admin] Moderate review (approve or reject)",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def moderate_review(
    review_id: uuid.UUID,
    body: ReviewApprovalUpdate,
    db: DBSession,
):
    service = ReviewService(db)
    return await service.moderate_review(review_id=review_id, is_approved=body.is_approved)
