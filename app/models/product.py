"""
Product & BranchStock models.

KEY DECISION: Stock is tracked per-branch in `branch_stock` table.
`products` table has NO `stock` column — BranchStock is the sole source of truth.

Partial unique index on `sku` ensures uniqueness only among active (non-deleted) products.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class Product(SoftDeleteMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        # Partial unique: SKU must be unique among non-deleted products only
        Index(
            "ix_products_sku_active",
            "sku",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)  # uniqueness via partial index
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # discount_percent: e.g. 15 means 15% off
    discount_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0.0, nullable=False)

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_prescription: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Relationships ──────────────────────────────────────────
    category: Mapped["Category"] = relationship(back_populates="products")  # noqa: F821
    branch_stock: Mapped[list["BranchStock"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    cart_items: Mapped[list["CartItem"]] = relationship(back_populates="product")  # noqa: F821
    order_items: Mapped[list["OrderItem"]] = relationship(back_populates="product")  # noqa: F821

    @property
    def discounted_price(self) -> float:
        return float(self.price) * (1 - float(self.discount_percent) / 100)

    def __repr__(self) -> str:
        return f"<Product sku={self.sku!r} name={self.name!r}>"


class BranchStock(TimestampMixin, Base):
    """
    Branch-wise stock level for a product.
    This is the SOLE source of truth for inventory — no stock column on Product.
    """

    __tablename__ = "branch_stock"
    __table_args__ = (
        UniqueConstraint("product_id", "branch_id", name="uq_branch_stock"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stock: Mapped[int] = mapped_column(nullable=False, default=0)

    # ── Relationships ──────────────────────────────────────────
    product: Mapped["Product"] = relationship(back_populates="branch_stock")
    branch: Mapped["Branch"] = relationship(back_populates="branch_stock")  # noqa: F821
