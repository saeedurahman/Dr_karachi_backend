"""
Concurrency & Race Condition Tests.

MANDATORY: Real PostgreSQL Only
These tests verify PostgreSQL-specific row-level locks (SELECT ... FOR UPDATE)
and partial unique constraints (uq_active_doctor_slot). SQLite is NOT supported
for these tests because SQLite ignores FOR UPDATE locks.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, time
from decimal import Decimal

import pytest
from app.models.appointment import Appointment, AppointmentStatus
from app.models.branch import Branch
from app.models.cart import CartItem
from app.models.category import Category
from app.models.doctor import DayOfWeek, Doctor, DoctorAvailability
from app.models.product import BranchStock, Product
from app.models.user import User, UserRole
from app.schemas.appointment import AppointmentCreate
from app.schemas.cart_order import CheckoutRequest, DeliveryMethod, PaymentMethod
from app.services.appointment_service import AppointmentService
from app.services.checkout_service import CheckoutService
from sqlalchemy import select
from tests.conftest import TestSessionLocal, create_user_helper


@pytest.mark.asyncio
async def test_concurrent_checkout_stock_oversell_prevention(setup_test_db):
    """
    Test two simultaneous checkouts competing for the LAST remaining unit of stock.
    Verifies that:
      1. Deadlock prevention logic acquires locks cleanly.
      2. Exactly one user successfully places the order.
      3. The other user receives an HTTP 409 Conflict.
      4. Stock never becomes negative.
    """
    async with TestSessionLocal() as session:
        # 1. Setup Branch
        branch = Branch(
            id=uuid.uuid4(),
            name="North Nazimabad Branch",
            city="Karachi",
            address="Block H",
            contact_number="+923000000001",
            working_hours="24/7",
            services_offered="Pharmacy",
            is_active=True,
        )
        session.add(branch)

        # 2. Setup Category & Product
        category = Category(id=uuid.uuid4(), name="Medicines", slug=f"meds-{uuid.uuid4().hex[:6]}")
        session.add(category)

        product = Product(
            id=uuid.uuid4(),
            name="Rare Antibiotic",
            sku=f"ANTIBIOTIC-{uuid.uuid4().hex[:6]}",
            price=Decimal("1500.00"),
            discount_percent=Decimal("0.00"),
            category_id=category.id,
            is_active=True,
        )
        session.add(product)

        # 3. Exactly 1 unit in stock
        branch_stock = BranchStock(
            product_id=product.id,
            branch_id=branch.id,
            stock=1,
        )
        session.add(branch_stock)

        # 4. Create two competing patients
        patient_a, _ = await create_user_helper(session, role=UserRole.patient)
        patient_b, _ = await create_user_helper(session, role=UserRole.patient)

        # 5. Both patients add 1 quantity of the item to their cart
        cart_a = CartItem(user_id=patient_a.id, product_id=product.id, branch_id=branch.id, quantity=1)
        cart_b = CartItem(user_id=patient_b.id, product_id=product.id, branch_id=branch.id, quantity=1)
        session.add_all([cart_a, cart_b])
        await session.commit()

    # 6. Execute two simultaneous checkouts using separate database sessions
    checkout_payload = CheckoutRequest(
        payment_method=PaymentMethod.cod,
        delivery_method=DeliveryMethod.standard,
        delivery_address="123 Test Street, Karachi",
    )

    async def _do_checkout(user: User):
        async with TestSessionLocal() as session:
            service = CheckoutService(session)
            return await service.checkout(user, checkout_payload)

    results = await asyncio.gather(
        _do_checkout(patient_a),
        _do_checkout(patient_b),
        return_exceptions=True,
    )

    # 7. Analyze outcomes
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected exactly 1 checkout success, got {len(successes)}"
    assert len(failures) == 1, f"Expected exactly 1 checkout failure, got {len(failures)}"

    # Confirm the failed request returned HTTP 409 Conflict
    exc = failures[0]
    assert hasattr(exc, "status_code"), f"Exception is not an HTTPException: {exc}"
    assert exc.status_code == 409
    assert "stock" in exc.detail.lower()

    # 8. Verify database state: stock must be exactly 0
    async with TestSessionLocal() as session:
        bs_res = await session.execute(
            select(BranchStock.stock).where(
                BranchStock.product_id == product.id,
                BranchStock.branch_id == branch.id,
            )
        )
        remaining_stock = bs_res.scalar_one()
        assert remaining_stock == 0, f"Remaining stock must be 0, found {remaining_stock}"


@pytest.mark.asyncio
async def test_concurrent_appointment_booking_conflict_prevention(setup_test_db):
    """
    Test two patients simultaneously trying to book the exact same 30-min doctor slot.
    Verifies that:
      1. Row-level locking on the Doctor serializes concurrent attempts.
      2. Exactly one booking succeeds.
      3. The competing booking raises HTTP 409 Conflict.
      4. Database has exactly one active appointment for that slot.
    """
    target_slot = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)  # Monday 10:00 AM

    async with TestSessionLocal() as session:
        # Setup Branch
        branch = Branch(
            id=uuid.uuid4(),
            name="DHA Phase 6 Clinic",
            city="Karachi",
            address="Khayaban-e-Shahbaz",
            contact_number="+923000000002",
            working_hours="09:00 - 21:00",
            services_offered="Clinics",
            is_active=True,
        )
        session.add(branch)

        # Setup Doctor
        doc_user, _ = await create_user_helper(session, role=UserRole.doctor)
        doctor = Doctor(
            id=uuid.uuid4(),
            user_id=doc_user.id,
            specialization="Cardiology",
            qualification="MBBS, FCPS",
            consultation_fee=Decimal("2500.00"),
            is_active=True,
        )
        session.add(doctor)

        # Doctor Availability on Monday 09:00 - 12:00
        avail = DoctorAvailability(
            doctor_id=doctor.id,
            branch_id=branch.id,
            day_of_week=DayOfWeek.monday,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_active=True,
        )
        session.add(avail)

        # Create competing patients
        patient_1, _ = await create_user_helper(session, role=UserRole.patient)
        patient_2, _ = await create_user_helper(session, role=UserRole.patient)
        await session.commit()

    # Competing booking payload
    booking_payload = AppointmentCreate(
        doctor_id=doctor.id,
        branch_id=branch.id,
        slot_datetime=target_slot,
        notes="Urgent checkup",
    )

    async def _do_book(patient: User):
        async with TestSessionLocal() as session:
            service = AppointmentService(session)
            return await service.book_appointment(patient, booking_payload)

    results = await asyncio.gather(
        _do_book(patient_1),
        _do_book(patient_2),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected exactly 1 booking success, got {len(successes)}"
    assert len(failures) == 1, f"Expected exactly 1 booking failure, got {len(failures)}"

    exc = failures[0]
    assert hasattr(exc, "status_code")
    assert exc.status_code == 409
    assert "already been booked" in exc.detail.lower()

    # Confirm exactly 1 appointment recorded in DB
    async with TestSessionLocal() as session:
        appt_res = await session.execute(
            select(Appointment).where(
                Appointment.doctor_id == doctor.id,
                Appointment.slot_datetime == target_slot,
                Appointment.status != AppointmentStatus.cancelled,
            )
        )
        appts = appt_res.scalars().all()
        assert len(appts) == 1
