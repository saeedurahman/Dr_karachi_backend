"""
Category model — self-referencing tree for product categories.

Supports:
  - Top-level categories (parent_id = NULL)
  - Sub-categories (parent_id = FK → categories.id)

Partial unique index on slug ensures uniqueness only among
non-deleted categories, so soft-deleted slugs can be reused.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin


class Category(SoftDeleteMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        # PARTIAL unique index: slug must be unique WHERE deleted_at IS NULL
        Index(
            "ix_categories_slug_active",
            "slug",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)  # uniqueness via partial index
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(default=0, nullable=False)

    # Self-referencing FK
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Relationships ──────────────────────────────────────────
    parent: Mapped["Category | None"] = relationship(
        "Category", remote_side="Category.id", back_populates="children"
    )
    children: Mapped[list["Category"]] = relationship(
        "Category", back_populates="parent"
    )
    products: Mapped[list["Product"]] = relationship(  # noqa: F821
        back_populates="category"
    )

    def __repr__(self) -> str:
        return f"<Category name={self.name!r} slug={self.slug!r}>"
