"""
Blog router — /api/v1/blog

Endpoints:
  GET    /             → Public paginated list of published articles
  GET    /top          → Top articles of the day (featured posts pinned)
  GET    /{slug}       → Article detail (increments view_count)
  POST   /             → [Staff/Admin] Create article (auto-slug)
  PUT    /{id}         → [Staff/Admin] Update article
  DELETE /{id}         → [Staff/Admin] Soft-delete article
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.dependencies import CurrentUser, DBSession, require_roles
from app.models.blog import BlogPost
from app.models.user import UserRole
from app.schemas.blog import (
    BlogPostCreate,
    BlogPostListResponse,
    BlogPostResponse,
    BlogPostUpdate,
)
from app.services.blog_service import BlogService

router = APIRouter(prefix="/blog", tags=["Blog"])


@router.get(
    "",
    response_model=BlogPostListResponse,
    summary="List published blog posts (paginated)",
)
async def list_blog_posts(
    db: DBSession,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    search: str | None = None,
):
    query = (
        select(BlogPost)
        .where(
            BlogPost.is_published.is_(True),
            BlogPost.deleted_at.is_(None),
        )
        .options(selectinload(BlogPost.author))
    )

    if search:
        term = f"%{search.strip()}%"
        query = query.where(BlogPost.title.ilike(term))

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * limit
    items_query = query.order_by(BlogPost.published_at.desc()).offset(offset).limit(limit)
    items_result = await db.execute(items_query)
    posts = items_result.scalars().all()

    service = BlogService(db)
    pages = math.ceil(total / limit) if limit > 0 else 1

    return BlogPostListResponse(
        items=[service._build_response(p, p.author) for p in posts],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/top",
    response_model=list[BlogPostResponse],
    summary="Get top articles of the day (featured posts pinned)",
)
async def get_top_articles(
    db: DBSession,
    limit: int = Query(5, ge=1, le=20),
):
    service = BlogService(db)
    return await service.get_top_articles(limit=limit)


@router.get(
    "/{slug}",
    response_model=BlogPostResponse,
    summary="Get blog post by slug (increments view count)",
)
async def get_blog_post(slug: str, db: DBSession):
    service = BlogService(db)
    return await service.get_by_slug(slug=slug)


@router.post(
    "",
    response_model=BlogPostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Staff/Admin] Create blog post",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def create_blog_post(
    body: BlogPostCreate,
    current_user: CurrentUser,
    db: DBSession,
):
    service = BlogService(db)
    return await service.create_post(author=current_user, body=body)


@router.put(
    "/{post_id}",
    response_model=BlogPostResponse,
    summary="[Staff/Admin] Update blog post",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def update_blog_post(
    post_id: uuid.UUID,
    body: BlogPostUpdate,
    db: DBSession,
):
    service = BlogService(db)
    return await service.update_post(post_id=post_id, body=body)


@router.delete(
    "/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="[Staff/Admin] Soft-delete blog post",
    dependencies=[require_roles(UserRole.super_admin, UserRole.branch_manager)],
)
async def delete_blog_post(post_id: uuid.UUID, db: DBSession):
    result = await db.execute(
        select(BlogPost).where(BlogPost.id == post_id, BlogPost.deleted_at.is_(None))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Blog post not found.")

    post.deleted_at = datetime.now(timezone.utc)
    post.is_published = False
    await db.flush()
