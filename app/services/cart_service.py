"""
Cart service — manages the patient's shopping cart.

Key invariant enforced here:
  A cart belongs to ONE branch at a time.
  Adding a product from a different branch raises BranchMismatchError.
  The router returns a 409 + BranchSwitchWarning so the frontend
  can prompt the patient to confirm clearing their cart.

Stock is read (not decremented) at cart time.
Actual decrement happens only at checkout (order_service).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cart import CartItem
from app.models.product import BranchStock, Product
from app.schemas.cart_order import (
    CartItemResponse,
    CartResponse,
    BranchSwitchWarning,
)
from app.services.order_service import CartLineItem, calculate_order_total


class BranchMismatchError(Exception):
    """Raised when a product from a different branch is added to an existing cart."""
    def __init__(self, current: uuid.UUID, requested: uuid.UUID) -> None:
        self.current = current
        self.requested = requested
        super().__init__("Branch mismatch")


class CartService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Read ───────────────────────────────────────────────────────────────────
    async def get_cart(self, user_id: uuid.UUID) -> CartResponse:
        """Return the full cart with live stock and computed totals."""
        result = await self.db.execute(
            select(CartItem).where(CartItem.user_id == user_id)
        )
        cart_items = result.scalars().all()

        if not cart_items:
            return CartResponse(
                branch_id=None,
                items=[],
                item_count=0,
                subtotal=Decimal("0"),
                discount_amount=Decimal("0"),
                platform_fee=Decimal("0"),
                delivery_charges=Decimal("0"),
                grand_total=Decimal("0"),
            )

        branch_id = cart_items[0].branch_id
        line_items: list[CartLineItem] = []
        item_responses: list[CartItemResponse] = []

        for ci in cart_items:
            product = await self.db.get(Product, ci.product_id)
            if not product or not product.is_active or product.deleted_at:
                # Product deactivated — skip (caller may want to clean up)
                continue

            stock = await self._get_stock(ci.product_id, branch_id) if branch_id else 0
            price = Decimal(str(product.price))
            disc = Decimal(str(product.discount_percent))
            discounted = (price * (100 - disc) / 100)
            line_total = discounted * ci.quantity

            item_responses.append(CartItemResponse(
                id=ci.id,
                product_id=ci.product_id,
                product_name=product.name,
                branch_id=ci.branch_id,
                quantity=ci.quantity,
                original_price=price,
                discount_percent=disc,
                discounted_price=discounted,
                line_total=line_total,
                stock_at_branch=stock,
            ))
            line_items.append(CartLineItem(price=price, discount_percent=disc, quantity=ci.quantity))

        totals = calculate_order_total(line_items, delivery_method="delivery")

        return CartResponse(
            branch_id=branch_id,
            items=item_responses,
            item_count=len(item_responses),
            subtotal=totals.subtotal,
            discount_amount=totals.discount_amount,
            platform_fee=totals.platform_fee,
            delivery_charges=totals.delivery_charges,
            grand_total=totals.grand_total,
        )

    # ── Add item ───────────────────────────────────────────────────────────────
    async def add_item(
        self,
        user_id: uuid.UUID,
        product_id: uuid.UUID,
        branch_id: uuid.UUID,
        quantity: int,
    ) -> CartItem:
        """
        Add a product to the cart.
        Raises BranchMismatchError if a different branch is already set.
        """
        # 1. Validate product exists and is active
        product = await self.db.get(Product, product_id)
        if not product or not product.is_active or product.deleted_at:
            raise HTTPException(status_code=404, detail="Product not found or unavailable.")

        # 2. Enforce single-branch rule
        await self._enforce_branch(user_id, branch_id)

        # 3. Check stock
        stock = await self._get_stock(product_id, branch_id)
        if stock < quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Only {stock} units available at selected branch.",
            )

        # 4. Upsert: if item already in cart, update quantity
        existing = await self.db.execute(
            select(CartItem).where(
                CartItem.user_id == user_id,
                CartItem.product_id == product_id,
            )
        )
        cart_item = existing.scalar_one_or_none()

        if cart_item:
            new_qty = cart_item.quantity + quantity
            if new_qty > stock:
                raise HTTPException(
                    status_code=400,
                    detail=f"Only {stock} units available. You already have {cart_item.quantity} in cart.",
                )
            cart_item.quantity = new_qty
        else:
            cart_item = CartItem(
                user_id=user_id,
                product_id=product_id,
                branch_id=branch_id,
                quantity=quantity,
            )
            self.db.add(cart_item)

        await self.db.flush()
        return cart_item

    # ── Update quantity ────────────────────────────────────────────────────────
    async def update_item(
        self,
        user_id: uuid.UUID,
        cart_item_id: uuid.UUID,
        quantity: int,
    ) -> CartItem:
        result = await self.db.execute(
            select(CartItem).where(
                CartItem.id == cart_item_id,
                CartItem.user_id == user_id,
            )
        )
        cart_item = result.scalar_one_or_none()
        if not cart_item:
            raise HTTPException(status_code=404, detail="Cart item not found.")

        # Re-check stock
        if cart_item.branch_id:
            stock = await self._get_stock(cart_item.product_id, cart_item.branch_id)
            if quantity > stock:
                raise HTTPException(status_code=400, detail=f"Only {stock} units available.")

        cart_item.quantity = quantity
        return cart_item

    # ── Remove item ────────────────────────────────────────────────────────────
    async def remove_item(self, user_id: uuid.UUID, cart_item_id: uuid.UUID) -> None:
        result = await self.db.execute(
            select(CartItem).where(
                CartItem.id == cart_item_id,
                CartItem.user_id == user_id,
            )
        )
        cart_item = result.scalar_one_or_none()
        if not cart_item:
            raise HTTPException(status_code=404, detail="Cart item not found.")
        await self.db.delete(cart_item)

    # ── Clear entire cart ──────────────────────────────────────────────────────
    async def clear_cart(self, user_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(CartItem).where(CartItem.user_id == user_id)
        )

    # ── Helpers ────────────────────────────────────────────────────────────────
    async def _get_stock(self, product_id: uuid.UUID, branch_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(BranchStock.stock).where(
                BranchStock.product_id == product_id,
                BranchStock.branch_id == branch_id,
            )
        )
        row = result.scalar_one_or_none()
        return row if row is not None else 0

    async def _enforce_branch(self, user_id: uuid.UUID, requested_branch_id: uuid.UUID) -> None:
        """
        Ensure all cart items belong to the same branch.
        Raises BranchMismatchError if a different branch is already set.
        """
        result = await self.db.execute(
            select(CartItem.branch_id)
            .where(CartItem.user_id == user_id, CartItem.branch_id.is_not(None))
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row and row != requested_branch_id:
            raise BranchMismatchError(current=row, requested=requested_branch_id)
