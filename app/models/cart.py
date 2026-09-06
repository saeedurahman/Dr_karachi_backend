"""
CartItem model.

branch_id is included so cart contents are scoped to the branch the
patient is ordering from, which is required for branch-wise stock checks.
branch_id starts as nullable (patient may not have selected a branch yet)
but must be set before checkout proceeds.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base
from app.models.base import TimestampMixin


class CartItem(TimestampMixin, Base):
    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Branch the patient is ordering from — required for stock validation at checkout.
    # Nullable until patient selects a branch.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ── Relationships ──────────────────────────────────────────
    user: Mapped["User"] = relationship(back_populates="cart_items")  # noqa: F821
    product: Mapped["Product"] = relationship(back_populates="cart_items")  # noqa: F821
    branch: Mapped["Branch | None"] = relationship(back_populates="cart_items")  # noqa: F821

    def __repr__(self) -> str:
        return f"<CartItem user={self.user_id} product={self.product_id} qty={self.quantity}>"
