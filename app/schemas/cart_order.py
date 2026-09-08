"""
Pydantic schemas for Cart and Order endpoints.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.order import OrderStatus, PaymentMethod


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CART
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class CartItemAdd(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(1, ge=1, le=100)
    branch_id: uuid.UUID  # required â€” enforces single-branch rule


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1, le=100)


class CartItemResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    branch_id: uuid.UUID | None
    quantity: int
    original_price: Decimal
    discount_percent: Decimal
    discounted_price: Decimal
    line_total: Decimal          # discounted_price أ— quantity
    stock_at_branch: int         # live stock check from branch_stock

    model_config = {"from_attributes": False}


class CartResponse(BaseModel):
    """Full cart with all items + running totals preview."""
    branch_id: uuid.UUID | None       # the branch this cart is locked to
    items: list[CartItemResponse]
    item_count: int
    subtotal: Decimal                 # sum of line_totals
    discount_amount: Decimal          # sum of discount savings
    platform_fee: Decimal             # preview (from settings)
    delivery_charges: Decimal         # preview (flat if delivery)
    grand_total: Decimal


class BranchSwitchWarning(BaseModel):
    """Returned when patient tries to add a product from a different branch."""
    detail: str = "Your cart belongs to a different branch. Clear it to switch."
    current_branch_id: uuid.UUID
    requested_branch_id: uuid.UUID


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ORDERS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class CheckoutRequest(BaseModel):
    delivery_method: str = Field("delivery", pattern="^(delivery|pickup)$")
    delivery_address: str | None = None
    notes: str | None = None
    payment_method: PaymentMethod = PaymentMethod.cod

    def validate_delivery(self) -> None:
        if self.delivery_method == "delivery" and not self.delivery_address:
            raise ValueError("delivery_address is required when delivery_method='delivery'.")


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


class OrderItemResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID | None
    product_name_snapshot: str
    sku_snapshot: str
    price_snapshot: Decimal
    discount_snapshot: Decimal
    quantity: int
    line_total: Decimal

    model_config = {"from_attributes": False}


class OrderResponse(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str | None = None
    patient_phone: str | None = None
    branch_id: uuid.UUID | None
    status: OrderStatus
    payment_method: PaymentMethod
    subtotal: Decimal
    discount_amount: Decimal
    platform_fee: Decimal
    delivery_charges: Decimal
    grand_total: Decimal
    delivery_address: str | None
    notes: str | None
    items: list[OrderItemResponse]
    created_at: datetime

    model_config = {"from_attributes": False}
