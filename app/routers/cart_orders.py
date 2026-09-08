"""
Cart router -- /api/v1/pharmacy/cart
Order router -- /api/v1/pharmacy/orders

Cart endpoints:
  GET    /cart            -- full cart with totals preview
  POST   /cart/items      -- add item (enforces single-branch rule)
  PUT    /cart/items/{id} -- update quantity
  DELETE /cart/items/{id} -- remove item
  DELETE /cart            -- clear entire cart

  On branch mismatch, returns 409 with BranchSwitchWarning body.
  Patient must call DELETE /cart first, then re-add with new branch.

Order endpoints:
  POST /orders            -- checkout (patient)
  GET  /orders            -- list orders (patient=own, admin=all)
  GET  /orders/{id}       -- detail
  PUT  /orders/{id}/status -- update lifecycle status (staff/admin)

Admin-only query params on GET /orders:
  ?order_status=          -- filter by OrderStatus enum value
  ?branch_id=             -- filter by fulfilling branch UUID
  ?search=                -- ilike match on patient full_name or phone
  ?date_from=             -- ISO date lower bound (inclusive) on created_at
  ?date_to=               -- ISO date upper bound (inclusive) on created_at
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select

from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.order import Order, OrderItem, OrderStatus
from app.models.user import User, UserRole
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

cart_router = APIRouter(prefix="/cart", tags=["Pharmacy -- Cart"])
order_router = APIRouter(prefix="/orders", tags=["Pharmacy -- Orders"])


# ---------------------------------------------------------------------------
# CART
# ---------------------------------------------------------------------------
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
    On confirm: DELETE /cart -- then re-POST /cart/items
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


# ---------------------------------------------------------------------------
# ORDERS
# ---------------------------------------------------------------------------
@order_router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Checkout -- convert cart to order (with stock lock)",
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
    branch_id: uuid.UUID | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    """
    Admin/staff filters (ignored for patients):
      ?order_status=  -- OrderStatus enum value
      ?branch_id=     -- fulfilling branch UUID
      ?search=        -- ilike on patient full_name or phone
      ?date_from=     -- ISO date, inclusive lower bound on created_at
      ?date_to=       -- ISO date, inclusive upper bound on created_at
    """
    # Join User so we can filter by patient name/phone and include in response
    query = select(Order, User).join(User, Order.patient_id == User.id)

    # Patients see only their own orders; ignore admin filters
    if current_user.role == UserRole.patient:
        query = query.where(Order.patient_id == current_user.id)
    else:
        # Admin/staff filters
        if branch_id:
            query = query.where(Order.branch_id == branch_id)
        if search:
            term = f"%{search}%"
            query = query.where(
                or_(User.full_name.ilike(term), User.phone.ilike(term))
            )
        if date_from:
            dt_from = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
            query = query.where(Order.created_at >= dt_from)
        if date_to:
            # include the entire day_to
            dt_to = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
            query = query.where(Order.created_at <= dt_to)

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
    rows = result.all()  # list of (Order, User) tuples

    responses = []
    for order, patient in rows:
        items_result = await db.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )
        items = items_result.scalars().all()
        responses.append(CheckoutService._build_response(order, items, patient=patient))

    return PagedResponse.create(responses, total, params)


@order_router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Get order detail",
)
async def get_order(order_id: uuid.UUID, current_user: CurrentUser, db: DBSession):
    # Join patient for name/phone in response
    result = await db.execute(
        select(Order, User).join(User, Order.patient_id == User.id)
        .where(Order.id == order_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found.")

    order, patient = row

    # Patients can only see their own orders
    if current_user.role == UserRole.patient and order.patient_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden.")

    items_result = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    items = items_result.scalars().all()
    return CheckoutService._build_response(order, items, patient=patient)


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
      pending -> confirmed -> processing -> out_for_delivery -> delivered
                                       -> cancelled
      delivered -> refunded

    Invalid transitions return 400 with allowed next states.
    """
    svc = CheckoutService(db)
    return await svc.update_status(order_id, body, current_user)
