"""
Cart router — /api/v1/pharmacy/cart
Order router — /api/v1/pharmacy/orders

Cart endpoints:
  GET    /cart            → full cart with totals preview
  POST   /cart/items      → add item (enforces single-branch rule)
  PUT    /cart/items/{id} → update quantity
  DELETE /cart/items/{id} → remove item
  DELETE /cart            → clear entire cart

  On branch mismatch, returns 409 with BranchSwitchWarning body.
  Patient must call DELETE /cart first, then re-add with new branch.

Order endpoints:
  POST /orders            → checkout (patient)
  GET  /orders            → list orders (patient=own, admin=all)
  GET  /orders/{id}       → detail
  PUT  /orders/{id}/status → update lifecycle status (staff/admin)
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.order import Order, OrderItem, OrderStatus
from app.models.user import UserRole
from app.schemas.cart_order import (
    BranchSwitchWarning,
    CartItemAdd,
    CartItemUpdate,
    CartResponse,
    CheckoutRequest,
    OrderResponse,
    OrderStatusUpdate,
)
from app.services.cart_service import BranchMismatchError, CartService
from app.services.checkout_service import CheckoutService
from app.utils.pagination import PagedResponse, PaginationParams, pagination_params

cart_router = APIRouter(prefix="/cart", tags=["Pharmacy — Cart"])
order_router = APIRouter(prefix="/orders", tags=["Pharmacy — Orders"])


# ═══════════════════════════════════════════════════════════════════════════════
# CART
# ═══════════════════════════════════════════════════════════════════════════════
@cart_router.get(
    "",
    response_model=CartResponse,
    summary="Get my cart with live stock + totals preview",
)
async def get_cart(current_user: CurrentUser, db: DBSession):
    svc = CartService(db)
    return await svc.get_cart(current_user.id)


@cart_router.post(
    "/items",
    response_model=CartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add item to cart (enforces single-branch rule)",
)
async def add_cart_item(body: CartItemAdd, current_user: CurrentUser, db: DBSession):
    """
    Attempts to add item to cart.
    If the cart already has a different branch selected, returns:
      409 Conflict + BranchSwitchWarning body
    Frontend should prompt: "Clear cart and switch to Branch X?"
    On confirm: DELETE /cart → then re-POST /cart/items
    """
    svc = CartService(db)
    try:
        await svc.add_item(
            user_id=current_user.id,
            product_id=body.product_id,
            branch_id=body.branch_id,
            quantity=body.quantity,
        )
    except BranchMismatchError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=BranchSwitchWarning(
                current_branch_id=e.current,
                requested_branch_id=e.requested,
            ).model_dump(mode="json"),
        )
    return await svc.get_cart(current_user.id)


@cart_router.put(
    "/items/{cart_item_id}",
    response_model=CartResponse,
    summary="Update cart item quantity",
)
async def update_cart_item(
    cart_item_id: uuid.UUID,
    body: CartItemUpdate,
    current_user: CurrentUser,
    db: DBSession,
):
    svc = CartService(db)
    await svc.update_item(current_user.id, cart_item_id, body.quantity)
    return await svc.get_cart(current_user.id)


@cart_router.delete(
    "/items/{cart_item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove item from cart",
)
async def remove_cart_item(
    cart_item_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
):
    svc = CartService(db)
    await svc.remove_item(current_user.id, cart_item_id)


@cart_router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear entire cart (use before switching branch)",
)
async def clear_cart(current_user: CurrentUser, db: DBSession):
    svc = CartService(db)
    await svc.clear_cart(current_user.id)


# ═══════════════════════════════════════════════════════════════════════════════
# ORDERS
# ═══════════════════════════════════════════════════════════════════════════════
@order_router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Checkout — convert cart to order (with stock lock)",
)
async def checkout(body: CheckoutRequest, current_user: CurrentUser, db: DBSession):
    """
    Places the order:
    1. Locks branch_stock rows (SELECT FOR UPDATE)
    2. Validates stock for all items
    3. Computes totals via calculate_order_total()
    4. Creates Order + OrderItems with price snapshots
    5. Decrements branch_stock
    6. Clears cart
    7. Emits order_placed event
    """
    svc = CheckoutService(db)
    return await svc.checkout(current_user, body)


@order_router.get(
    "",
    response_model=PagedResponse[OrderResponse],
    summary="List orders (patient=own | admin/staff=all)",
)
async def list_orders(
    current_user: CurrentUser,
    db: DBSession,
    params: PaginationParams = Depends(pagination_params),
    order_status: OrderStatus | None = None,
):
    from sqlalchemy import func

    query = select(Order)

    # Patients see only their own orders
    if current_user.role == UserRole.patient:
        query = query.where(Order.patient_id == current_user.id)

    if order_status:
        query = query.where(Order.status == order_status)

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        query.order_by(Order.created_at.desc())
        .offset(params.offset)
        .limit(params.page_size)
    )
    orders = result.scalars().all()

    responses = []
    for order in orders:
        items_result = await db.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )
        items = items_result.scalars().all()
        responses.append(CheckoutService._build_response(order, items))

    return PagedResponse.create(responses, total, params)


@order_router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Get order detail",
)
async def get_order(order_id: uuid.UUID, current_user: CurrentUser, db: DBSession):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    # Patients can only see their own orders
    if current_user.role == UserRole.patient and order.patient_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden.")

    items_result = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    items = items_result.scalars().all()
    return CheckoutService._build_response(order, items)


@order_router.put(
    "/{order_id}/status",
    response_model=OrderResponse,
    summary="[Staff/Admin] Update order status",
    dependencies=[
        require_roles(
            UserRole.super_admin,
            UserRole.branch_manager,
            UserRole.pharmacy_staff,
        )
    ],
)
async def update_order_status(
    order_id: uuid.UUID,
    body: OrderStatusUpdate,
    current_user: CurrentUser,
    db: DBSession,
):
    """
    Updates order status following the allowed transition table:
      pending → confirmed → processing → out_for_delivery → delivered
                                       ↘ cancelled
      delivered → refunded

    Invalid transitions return 400 with allowed next states.
    """
    svc = CheckoutService(db)
    return await svc.update_status(order_id, body, current_user)
