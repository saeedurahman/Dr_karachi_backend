"""
Lab tests router — /api/v1/lab-tests

Endpoints:
  GET    /            → Paginated list with filters (?branch_id, ?home_sampling_only, ?search)
  GET    /{id}        → Test detail
  POST   /            → [Admin/Lab Staff] Create lab test
  PUT    /{id}        → [Admin/Lab Staff] Update lab test
  DELETE /{id}        → [Admin/Lab Staff] Soft-delete test
"""
from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.branch import Branch
from app.models.lab_booking import (
    VALID_BOOKING_STATUS_TRANSITIONS,
    CollectionType,
    LabBooking,
    LabBookingStatus,
)
from app.models.lab_test import LabTest
from app.models.notification import NotificationEventType
from app.models.user import User, UserRole
from app.schemas.lab_test import (
    LabBookingAdminListResponse,
    LabBookingAdminResponse,
    LabBookingCreate,
    LabBookingListResponse,
    LabBookingResponse,
    LabBookingStatusUpdate,
    LabTestCreate,
    LabTestListResponse,
    LabTestResponse,
    LabTestUpdate,
    PatientSearchResult,
)
from app.services.notification_service import emit_event

router = APIRouter(prefix="/lab-tests", tags=["Lab Tests"])


@router.get(
    "",
    response_model=LabTestListResponse,
    summary="List lab tests (paginated, with search & branch filter)",
)
async def list_lab_tests(
    db: DBSession,
    branch_id: uuid.UUID | None = Query(None, description="Filter by branch ID or universal tests"),
    home_sampling_only: bool = Query(False, description="Only tests offering home sampling"),
    search: str | None = Query(None, min_length=1, description="Search by name or code"),
    include_inactive: bool = Query(False, description="Include inactive tests (admin use)"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = select(LabTest).where(LabTest.deleted_at.is_(None))
    if not include_inactive:
        query = query.where(LabTest.is_active.is_(True))

    if branch_id:
        # Show universal tests (branch_id IS NULL) + branch-specific tests
        query = query.where(or_(LabTest.branch_id.is_(None), LabTest.branch_id == branch_id))

    if home_sampling_only:
        query = query.where(LabTest.home_sampling_available.is_(True))

    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                LabTest.name.ilike(term),
                LabTest.code.ilike(term),
            )
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * limit
    items_query = query.order_by(LabTest.name.asc()).offset(offset).limit(limit)
    items_result = await db.execute(items_query)
    items = items_result.scalars().all()

    pages = math.ceil(total / limit) if limit > 0 else 1

    return LabTestListResponse(
        items=[LabTestResponse.model_validate(t) for t in items],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


def _build_booking_admin_response(
    booking: LabBooking, patient: User, branch: Branch
) -> LabBookingAdminResponse:
    return LabBookingAdminResponse(
        id=booking.id,
        patient_id=booking.patient_id,
        test_id=booking.test_id,
        branch_id=booking.branch_id,
        collection_type=booking.collection_type,
        preferred_date=booking.preferred_date,
        time_slot=booking.time_slot,
        collection_address=booking.collection_address,
        notes=booking.notes,
        status=booking.status,
        total_price=booking.total_price,
        test=LabTestResponse.model_validate(booking.test) if booking.test else None,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
        patient_name=patient.full_name,
        patient_phone=patient.phone,
        branch_name=branch.name,
    )


@router.get(
    "/bookings",
    response_model=LabBookingAdminListResponse,
    summary="[Admin/Lab Staff] List all lab bookings (filterable)",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def list_lab_bookings_admin(
    db: DBSession,
    status_filter: LabBookingStatus | None = Query(None, alias="status"),
    branch_id: uuid.UUID | None = None,
    collection_type: CollectionType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    # Registered ahead of GET /{test_id} below: both are single-segment paths
    # ("/bookings" vs "/{test_id}"), and FastAPI/Starlette matches routes in
    # registration order, not by specificity -- if this were registered after
    # /{test_id}, "/bookings" would be swallowed by it and 422 on UUID parsing.
    query = (
        select(LabBooking, User, Branch)
        .join(User, LabBooking.patient_id == User.id)
        .join(Branch, LabBooking.branch_id == Branch.id)
        .options(selectinload(LabBooking.test))
    )
    if status_filter:
        query = query.where(LabBooking.status == status_filter)
    if branch_id:
        query = query.where(LabBooking.branch_id == branch_id)
    if collection_type:
        query = query.where(LabBooking.collection_type == collection_type)
    if date_from:
        query = query.where(LabBooking.preferred_date >= date_from)
    if date_to:
        query = query.where(LabBooking.preferred_date <= date_to)

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * limit
    result = await db.execute(
        query.order_by(LabBooking.preferred_date.desc(), LabBooking.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = result.all()  # (LabBooking, User, Branch) tuples

    pages = math.ceil(total / limit) if limit > 0 else 1

    return LabBookingAdminListResponse(
        items=[_build_booking_admin_response(b, patient, branch) for b, patient, branch in rows],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/{test_id}",
    response_model=LabTestResponse,
    summary="Get single lab test detail",
)
async def get_lab_test(test_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(LabTest).where(LabTest.id == test_id, LabTest.deleted_at.is_(None))
    )
    test = result.scalar_one_or_none()
    if not test:
        raise HTTPException(status_code=404, detail="Lab test not found.")
    return LabTestResponse.model_validate(test)


@router.post(
    "",
    response_model=LabTestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin/Lab Staff] Create a new lab test",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def create_lab_test(body: LabTestCreate, db: DBSession):
    # Check code uniqueness among active tests
    existing = await db.execute(
        select(LabTest).where(LabTest.code == body.code, LabTest.deleted_at.is_(None))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Lab test with code '{body.code}' already exists.",
        )

    test = LabTest(**body.model_dump())
    db.add(test)
    await db.flush()
    return LabTestResponse.model_validate(test)


@router.put(
    "/{test_id}",
    response_model=LabTestResponse,
    summary="[Admin/Lab Staff] Update a lab test",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def update_lab_test(test_id: uuid.UUID, body: LabTestUpdate, db: DBSession):
    result = await db.execute(
        select(LabTest).where(LabTest.id == test_id, LabTest.deleted_at.is_(None))
    )
    test = result.scalar_one_or_none()
    if not test:
        raise HTTPException(status_code=404, detail="Lab test not found.")

    update_data = body.model_dump(exclude_unset=True)
    if "code" in update_data and update_data["code"] != test.code:
        conflict = await db.execute(
            select(LabTest).where(
                LabTest.code == update_data["code"],
                LabTest.id != test_id,
                LabTest.deleted_at.is_(None),
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Lab test with code '{update_data['code']}' already exists.",
            )

    for field, value in update_data.items():
        setattr(test, field, value)

    await db.flush()
    return LabTestResponse.model_validate(test)


@router.delete(
    "/{test_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Admin/Lab Staff] Soft-delete a lab test",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def delete_lab_test(test_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(LabTest).where(LabTest.id == test_id, LabTest.deleted_at.is_(None))
    )
    test = result.scalar_one_or_none()
    if not test:
        raise HTTPException(status_code=404, detail="Lab test not found.")

    test.deleted_at = datetime.now(UTC)
    test.is_active = False
    await db.flush()


# ── Lab Test Booking ─────────────────────────────────────────────────────────

@router.post(
    "/{test_id}/book",
    response_model=LabBookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Book a diagnostic lab test (walk-in or home sampling)",
)
async def book_lab_test(
    test_id: uuid.UUID,
    body: LabBookingCreate,
    current_user: CurrentUser,
    db: DBSession,
):
    # 1. Fetch test
    test_result = await db.execute(
        select(LabTest).where(
            LabTest.id == test_id,
            LabTest.is_active.is_(True),
            LabTest.deleted_at.is_(None),
        )
    )
    test = test_result.scalar_one_or_none()
    if not test:
        raise HTTPException(status_code=404, detail="Lab test not found or unavailable.")

    # 2. Verify branch
    branch_result = await db.execute(
        select(Branch).where(Branch.id == body.branch_id, Branch.is_active.is_(True))
    )
    branch = branch_result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found or inactive.")

    # If test is branch-specific, ensure branch matches
    if test.branch_id and test.branch_id != body.branch_id:
        raise HTTPException(status_code=400, detail="This test is not available at the selected branch.")

    # 3. If home sampling, verify test supports it and address is provided
    if body.collection_type == CollectionType.home_sampling:
        if not test.home_sampling_available:
            raise HTTPException(
                status_code=400,
                detail="Home sample collection is not available for this test.",
            )
        if not body.collection_address or not body.collection_address.strip():
            raise HTTPException(
                status_code=400,
                detail="Collection address is required for home sampling.",
            )
        total_price = Decimal(str(test.price)) + Decimal(str(test.home_sampling_fee))
    else:
        total_price = Decimal(str(test.price))

    # 4. Create booking
    booking = LabBooking(
        patient_id=current_user.id,
        test_id=test.id,
        branch_id=branch.id,
        collection_type=body.collection_type,
        preferred_date=body.preferred_date,
        time_slot=body.time_slot,
        collection_address=body.collection_address.strip() if body.collection_address else None,
        notes=body.notes,
        status=LabBookingStatus.pending,
        total_price=total_price,
    )
    db.add(booking)
    await db.flush()

    # 5. Emit event
    await emit_event(
        db=db,
        event_type=NotificationEventType.order_placed,
        payload={
            "booking_id": str(booking.id),
            "patient_id": str(current_user.id),
            "test_name": test.name,
            "branch_name": branch.name,
            "collection_type": body.collection_type.value,
            "preferred_date": body.preferred_date.isoformat(),
            "total_price": str(total_price),
        },
        user_id=current_user.id,
        delivery_channel="whatsapp",
    )

    await db.commit()

    return LabBookingResponse(
        id=booking.id,
        patient_id=booking.patient_id,
        test_id=booking.test_id,
        branch_id=booking.branch_id,
        collection_type=booking.collection_type,
        preferred_date=booking.preferred_date,
        time_slot=booking.time_slot,
        collection_address=booking.collection_address,
        notes=booking.notes,
        status=booking.status,
        total_price=booking.total_price,
        test=LabTestResponse.model_validate(test),
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


@router.get(
    "/bookings/my",
    response_model=LabBookingListResponse,
    summary="List patient's own lab test bookings",
)
async def list_my_lab_bookings(
    current_user: CurrentUser,
    db: DBSession,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = (
        select(LabBooking)
        .options(selectinload(LabBooking.test))
        .where(LabBooking.patient_id == current_user.id)
        .order_by(LabBooking.created_at.desc())
    )
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * limit
    items_result = await db.execute(query.offset(offset).limit(limit))
    bookings = items_result.scalars().all()

    pages = math.ceil(total / limit) if limit > 0 else 1

    return LabBookingListResponse(
        items=[
            LabBookingResponse(
                id=b.id,
                patient_id=b.patient_id,
                test_id=b.test_id,
                branch_id=b.branch_id,
                collection_type=b.collection_type,
                preferred_date=b.preferred_date,
                time_slot=b.time_slot,
                collection_address=b.collection_address,
                notes=b.notes,
                status=b.status,
                total_price=b.total_price,
                test=LabTestResponse.model_validate(b.test) if b.test else None,
                created_at=b.created_at,
                updated_at=b.updated_at,
            )
            for b in bookings
        ],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )



@router.put(
    "/bookings/{booking_id}/status",
    response_model=LabBookingAdminResponse,
    summary="[Admin/Lab Staff] Update lab booking status",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def update_lab_booking_status(
    booking_id: uuid.UUID,
    body: LabBookingStatusUpdate,
    db: DBSession,
):
    result = await db.execute(
        select(LabBooking, User, Branch)
        .join(User, LabBooking.patient_id == User.id)
        .join(Branch, LabBooking.branch_id == Branch.id)
        .options(selectinload(LabBooking.test))
        .where(LabBooking.id == booking_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Lab booking not found.")
    booking, patient, branch = row

    allowed = VALID_BOOKING_STATUS_TRANSITIONS.get(booking.status, set())
    if body.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot transition from '{booking.status.value}' to '{body.status.value}'. "
                f"Allowed: {[s.value for s in allowed] or 'none'}."
            ),
        )
    booking.status = body.status
    await db.flush()

    return _build_booking_admin_response(booking, patient, branch)


@router.get(
    "/patients/search",
    response_model=list[PatientSearchResult],
    summary="[Admin/Lab Staff] Search patients by name or phone (for report upload)",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager, UserRole.lab_staff)],
)
async def search_patients(
    db: DBSession,
    q: str = Query(..., min_length=2),
):
    term = f"%{q.strip()}%"
    result = await db.execute(
        select(User)
        .where(
            User.role == UserRole.patient,
            User.deleted_at.is_(None),
            or_(User.full_name.ilike(term), User.phone.ilike(term)),
        )
        .order_by(User.full_name.asc())
        .limit(15)
    )
    patients = result.scalars().all()
    return [PatientSearchResult.model_validate(p) for p in patients]
