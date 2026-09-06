"""
Order and OrderItem models.

order_items snapshots product name and price at the time of order,
so historical orders remain accurate even if products are later
edited or soft-deleted.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base
from app.models.base import TimestampMixin


class OrderStatus(str, enum.Enum):
    """
    Full lifecycle:
      pending â†’ confirmed â†’ processing â†’ out_for_delivery â†’ delivered
                                      â†ک cancelled
      delivered â†’ refunded (partial support)
    """
    pending = "pending"
    confirmed = "confirmed"
    processing = "processing"
    out_for_delivery = "out_for_delivery"   # replaces "dispatched"
    delivered = "delivered"
    cancelled = "cancelled"
    refunded = "refunded"

# â”€â”€ Valid status transitions (enforced by order_service) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
VALID_STATUS_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.pending:          {OrderStatus.confirmed, OrderStatus.cancelled},
    OrderStatus.confirmed:        {OrderStatus.processing, OrderStatus.cancelled},
    OrderStatus.processing:       {OrderStatus.out_for_delivery, OrderStatus.cancelled},
    OrderStatus.out_for_delivery: {OrderStatus.delivered},
    OrderStatus.delivered:        {OrderStatus.refunded},
    OrderStatus.cancelled:        set(),
    OrderStatus.refunded:         set(),
}


class PaymentMethod(str, enum.Enum):
    cod = "cod"  # Cash on Delivery â€” Phase 1 only


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # â”€â”€ Totals (calculated by order_service.calculate_order_total) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    platform_fee: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    delivery_charges: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    grand_total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="orderstatus"),
        default=OrderStatus.pending,
        nullable=False,
        index=True,
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="paymentmethod"),
        default=PaymentMethod.cod,
        nullable=False,
    )
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # â”€â”€ Relationships â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    patient: Mapped[User] = relationship(back_populates="orders")  # noqa: F821
    branch: Mapped[Branch | None] = relationship(back_populates="orders")  # noqa: F821
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} status={self.status} total={self.grand_total}>"


class OrderItem(TimestampMixin, Base):
    """
    Snapshot of each product at the time the order was placed.
    product_name_snapshot and price_snapshot ensure historical accuracy
    even if the product is later edited or soft-deleted.
    """

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Soft FK â€” product may be soft-deleted but order_item must remain intact
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )

    # â”€â”€ Snapshots (frozen at order time) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    product_name_snapshot: Mapped[str] = mapped_column(String(500), nullable=False)
    sku_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    price_snapshot: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    discount_snapshot: Mapped[float] = mapped_column(Numeric(5, 2), default=0, nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)

    @property
    def line_total(self) -> float:
        return float(self.price_snapshot) * (1 - float(self.discount_snapshot) / 100) * self.quantity

    # â”€â”€ Relationships â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship(back_populates="order_items")  # noqa: F821
