"""
Checkout service — converts a validated cart into an Order.

Critical behaviors:
1. Uses calculate_order_total() from order_service (never duplicates math)
2. Acquires a SELECT ... FOR UPDATE row-level lock on each branch_stock row
   before decrementing — prevents overselling under concurrent requests
3. Emits order_placed notification event after successful commit
4. Clears cart after successful order creation
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cart import CartItem
from app.models.notification import NotificationEventType
from app.models.order import (
    VALID_STATUS_TRANSITIONS,
    Order,
    OrderItem,
    OrderStatus,
)
from app.models.product import BranchStock, Product
from app.models.user import User
from app.schemas.cart_order import (
    CheckoutRequest,
    OrderItemResponse,
    OrderResponse,
    OrderStatusUpdate,
)
from app.services.notification_service import emit_event
from app.services.order_service import CartLineItem, calculate_order_total


class CheckoutService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Checkout ───────────────────────────────────────────────────────────────
    async def checkout(self, user: User, body: CheckoutRequest) -> OrderResponse:
        """
        Convert the patient's cart into a confirmed Order.

        Steps:
          1. Load cart items (must be non-empty, must have a branch_id)
          2. Lock branch_stock rows (FOR UPDATE) in sorted product_id order (Deadlock Prevention)
          3. Validate stock for each item
          4. Compute totals via calculate_order_total()
          5. Create Order + OrderItems (with price snapshots)
          6. Decrement branch_stock
          7. Emit order_placed notification event
          8. Clear cart items for this branch on success
        """
        body.validate_delivery()

        # 1. Load cart
        result = await self.db.execute(
            select(CartItem).where(CartItem.user_id == user.id)
        )
        cart_items = result.scalars().all()
        if not cart_items:
            raise HTTPException(status_code=400, detail="Your cart is empty.")

        branch_id = cart_items[0].branch_id
        if not branch_id:
            raise HTTPException(
                status_code=400,
                detail="Please select a branch before checkout.",
            )

        # 2 & 3. Lock stock rows in deterministic sorted order + validate
        locked_stock = await self._lock_and_validate_stock(cart_items, branch_id)

        # 4. Build line items + calculate totals
        line_items: list[CartLineItem] = []
        for ci in cart_items:
            product = locked_stock[ci.product_id]["product"]
            line_items.append(
                CartLineItem(
                    price=Decimal(str(product.price)),
                    discount_percent=Decimal(str(product.discount_percent)),
                    quantity=ci.quantity,
                )
            )

        totals = calculate_order_total(line_items, delivery_method=body.delivery_method)

        # 5. Create Order
        order = Order(
            patient_id=user.id,
            branch_id=branch_id,
            subtotal=totals.subtotal,
            discount_amount=totals.discount_amount,
            platform_fee=totals.platform_fee,
            delivery_charges=totals.delivery_charges,
            grand_total=totals.grand_total,
            status=OrderStatus.pending,
            payment_method=body.payment_method,
            delivery_address=body.delivery_address,
            notes=body.notes,
        )
        self.db.add(order)
        await self.db.flush()  # get order.id

        # 5b. Create OrderItems (price snapshots)
        order_item_models = []
        for ci in cart_items:
            product = locked_stock[ci.product_id]["product"]
            oi = OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name_snapshot=product.name,
                sku_snapshot=product.sku,
                price_snapshot=product.price,
                discount_snapshot=product.discount_percent,
                quantity=ci.quantity,
            )
            self.db.add(oi)
            order_item_models.append(oi)

        # 6. Decrement stock (rows already locked)
        for ci in cart_items:
            bs: BranchStock = locked_stock[ci.product_id]["branch_stock"]
            bs.stock -= ci.quantity

        await self.db.flush()

        # 7. Emit notification event
        await emit_event(
            db=self.db,
            event_type=NotificationEventType.order_placed,
            payload={
                "order_id": str(order.id),
                "patient_phone": user.phone,
                "grand_total": str(totals.grand_total),
                "branch_id": str(branch_id),
                "item_count": len(cart_items),
            },
            user_id=user.id,
            delivery_channel="whatsapp",
        )

        # 8. Clear cart items for this branch on success
        await self.db.execute(
            delete(CartItem).where(
                CartItem.user_id == user.id,
                CartItem.branch_id == branch_id,
            )
        )
        # Commit so FOR UPDATE locks are held until stock decrement is durable.
        # Without this, concurrent sessions see the pre-decrement stock after rollback
        # on session close and both checkouts can succeed (oversell).
        await self.db.commit()

        return self._build_response(order, order_item_models)

    # ── Update order status ────────────────────────────────────────────────────
    async def update_status(
        self, order_id: uuid.UUID, body: OrderStatusUpdate, actor: User
    ) -> OrderResponse:
        result = await self.db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")

        # Enforce valid transition
        allowed = VALID_STATUS_TRANSITIONS.get(order.status, set())
        if body.status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot transition from '{order.status}' to '{body.status}'. "
                    f"Allowed: {[s.value for s in allowed] or 'none'}."
                ),
            )

        order.status = body.status

        # Emit status-change notification
        await emit_event(
            db=self.db,
            event_type=NotificationEventType.order_status_changed,
            payload={
                "order_id": str(order.id),
                "new_status": body.status.value,
                "patient_id": str(order.patient_id),
            },
            user_id=order.patient_id,
            delivery_channel="whatsapp",
        )

        items_result = await self.db.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )
        items = items_result.scalars().all()
        return self._build_response(order, items)

    # ── Helpers ────────────────────────────────────────────────────────────────
    async def _lock_and_validate_stock(
        self,
        cart_items: list[CartItem],
        branch_id: uuid.UUID,
    ) -> dict[uuid.UUID, dict]:
        """
        Acquire row-level locks on branch_stock rows and validate quantities.
        Returns a dict keyed by product_id → {product, branch_stock}.

        Deadlock Prevention:
          Sorts cart items by product_id BEFORE acquiring row-level locks
          (SELECT ... FOR UPDATE). This ensures all concurrent checkout transactions
          acquire locks in the exact same deterministic order, preventing deadlocks
          when multiple users checkout overlapping items simultaneously.
        """
        locked: dict[uuid.UUID, dict] = {}

        # Sort cart items deterministically by product_id to prevent deadlocks
        sorted_cart_items = sorted(cart_items, key=lambda item: item.product_id)

        for ci in sorted_cart_items:
            # Lock the branch_stock row
            bs_result = await self.db.execute(
                select(BranchStock)
                .where(
                    BranchStock.product_id == ci.product_id,
                    BranchStock.branch_id == branch_id,
                )
                .with_for_update()  # ← row-level lock
            )
            bs = bs_result.scalar_one_or_none()

            if bs is None or bs.stock < ci.quantity:
                available = bs.stock if bs else 0
                # Fetch product name for error message
                product = await self.db.get(Product, ci.product_id)
                name = product.name if product else str(ci.product_id)
                raise HTTPException(
                    status_code=409,
                    detail=f"'{name}': only {available} units in stock at selected branch.",
                )

            product = await self.db.get(Product, ci.product_id)
            if not product or not product.is_active or product.deleted_at:
                raise HTTPException(
                    status_code=400,
                    detail=f"Product {ci.product_id} is no longer available.",
                )

            locked[ci.product_id] = {"product": product, "branch_stock": bs}

        return locked

    @staticmethod
    def _build_response(order: Order, items: list[OrderItem]) -> OrderResponse:
        return OrderResponse(
            id=order.id,
            patient_id=order.patient_id,
            branch_id=order.branch_id,
            status=order.status,
            payment_method=order.payment_method,
            subtotal=Decimal(str(order.subtotal)),
            discount_amount=Decimal(str(order.discount_amount)),
            platform_fee=Decimal(str(order.platform_fee)),
            delivery_charges=Decimal(str(order.delivery_charges)),
            grand_total=Decimal(str(order.grand_total)),
            delivery_address=order.delivery_address,
            notes=order.notes,
            items=[
                OrderItemResponse(
                    id=oi.id,
                    product_id=oi.product_id,
                    product_name_snapshot=oi.product_name_snapshot,
                    sku_snapshot=oi.sku_snapshot,
                    price_snapshot=Decimal(str(oi.price_snapshot)),
                    discount_snapshot=Decimal(str(oi.discount_snapshot)),
                    quantity=oi.quantity,
                    line_total=Decimal(str(oi.line_total)),
                )
                for oi in items
            ],
            created_at=order.created_at,
        )
