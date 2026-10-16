"""Admin endpoints for WhatsApp marketing broadcasts and campaigns."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.user import UserRole
from app.models.whatsapp_broadcast import WhatsAppBroadcast
from app.schemas.whatsapp_broadcast import (
    BroadcastCreate,
    BroadcastPreviewResponse,
    BroadcastResponse,
    BroadcastStats,
    MarketingTemplateResponse,
)
from app.services.whatsapp.broadcast_service import BroadcastService
from app.services.whatsapp.templates import template_registry

router = APIRouter(
    prefix="/admin/whatsapp/broadcasts",
    tags=["Admin WhatsApp Broadcasts"],
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)

templates_router = APIRouter(
    prefix="/admin/whatsapp/templates",
    tags=["Admin WhatsApp Templates"],
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)


@templates_router.get(
    "",
    response_model=list[MarketingTemplateResponse],
    summary="[Admin] List marketing WhatsApp templates",
)
async def list_marketing_templates() -> list[MarketingTemplateResponse]:
    """Return only templates with category 'marketing' from the TemplateRegistry.

    Includes key, supported languages, ordered parameter names, and opt_out_hint.
    Never exposes secrets or Meta IDs.
    """
    templates: list[MarketingTemplateResponse] = []
    for key, tpl in template_registry._templates.items():
        if tpl.category == "marketing":
            langs = list(tpl.languages.keys())
            params = list(tpl.body_params)
            templates.append(
                MarketingTemplateResponse(
                    key=tpl.key,
                    template_name=tpl.template_name,
                    category=tpl.category,
                    languages=langs,
                    supported_languages=langs,
                    parameter_names=params,
                    ordered_parameter_names=params,
                    ordered_parameters=params,
                    body_params=params,
                    opt_out_hint=tpl.opt_out_hint,
                )
            )
    return templates



def _assert_branch_access(current_user: Any, broadcast: WhatsAppBroadcast) -> None:
    """Enforce that branch managers can only access broadcasts belonging to their branch."""
    if current_user.role == UserRole.branch_manager:
        user_branch = getattr(current_user, "branch_id", None)
        if broadcast.branch_id is None or (user_branch is not None and broadcast.branch_id != user_branch):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Branch managers can only access broadcasts scoped to their branch.",
            )


@router.post(
    "",
    response_model=BroadcastResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Create marketing broadcast",
)
async def create_broadcast(
    body: BroadcastCreate,
    current_user: CurrentUser,
    db: DBSession,
):
    """Create a new WhatsApp marketing broadcast.

    Enforces:
    - Super admins can broadcast to all or a specific branch.
    - Branch managers can ONLY broadcast to their own branch (forced branch_id).
    - Only marketing category templates are allowed.
    - Static parameters are validated against TemplateRegistry.
    """
    branch_id: uuid.UUID | None = None

    if current_user.role == UserRole.branch_manager:
        user_branch = getattr(current_user, "branch_id", None)
        if user_branch is not None:
            branch_id = user_branch
        elif "branch_id" in body.segment_filters and body.segment_filters["branch_id"]:
            try:
                branch_id = uuid.UUID(str(body.segment_filters["branch_id"]))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid branch_id in segment filters.",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Branch managers must specify their branch.",
            )
    else:
        # Super admin: optional branch_id filter
        filter_branch = body.segment_filters.get("branch_id")
        if filter_branch:
            try:
                branch_id = uuid.UUID(str(filter_branch))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid branch_id in segment filters.",
                )

    try:
        broadcast = await BroadcastService.create_broadcast(
            db=db,
            name=body.name,
            template_key=body.template_key,
            created_by=current_user.id,
            language=body.language,
            params=body.params,
            segment_filters=body.segment_filters,
            branch_id=branch_id,
            scheduled_at=body.scheduled_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    stats_dict = await BroadcastService.get_broadcast_stats(db, broadcast.id)
    resp = BroadcastResponse.model_validate(broadcast)
    resp.stats = BroadcastStats(**stats_dict)
    return resp


@router.post(
    "/{broadcast_id}/preview",
    response_model=BroadcastPreviewResponse,
    summary="[Admin] Preview broadcast recipient resolution and skip counts",
)
async def preview_broadcast(
    broadcast_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
):
    """Preview recipient counts after consent and segmentation filtering without sending."""
    broadcast = await db.get(WhatsAppBroadcast, broadcast_id)
    if not broadcast:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Broadcast not found.",
        )
    _assert_branch_access(current_user, broadcast)

    preview_data = await BroadcastService.preview_broadcast(db, broadcast)
    return BroadcastPreviewResponse(**preview_data)


@router.post(
    "/{broadcast_id}/start",
    response_model=BroadcastResponse,
    summary="[Admin] Start dispatching a broadcast campaign",
)
async def start_broadcast(
    broadcast_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
):
    """Resolve recipients, populate delivery queue, and begin sending."""
    broadcast = await db.get(WhatsAppBroadcast, broadcast_id)
    if not broadcast:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Broadcast not found.",
        )
    _assert_branch_access(current_user, broadcast)

    try:
        started = await BroadcastService.start_broadcast(db, broadcast_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    stats_dict = await BroadcastService.get_broadcast_stats(db, started.id)
    resp = BroadcastResponse.model_validate(started)
    resp.stats = BroadcastStats(**stats_dict)
    return resp


@router.post(
    "/{broadcast_id}/cancel",
    response_model=BroadcastResponse,
    summary="[Admin] Cancel an in-progress or scheduled broadcast",
)
async def cancel_broadcast(
    broadcast_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
):
    """Cancel a broadcast campaign. Stops dispatch immediately."""
    broadcast = await db.get(WhatsAppBroadcast, broadcast_id)
    if not broadcast:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Broadcast not found.",
        )
    _assert_branch_access(current_user, broadcast)

    cancelled = await BroadcastService.cancel_broadcast(db, broadcast_id)
    stats_dict = await BroadcastService.get_broadcast_stats(db, cancelled.id)
    resp = BroadcastResponse.model_validate(cancelled)
    resp.stats = BroadcastStats(**stats_dict)
    return resp


@router.get(
    "",
    response_model=list[BroadcastResponse],
    summary="[Admin] List broadcasts with live stats",
)
async def list_broadcasts(
    current_user: CurrentUser,
    db: DBSession,
    branch_id: uuid.UUID | None = Query(default=None),
):
    """List broadcasts.

    Branch managers only see broadcasts scoped to their branch.
    Super admins see all or filter by query branch_id.
    """
    stmt = select(WhatsAppBroadcast).order_by(WhatsAppBroadcast.created_at.desc())

    if current_user.role == UserRole.branch_manager:
        user_branch = getattr(current_user, "branch_id", None)
        if user_branch is not None:
            stmt = stmt.where(WhatsAppBroadcast.branch_id == user_branch)
        elif branch_id is not None:
            stmt = stmt.where(WhatsAppBroadcast.branch_id == branch_id)
        else:
            # Cannot view unscoped broadcasts
            stmt = stmt.where(WhatsAppBroadcast.branch_id.is_not(None))
    elif branch_id is not None:
        stmt = stmt.where(WhatsAppBroadcast.branch_id == branch_id)

    broadcasts = (await db.execute(stmt)).scalars().all()

    results: list[BroadcastResponse] = []
    for b in broadcasts:
        stats_dict = await BroadcastService.get_broadcast_stats(db, b.id)
        item = BroadcastResponse.model_validate(b)
        item.stats = BroadcastStats(**stats_dict)
        results.append(item)

    return results


@router.get(
    "/{broadcast_id}",
    response_model=BroadcastResponse,
    summary="[Admin] Get broadcast campaign with live statistics",
)
async def get_broadcast(
    broadcast_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
):
    """Retrieve broadcast details with live stats (pending/sent/delivered/read/failed/skipped)."""
    broadcast = await db.get(WhatsAppBroadcast, broadcast_id)
    if not broadcast:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Broadcast not found.",
        )
    _assert_branch_access(current_user, broadcast)

    stats_dict = await BroadcastService.get_broadcast_stats(db, broadcast.id)
    resp = BroadcastResponse.model_validate(broadcast)
    resp.stats = BroadcastStats(**stats_dict)
    return resp
