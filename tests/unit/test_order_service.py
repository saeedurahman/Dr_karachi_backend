"""
Unit tests for order total calculation service.
Validates Decimal precision, delivery methods, and discounts.
"""
from decimal import Decimal

from app.services.order_service import CartLineItem, calculate_order_total


def test_delivery_order_total():
    # 2 items at Rs 500 with 10% discount = Rs 450 each -> subtotal = 900
    # delivery = 150 (settings.DELIVERY_FEE), platform fee = 900 * 0.02 = 18
    # expected grand total = 900 + 150 + 18 = 1068
    items = [
        CartLineItem(price=Decimal("500.00"), discount_percent=Decimal("10.00"), quantity=2)
    ]
    totals = calculate_order_total(items, delivery_method="delivery")

    assert totals.subtotal == Decimal("900.00")
    assert totals.discount_amount == Decimal("100.00")
    assert totals.delivery_charges == Decimal("150.00")
    assert totals.platform_fee == Decimal("18.00")
    assert totals.grand_total == Decimal("1068.00")


def test_delivery_order_total_no_discount():
    # 1 item at Rs 1000 with 0% discount
    # delivery = 150, platform fee = 1000 * 0.02 = 20
    # expected grand total = 1000 + 150 + 20 = 1170
    items = [
        CartLineItem(price=Decimal("1000.00"), discount_percent=Decimal("0.00"), quantity=1)
    ]
    totals = calculate_order_total(items, delivery_method="delivery")

    assert totals.subtotal == Decimal("1000.00")
    assert totals.discount_amount == Decimal("0.00")
    assert totals.delivery_charges == Decimal("150.00")
    assert totals.platform_fee == Decimal("20.00")
    assert totals.grand_total == Decimal("1170.00")


def test_branch_pickup_order_total():
    # Pickup has Rs 0 delivery charges
    items = [
        CartLineItem(price=Decimal("250.00"), discount_percent=Decimal("20.00"), quantity=4)
    ]
    totals = calculate_order_total(items, delivery_method="pickup")

    # 250 * 0.8 = 200 * 4 = 800 subtotal, 200 discount, 0 delivery, 16 platform fee
    assert totals.subtotal == Decimal("800.00")
    assert totals.discount_amount == Decimal("200.00")
    assert totals.delivery_charges == Decimal("0.00")
    assert totals.platform_fee == Decimal("16.00")
    assert totals.grand_total == Decimal("816.00")


def test_empty_cart_order_total():
    totals = calculate_order_total([], delivery_method="pickup")
    assert totals.subtotal == Decimal("0.00")
    assert totals.grand_total == Decimal("0.00")
    assert totals.platform_fee == Decimal("0.00")
