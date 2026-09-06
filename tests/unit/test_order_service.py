"""
Unit tests for order total calculation service.
Validates Decimal precision, delivery methods, and discounts.
"""
from decimal import Decimal

from app.schemas.cart_order import DeliveryMethod
from app.services.order_service import CartLineItem, calculate_order_total


def test_standard_delivery_order_total():
    # 2 items at Rs 500 with 10% discount = Rs 450 each -> subtotal = 900
    # standard delivery = 200, platform fee = 25
    # expected grand total = 900 + 200 + 25 = 1125
    items = [
        CartLineItem(price=Decimal("500.00"), discount_percent=Decimal("10.00"), quantity=2)
    ]
    totals = calculate_order_total(items, delivery_method=DeliveryMethod.standard)

    assert totals.subtotal == Decimal("900.00")
    assert totals.discount_amount == Decimal("100.00")
    assert totals.delivery_charges == Decimal("200.00")
    assert totals.platform_fee == Decimal("25.00")
    assert totals.grand_total == Decimal("1125.00")


def test_express_delivery_order_total():
    # 1 item at Rs 1000 with 0% discount
    # express delivery = 350, platform fee = 25
    # expected grand total = 1000 + 350 + 25 = 1375
    items = [
        CartLineItem(price=Decimal("1000.00"), discount_percent=Decimal("0.00"), quantity=1)
    ]
    totals = calculate_order_total(items, delivery_method=DeliveryMethod.express)

    assert totals.subtotal == Decimal("1000.00")
    assert totals.discount_amount == Decimal("0.00")
    assert totals.delivery_charges == Decimal("350.00")
    assert totals.platform_fee == Decimal("25.00")
    assert totals.grand_total == Decimal("1375.00")


def test_branch_pickup_order_total():
    # Pickup has Rs 0 delivery charges
    items = [
        CartLineItem(price=Decimal("250.00"), discount_percent=Decimal("20.00"), quantity=4)
    ]
    totals = calculate_order_total(items, delivery_method=DeliveryMethod.pickup)

    # 250 * 0.8 = 200 * 4 = 800 subtotal, 200 discount, 0 delivery, 25 platform fee
    assert totals.subtotal == Decimal("800.00")
    assert totals.discount_amount == Decimal("200.00")
    assert totals.delivery_charges == Decimal("0.00")
    assert totals.platform_fee == Decimal("25.00")
    assert totals.grand_total == Decimal("825.00")


def test_empty_cart_order_total():
    totals = calculate_order_total([], delivery_method=DeliveryMethod.standard)
    assert totals.subtotal == Decimal("0.00")
    assert totals.grand_total == Decimal("0.00")
    assert totals.platform_fee == Decimal("0.00")
