"""
Lab tests router — /api/v1/lab-tests

Endpoints:
  GET    /            → Paginated list with filters (?branch_id, ?home_sampling_only, ?search)
  GET    /{id}        → Test detail
  POST   /            → [Admin/Lab Staff] Create lab test
  PUT    /{id}        → [Admin/Lab Staff] Update lab test
  DELETE /{id}        → [Admin/Lab Staff] Soft-delete test
"""
from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app.dependencies import DBSession, require_roles
from app.models.lab_test import LabTest
from app.models.user import UserRole
from app.schemas.lab_test import (
    LabTestCreate,
    LabTestListResponse,
    LabTestResponse,
    LabTestUpdate,
)

router = APIRouter(prefix="/lab-tests", tags=["Lab Tests"])


@router.get(
    "",
    response_model=LabTestListResponse,
    summary="List lab tests (paginated, with search & branch filter)",
)
async def list_lab_tests(
    db: DBSession,
    branch_id: uuid.UUID | None = Query(None, description="Filter by branch ID or universal tests"),
    home_sampling_only: bool = Query(False, description="Only tests offering home sampling"),
    search: str | None = Query(None, min_length=1, description="Search by name or code"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = select(LabTest).where(
        LabTest.is_active.is_(True),
        LabTest.deleted_at.is_(None),
    )

    if branch_id:
        # Show universal tests (branch_id IS NULL) + branch-specific tests
        query = query.where(or_(LabTest.branch_id.is_(None), LabTest.branch_id == branch_id))

    if home_sampling_only:
        query = query.where(LabTest.home_sampling_available.is_(True))

    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                LabTest.name.ilike(term),
                LabTest.code.ilike(term),
            )
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * limit
    items_query = query.order_by(LabTest.name.asc()).offset(offset).limit(limit)
    items_result = await db.execute(items_query)
    items = items_result.scalars().all()

    pages = math.ceil(total / limit) if limit > 0 else 1

    return LabTestListResponse(
        items=[LabTestResponse.model_validate(t) for t in items],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/{test_id}",
    response_model=LabTestResponse,
    summary="Get single lab test detail",
)
async def get_lab_test(test_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(LabTest).where(LabTest.id == test_id, LabTest.deleted_at.is_(None))
    )
    test = result.scalar_one_or_none()
    if not test:
        raise HTTPException(status_code=404, detail="Lab test not found.")
    return LabTestResponse.model_validate(test)


@router.post(
    "",
    response_model=LabTestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin/Lab Staff] Create a new lab test",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def create_lab_test(body: LabTestCreate, db: DBSession):
    # Check code uniqueness among active tests
    existing = await db.execute(
        select(LabTest).where(LabTest.code == body.code, LabTest.deleted_at.is_(None))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Lab test with code '{body.code}' already exists.",
        )

    test = LabTest(**body.model_dump())
    db.add(test)
    await db.flush()
    return LabTestResponse.model_validate(test)


@router.put(
    "/{test_id}",
    response_model=LabTestResponse,
    summary="[Admin/Lab Staff] Update a lab test",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def update_lab_test(test_id: uuid.UUID, body: LabTestUpdate, db: DBSession):
    result = await db.execute(
        select(LabTest).where(LabTest.id == test_id, LabTest.deleted_at.is_(None))
    )
    test = result.scalar_one_or_none()
    if not test:
        raise HTTPException(status_code=404, detail="Lab test not found.")

    update_data = body.model_dump(exclude_unset=True)
    if "code" in update_data and update_data["code"] != test.code:
        conflict = await db.execute(
            select(LabTest).where(
                LabTest.code == update_data["code"],
                LabTest.id != test_id,
                LabTest.deleted_at.is_(None),
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Lab test with code '{update_data['code']}' already exists.",
            )

    for field, value in update_data.items():
        setattr(test, field, value)

    await db.flush()
    return LabTestResponse.model_validate(test)


@router.delete(
    "/{test_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Admin/Lab Staff] Soft-delete a lab test",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def delete_lab_test(test_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(LabTest).where(LabTest.id == test_id, LabTest.deleted_at.is_(None))
    )
    test = result.scalar_one_or_none()
    if not test:
        raise HTTPException(status_code=404, detail="Lab test not found.")

    test.deleted_at = datetime.now(UTC)
    test.is_active = False
    await db.flush()
