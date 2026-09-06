"""Branches router — /api/v1/branches"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.dependencies import DBSession, require_roles
from app.models.branch import Branch
from app.models.user import UserRole
from app.schemas.branch import BranchCreate, BranchResponse, BranchUpdate
from app.utils.pagination import PagedResponse, PaginationParams, pagination_params

router = APIRouter(prefix="/branches", tags=["Branches"])


@router.get(
    "/",
    response_model=PagedResponse[BranchResponse],
    summary="List all active branches",
)
async def list_branches(
    db: DBSession,
    params: PaginationParams = Depends(pagination_params),
    city: str | None = None,
):
    query = select(Branch).where(Branch.is_active == True)
    if city:
        query = query.where(Branch.city.ilike(f"%{city}%"))

    total_result = await db.execute(select(Branch.id).where(Branch.is_active == True))
    total = len(total_result.all())

    result = await db.execute(
        query.order_by(Branch.created_at).offset(params.offset).limit(params.page_size)
    )
    branches = result.scalars().all()
    return PagedResponse.create(
        [BranchResponse.model_validate(b) for b in branches], total, params
    )


@router.get(
    "/{branch_id}",
    response_model=BranchResponse,
    summary="Get branch detail",
)
async def get_branch(branch_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found.")
    return BranchResponse.model_validate(branch)


@router.post(
    "",
    response_model=BranchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Create a new branch",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def create_branch(body: BranchCreate, db: DBSession):
    branch = Branch(**body.model_dump())
    db.add(branch)
    await db.flush()
    return BranchResponse.model_validate(branch)


@router.put(
    "/{branch_id}",
    response_model=BranchResponse,
    summary="[Admin/Manager] Update branch",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def update_branch(branch_id: uuid.UUID, body: BranchUpdate, db: DBSession):
    result = await db.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found.")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(branch, field, value)
    return BranchResponse.model_validate(branch)


@router.delete(
    "/{branch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Admin] Deactivate a branch",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def deactivate_branch(branch_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found.")
    branch.is_active = False
