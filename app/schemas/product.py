"""
Pydantic schemas for Product and BranchStock endpoints.

ProductResponse always includes three explicit price fields:
  - original_price    → raw price (for strike-through display)
  - discounted_price  → price after discount (what patient pays)
  - discount_percent  → the % badge value
  - stock_at_branch   → quantity available at the requested branch (None if no branch filter)

Frontend never needs to recalculate anything.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


# ── Request schemas ────────────────────────────────────────────────────────────
class ProductCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=500)
    sku: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    price: Decimal = Field(..., gt=0, decimal_places=2)
    discount_percent: Decimal = Field(Decimal(0), ge=0, le=100, decimal_places=2)
    category_id: uuid.UUID | None = None
    requires_prescription: bool = False


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=500)
    description: str | None = None
    price: Decimal | None = Field(None, gt=0)
    discount_percent: Decimal | None = Field(None, ge=0, le=100)
    category_id: uuid.UUID | None = None
    requires_prescription: bool | None = None
    is_active: bool | None = None


class BranchStockUpsert(BaseModel):
    """Set or update stock for a product at a specific branch."""
    branch_id: uuid.UUID
    stock: int = Field(..., ge=0)


# ── Response schemas ───────────────────────────────────────────────────────────
class BranchStockInfo(BaseModel):
    branch_id: uuid.UUID
    stock: int

    model_config = {"from_attributes": True}


class ProductResponse(BaseModel):
    """
    Full product response with explicit price breakdown for frontend display.
    No frontend calculation needed — all three values are pre-computed.
    """
    id: uuid.UUID
    name: str
    sku: str
    description: str | None
    category_id: uuid.UUID | None

    # ── Explicit price fields ─────────────────────────────────────────────────
    original_price: Decimal        # strike-through display
    discount_percent: Decimal      # "X% OFF" badge
    discounted_price: Decimal      # actual price patient pays

    # ── Stock ─────────────────────────────────────────────────────────────────
    # populated only when branch_id filter was used
    stock_at_branch: int | None = None

    image_url: str | None
    requires_prescription: bool
    is_active: bool

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm_with_branch(
        cls,
        product: object,
        branch_stock: int | None = None,
    ) -> ProductResponse:
        """
        Build response from ORM Product + optional branch stock quantity.
        Computes all three price fields here so the router stays thin.
        """
        p = product  # type: ignore[assignment]
        original = Decimal(str(p.price))
        disc_pct = Decimal(str(p.discount_percent))
        discounted = (original * (100 - disc_pct) / 100).quantize(Decimal("0.01"))

        return cls(
            id=p.id,
            name=p.name,
            sku=p.sku,
            description=p.description,
            category_id=p.category_id,
            original_price=original,
            discount_percent=disc_pct,
            discounted_price=discounted,
            stock_at_branch=branch_stock,
            image_url=p.image_url,
            requires_prescription=p.requires_prescription,
            is_active=p.is_active,
        )
