"""
Review service — handles polymorphic target validation, atomic upsert,
moderation approval, and rating summaries.

Key invariants:
1. Target validation: checks doctor / service existence before accepting review.
2. Race-condition-free upsert: Uses PostgreSQL INSERT ... ON CONFLICT (uq_user_target_review)
   DO UPDATE, with IntegrityError fallback, guaranteeing no 500 error under concurrency.
3. Updated reviews automatically reset is_approved = False for re-moderation.
4. Emits new_review notification event.
5. get_summary computes average rating and 1-5 star distribution for approved reviews.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.branch import Branch
from app.models.doctor import Doctor
from app.models.lab_test import LabTest
from app.models.notification import NotificationEventType
from app.models.review import Review, ReviewTargetType
from app.models.user import User
from app.schemas.review import (
    ReviewAuthorSummary,
    ReviewCreate,
    ReviewResponse,
    ReviewSummaryResponse,
)
from app.services.notification_service import emit_event


class ReviewService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── 1. Target Validation ───────────────────────────────────────────────────
    async def validate_target_exists(self, target_type: ReviewTargetType, target_id: uuid.UUID) -> None:
        if target_type == ReviewTargetType.doctor:
            res = await self.db.execute(
                select(Doctor.id).where(Doctor.id == target_id, Doctor.deleted_at.is_(None))
            )
            if not res.scalar_one_or_none():
                raise HTTPException(status_code=404, detail=f"Doctor with ID '{target_id}' not found.")
        elif target_type == ReviewTargetType.service:
            # Check lab test or branch
            lab_res = await self.db.execute(
                select(LabTest.id).where(LabTest.id == target_id, LabTest.deleted_at.is_(None))
            )
            if not lab_res.scalar_one_or_none():
                branch_res = await self.db.execute(
                    select(Branch.id).where(Branch.id == target_id, Branch.is_active.is_(True))
                )
                if not branch_res.scalar_one_or_none():
                    raise HTTPException(
                        status_code=404,
                        detail=f"Service (lab test or branch) with ID '{target_id}' not found.",
                    )

    # ── 2. Atomic Upsert Review ────────────────────────────────────────────────
    async def submit_review(self, user: User, body: ReviewCreate) -> ReviewResponse:
        # Validate target
        await self.validate_target_exists(body.target_type, body.target_id)

        now = datetime.now(UTC)

        # Atomic PostgreSQL ON CONFLICT DO UPDATE
        stmt = (
            pg_insert(Review)
            .values(
                id=uuid.uuid4(),
                user_id=user.id,
                target_type=body.target_type,
                target_id=body.target_id,
                rating=body.rating,
                comment=body.comment,
                is_approved=False,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_user_target_review",
                set_={
                    "rating": body.rating,
                    "comment": body.comment,
                    "is_approved": False,  # Re-queue for moderation
                    "updated_at": now,
                },
            )
            .returning(Review.id)
        )

        try:
            res = await self.db.execute(stmt)
            review_id = res.scalar_one()
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            # Graceful retry fallback if dialect/constraint differences occur
            existing_res = await self.db.execute(
                select(Review).where(
                    Review.user_id == user.id,
                    Review.target_type == body.target_type,
                    Review.target_id == body.target_id,
                )
            )
            existing = existing_res.scalar_one()
            existing.rating = body.rating
            existing.comment = body.comment
            existing.is_approved = False
            existing.updated_at = now
            await self.db.flush()
            review_id = existing.id

        # Fetch review with author loaded
        full_res = await self.db.execute(
            select(Review).where(Review.id == review_id).options(selectinload(Review.author))
        )
        review = full_res.scalar_one()

        # Emit moderation event
        await emit_event(
            db=self.db,
            event_type=NotificationEventType.new_review,
            payload={
                "review_id": str(review.id),
                "author_id": str(user.id),
                "author_name": user.full_name,
                "target_type": body.target_type.value,
                "target_id": str(body.target_id),
                "rating": body.rating,
            },
            user_id=user.id,
            delivery_channel="whatsapp",
        )

        return self._build_response(review)

    # ── 3. Rating Summary ──────────────────────────────────────────────────────
    async def get_summary(
        self,
        target_type: ReviewTargetType,
        target_id: uuid.UUID,
    ) -> ReviewSummaryResponse:
        # Only compute summary from approved reviews
        result = await self.db.execute(
            select(Review.rating, func.count(Review.id))
            .where(
                Review.target_type == target_type,
                Review.target_id == target_id,
                Review.is_approved.is_(True),
            )
            .group_by(Review.rating)
        )
        rows = result.all()

        distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        total_count = 0
        sum_rating = 0

        for rating, count in rows:
            if 1 <= rating <= 5:
                distribution[rating] = count
                total_count += count
                sum_rating += rating * count

        avg_rating = round(sum_rating / total_count, 1) if total_count > 0 else 0.0

        return ReviewSummaryResponse(
            target_type=target_type,
            target_id=target_id,
            avg_rating=avg_rating,
            total_count=total_count,
            distribution=distribution,
        )

    # ── 4. Moderate Review ─────────────────────────────────────────────────────
    async def moderate_review(self, review_id: uuid.UUID, is_approved: bool) -> ReviewResponse:
        result = await self.db.execute(
            select(Review).where(Review.id == review_id).options(selectinload(Review.author))
        )
        review = result.scalar_one_or_none()
        if not review:
            raise HTTPException(status_code=404, detail="Review not found.")

        review.is_approved = is_approved
        await self.db.flush()
        return self._build_response(review)

    # ── Helper ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _build_response(review: Review) -> ReviewResponse:
        author_summary = None
        if review.author:
            author_summary = ReviewAuthorSummary(
                id=review.author.id,
                full_name=review.author.full_name,
            )
        return ReviewResponse(
            id=review.id,
            user_id=review.user_id,
            target_type=review.target_type,
            target_id=review.target_id,
            rating=review.rating,
            comment=review.comment,
            is_approved=review.is_approved,
            author=author_summary,
            created_at=review.created_at,
            updated_at=review.updated_at,
        )
