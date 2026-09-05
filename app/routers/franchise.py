"""
Franchise leads router — /api/v1/franchise-leads

Endpoints:
  POST /        → Public lead capture (SlowAPI rate-limited: 5/minute)
  GET  /        → [Admin] List leads (paginated, with filters)
  GET  /{id}    → [Admin] Lead detail (marks is_read = True)
  PUT  /{id}    → [Admin] Update lead status (new/contacted/closed), notes, is_read
"""
from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.dependencies import DBSession, require_roles
from app.models.franchise import FranchiseLead, FranchiseLeadStatus, InvestmentRange
from app.models.notification import NotificationEventType
from app.models.user import UserRole
from app.schemas.franchise import (
    FranchiseLeadCreate,
    FranchiseLeadListResponse,
    FranchiseLeadResponse,
    FranchiseLeadUpdate,
)
from app.services.notification_service import emit_event
from app.utils.limiter import limiter

router = APIRouter(prefix="/franchise-leads", tags=["Franchise Leads"])


@router.post(
    "",
    response_model=FranchiseLeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit franchise inquiry (Public, rate-limited: 5/minute)",
)
@limiter.limit("5/minute")
async def submit_franchise_lead(
    request: Request,
    body: FranchiseLeadCreate,
    db: DBSession,
):
    lead = FranchiseLead(
        full_name=body.full_name,
        phone=body.phone,
        email=body.email,
        city=body.city,
        investment_range=body.investment_range,
        message=body.message,
        status=FranchiseLeadStatus.new,
        is_read=False,
    )
    db.add(lead)
    await db.flush()

    # Emit notification event to alert administration
    await emit_event(
        db=db,
        event_type=NotificationEventType.franchise_lead_received,
        payload={
            "lead_id": str(lead.id),
            "full_name": lead.full_name,
            "phone": lead.phone,
            "city": lead.city,
            "investment_range": lead.investment_range.value,
        },
        delivery_channel="whatsapp",
    )

    return FranchiseLeadResponse.model_validate(lead)


@router.get(
    "",
    response_model=FranchiseLeadListResponse,
    summary="[Admin] List franchise leads",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def list_franchise_leads(
    db: DBSession,
    status_filter: FranchiseLeadStatus | None = Query(None, alias="status"),
    is_read: bool | None = None,
    city: str | None = None,
    investment_range: InvestmentRange | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    query = select(FranchiseLead)

    if status_filter:
        query = query.where(FranchiseLead.status == status_filter)
    if is_read is not None:
        query = query.where(FranchiseLead.is_read == is_read)
    if city:
        query = query.where(FranchiseLead.city.ilike(f"%{city.strip()}%"))
    if investment_range:
        query = query.where(FranchiseLead.investment_range == investment_range)

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * limit
    items_query = query.order_by(FranchiseLead.created_at.desc()).offset(offset).limit(limit)
    items_result = await db.execute(items_query)
    leads = items_result.scalars().all()

    pages = math.ceil(total / limit) if limit > 0 else 1

    return FranchiseLeadListResponse(
        items=[FranchiseLeadResponse.model_validate(l) for l in leads],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/{lead_id}",
    response_model=FranchiseLeadResponse,
    summary="[Admin] Get lead detail and mark as read",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def get_franchise_lead(lead_id: uuid.UUID, db: DBSession):
    result = await db.execute(select(FranchiseLead).where(FranchiseLead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Franchise lead not found.")

    if not lead.is_read:
        lead.is_read = True
        await db.flush()

    return FranchiseLeadResponse.model_validate(lead)


@router.put(
    "/{lead_id}",
    response_model=FranchiseLeadResponse,
    summary="[Admin] Update lead status, notes, or read state",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def update_franchise_lead(
    lead_id: uuid.UUID,
    body: FranchiseLeadUpdate,
    db: DBSession,
):
    result = await db.execute(select(FranchiseLead).where(FranchiseLead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Franchise lead not found.")

    if body.status is not None:
        lead.status = body.status
    if body.is_read is not None:
        lead.is_read = body.is_read
    if body.notes is not None:
        lead.notes = body.notes

    await db.flush()
    return FranchiseLeadResponse.model_validate(lead)
