"""
Products router — /api/v1/pharmacy/products

Key behaviors:
- ?branch_id=  filter returns stock_at_branch from branch_stock
- All responses include original_price, discounted_price, discount_percent
  (frontend never calculates — just renders)
- Soft-delete: product stays in DB, removed from listings
- Stock management: staff upserts into branch_stock (not product.stock)
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.dependencies import DBSession, require_roles
from app.models.product import BranchStock, Product
from app.models.user import UserRole
from app.schemas.product import (
    BranchStockUpsert,
    ProductCreate,
    ProductResponse,
    ProductUpdate,
)
from app.utils.pagination import PagedResponse, PaginationParams, pagination_params

router = APIRouter(prefix="/products", tags=["Pharmacy — Products"])


def _product_response(product: Product, stock: int | None = None) -> ProductResponse:
    return ProductResponse.from_orm_with_branch(product, branch_stock=stock)


@router.get(
    "/",
    response_model=PagedResponse[ProductResponse],
    summary="List products (filter by branch, category, search)",
)
async def list_products(
    db: DBSession,
    params: PaginationParams = Depends(pagination_params),
    branch_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    search: str | None = None,
    in_stock_only: bool = False,
):
    """
    ?branch_id    → returns stock_at_branch field, filters out-of-stock if in_stock_only=true
    ?category_id  → filter by category
    ?search       → name/sku ilike search
    ?in_stock_only → only return products with stock > 0 at selected branch
    """
    query = select(Product).where(
        Product.deleted_at.is_(None),
        Product.is_active == True,
    )

    if category_id:
        query = query.where(Product.category_id == category_id)
    if search:
        query = query.where(
            Product.name.ilike(f"%{search}%") | Product.sku.ilike(f"%{search}%")
        )

    # If branch filter: join branch_stock for availability
    if branch_id and in_stock_only:
        query = query.join(
            BranchStock,
            (BranchStock.product_id == Product.id) & (BranchStock.branch_id == branch_id),
        ).where(BranchStock.stock > 0)

    # Count total
    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one()

    # Paginated results
    result = await db.execute(
        query.order_by(Product.name).offset(params.offset).limit(params.page_size)
    )
    products = result.scalars().all()

    # Build responses — fetch stock per-product if branch_id provided
    responses: list[ProductResponse] = []
    for p in products:
        stock_qty: int | None = None
        if branch_id:
            bs_result = await db.execute(
                select(BranchStock.stock).where(
                    BranchStock.product_id == p.id,
                    BranchStock.branch_id == branch_id,
                )
            )
            stock_qty = bs_result.scalar_one_or_none() or 0
        responses.append(_product_response(p, stock_qty))

    return PagedResponse.create(responses, total, params)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Get product detail",
)
async def get_product(
    product_id: uuid.UUID,
    db: DBSession,
    branch_id: uuid.UUID | None = None,
):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    stock_qty: int | None = None
    if branch_id:
        bs_result = await db.execute(
            select(BranchStock.stock).where(
                BranchStock.product_id == product_id,
                BranchStock.branch_id == branch_id,
            )
        )
        stock_qty = bs_result.scalar_one_or_none() or 0

    return _product_response(product, stock_qty)


@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Staff/Admin] Create product",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def create_product(body: ProductCreate, db: DBSession):
    # SKU uniqueness check (partial index gives DB guarantee, this gives a nice error)
    existing = await db.execute(
        select(Product).where(Product.sku == body.sku, Product.deleted_at.is_(None))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"SKU '{body.sku}' already exists.")

    product = Product(
        name=body.name,
        sku=body.sku,
        description=body.description,
        price=float(body.price),
        discount_percent=float(body.discount_percent),
        category_id=body.category_id,
        requires_prescription=body.requires_prescription,
    )
    db.add(product)
    await db.flush()
    return _product_response(product)


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    summary="[Staff/Admin] Update product",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def update_product(product_id: uuid.UUID, body: ProductUpdate, db: DBSession):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    data = body.model_dump(exclude_none=True)
    if "price" in data:
        data["price"] = float(data["price"])
    if "discount_percent" in data:
        data["discount_percent"] = float(data["discount_percent"])

    for field, value in data.items():
        setattr(product, field, value)
    return _product_response(product)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Admin] Soft-delete product",
    dependencies=[require_roles(UserRole.super_admin)],
)
async def delete_product(product_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    product.soft_delete()


@router.put(
    "/{product_id}/stock",
    response_model=dict,
    summary="[Staff/Admin] Set stock for a product at a specific branch",
    dependencies=[require_roles(UserRole.super_admin, UserRole.pharmacy_staff)],
)
async def upsert_branch_stock(product_id: uuid.UUID, body: BranchStockUpsert, db: DBSession):
    """
    Upserts (create or update) the branch_stock record.
    This is the only way to modify stock — no direct product.stock field exists.
    """
    result = await db.execute(
        select(BranchStock).where(
            BranchStock.product_id == product_id,
            BranchStock.branch_id == body.branch_id,
        )
    )
    bs = result.scalar_one_or_none()
    if bs:
        bs.stock = body.stock
    else:
        bs = BranchStock(
            product_id=product_id,
            branch_id=body.branch_id,
            stock=body.stock,
        )
        db.add(bs)
    await db.flush()
    return {"product_id": product_id, "branch_id": body.branch_id, "stock": bs.stock}
