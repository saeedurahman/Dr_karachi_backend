"""
Order calculation service — single reusable function.

calculate_order_total() is the ONLY place order totals are computed.
Never duplicated in routers or other services.

Formula:
  subtotal       = sum(discounted_price × qty) for each cart item
  discount_amount= original_subtotal - discounted_subtotal
  platform_fee   = subtotal × PLATFORM_FEE_RATE
  delivery_charges = DELIVERY_FEE if method='delivery' else 0
  grand_total    = subtotal + platform_fee + delivery_charges
                   (discount already baked into subtotal)
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.config import settings


@dataclass(frozen=True)
class OrderTotals:
    """Immutable result of calculate_order_total()."""
    subtotal: Decimal           # discounted line totals
    original_subtotal: Decimal  # before discount (for display)
    discount_amount: Decimal    # how much patient saved
    platform_fee: Decimal
    delivery_charges: Decimal
    grand_total: Decimal

    def as_dict(self) -> dict:
        return {
            "subtotal": self.subtotal,
            "discount_amount": self.discount_amount,
            "platform_fee": self.platform_fee,
            "delivery_charges": self.delivery_charges,
            "grand_total": self.grand_total,
        }


@dataclass
class CartLineItem:
    """Input DTO for calculate_order_total — one line per cart item."""
    price: Decimal
    discount_percent: Decimal   # 0-100
    quantity: int


def _two_places(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_order_total(
    items: list[CartLineItem],
    delivery_method: str = "delivery",  # "delivery" | "pickup"
) -> OrderTotals:
    """
    Canonical order total calculation.

    Args:
        items: list of CartLineItem DTOs (price + discount_percent + qty)
        delivery_method: "delivery" charges DELIVERY_FEE; "pickup" = 0

    Returns:
        OrderTotals — frozen dataclass, all values Decimal.
    """
    original_subtotal = Decimal("0")
    discounted_subtotal = Decimal("0")

    for item in items:
        line_original = _two_places(item.price * item.quantity)
        discount_factor = (Decimal("100") - item.discount_percent) / Decimal("100")
        line_discounted = _two_places(item.price * discount_factor * item.quantity)

        original_subtotal += line_original
        discounted_subtotal += line_discounted

    discount_amount = _two_places(original_subtotal - discounted_subtotal)
    platform_fee = _two_places(discounted_subtotal * settings.PLATFORM_FEE_RATE)

    delivery_charges = (
        _two_places(settings.DELIVERY_FEE)
        if delivery_method == "delivery"
        else Decimal("0")
    )

    grand_total = _two_places(discounted_subtotal + platform_fee + delivery_charges)

    return OrderTotals(
        subtotal=_two_places(discounted_subtotal),
        original_subtotal=_two_places(original_subtotal),
        discount_amount=discount_amount,
        platform_fee=platform_fee,
        delivery_charges=delivery_charges,
        grand_total=grand_total,
    )
