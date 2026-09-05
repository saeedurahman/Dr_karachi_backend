"""
Categories router — /api/v1/pharmacy/categories

GET /          → paginated flat list  (admin/staff use)
GET /tree      → NESTED tree (parent + children) — for mega-menu dropdown
GET /{slug}    → single category detail
POST /         → create
PUT  /{id}     → update
DELETE /{id}   → soft-delete
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.database import Base
from app.dependencies import DBSession, require_roles
from app.models.category import Category
from app.models.user import UserRole
from app.schemas.category import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    NestedCategoryResponse,
)

router = APIRouter(prefix="/categories", tags=["Pharmacy — Categories"])


def _build_nested(category: Category, all_categories: list[Category]) -> NestedCategoryResponse:
    """Recursively build a nested category node from a flat list."""
    children = [
        _build_nested(c, all_categories)
        for c in all_categories
        if c.parent_id == category.id
    ]
    children.sort(key=lambda c: c.sort_order)
    return NestedCategoryResponse(
        id=category.id,
        name=category.name,
        slug=category.slug,
        description=category.description,
        image_url=category.image_url,
        sort_order=category.sort_order,
        children=children,
    )


@router.get(
    "/tree",
    response_model=list[NestedCategoryResponse],
    summary="Full category tree (parent + children) — use for mega-menu",
)
async def get_category_tree(db: DBSession):
    """
    Returns all non-deleted categories in a nested tree structure.
    Single DB query + Python recursion — efficient for ~50 categories.

    Response shape:
      [
        {
          "name": "Medications",
          "children": [
            {"name": "Antibiotics", "children": []},
            {"name": "Vitamins", "children": []}
          ]
        },
        { "name": "Wellness & Beauty", "children": [...] }
      ]
    """
    result = await db.execute(
        select(Category)
        .where(Category.deleted_at.is_(None))
        .order_by(Category.sort_order, Category.name)
    )
    all_cats = result.scalars().all()

    # Top-level categories (no parent)
    roots = [c for c in all_cats if c.parent_id is None]
    return [_build_nested(root, list(all_cats)) for root in roots]


@router.get(
    "/",
    response_model=list[CategoryResponse],
    summary="Flat list of all active categories",
)
async def list_categories(db: DBSession, parent_id: uuid.UUID | None = None):
    query = select(Category).where(Category.deleted_at.is_(None))
    if parent_id:
        query = query.where(Category.parent_id == parent_id)
    result = await db.execute(query.order_by(Category.sort_order))
    categories = result.scalars().all()
    return [CategoryResponse.model_validate(c) for c in categories]


@router.get(
    "/{slug}",
    response_model=CategoryResponse,
    summary="Get category by slug",
)
async def get_category(slug: str, db: DBSession):
    result = await db.execute(
        select(Category).where(Category.slug == slug, Category.deleted_at.is_(None))
    )
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")
    return CategoryResponse.model_validate(cat)


@router.post(
    "/",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Staff/Admin] Create category",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def create_category(body: CategoryCreate, db: DBSession):
    # Check slug uniqueness (partial index only covers active rows,
    # so we do an explicit check to give a nice error)
    existing = await db.execute(
        select(Category).where(Category.slug == body.slug, Category.deleted_at.is_(None))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Slug '{body.slug}' already in use.")

    cat = Category(**body.model_dump())
    db.add(cat)
    await db.flush()
    return CategoryResponse.model_validate(cat)


@router.put(
    "/{category_id}",
    response_model=CategoryResponse,
    summary="[Staff/Admin] Update category",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def update_category(category_id: uuid.UUID, body: CategoryUpdate, db: DBSession):
    result = await db.execute(
        select(Category).where(Category.id == category_id, Category.deleted_at.is_(None))
    )
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(cat, field, value)
    return CategoryResponse.model_validate(cat)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Admin] Soft-delete category",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def delete_category(category_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Category).where(Category.id == category_id, Category.deleted_at.is_(None))
    )
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")
    cat.soft_delete()
