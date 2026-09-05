"""
Blog service — handles post publishing, unique slug generation,
view counting, and top featured articles.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.blog import BlogPost
from app.models.notification import NotificationEventType
from app.models.user import User
from app.schemas.blog import AuthorSummary, BlogPostCreate, BlogPostResponse, BlogPostUpdate
from app.services.notification_service import emit_event
from app.utils.slug import slugify


class BlogService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate_unique_slug(self, title: str, exclude_id: uuid.UUID | None = None) -> str:
        base_slug = slugify(title)
        if not base_slug:
            base_slug = f"post-{uuid.uuid4().hex[:8]}"

        slug = base_slug
        counter = 1

        while True:
            query = select(BlogPost.id).where(
                BlogPost.slug == slug,
                BlogPost.deleted_at.is_(None),
            )
            if exclude_id:
                query = query.where(BlogPost.id != exclude_id)

            res = await self.db.execute(query)
            if not res.scalar_one_or_none():
                return slug

            counter += 1
            slug = f"{base_slug}-{counter}"

    async def create_post(self, author: User, body: BlogPostCreate) -> BlogPostResponse:
        slug = body.slug.strip() if body.slug else None
        if not slug:
            slug = await self.generate_unique_slug(body.title)
        else:
            # Check custom slug uniqueness
            existing = await self.db.execute(
                select(BlogPost.id).where(BlogPost.slug == slug, BlogPost.deleted_at.is_(None))
            )
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=409, detail=f"Slug '{slug}' is already in use.")

        published_at = datetime.now(timezone.utc) if body.is_published else None

        post = BlogPost(
            title=body.title,
            slug=slug,
            content=body.content,
            excerpt=body.excerpt,
            cover_image_url=body.cover_image_url,
            author_id=author.id,
            is_published=body.is_published,
            is_featured=body.is_featured,
            published_at=published_at,
            meta_title=body.meta_title,
            meta_description=body.meta_description,
        )
        self.db.add(post)
        await self.db.flush()

        if body.is_published:
            await emit_event(
                db=self.db,
                event_type=NotificationEventType.blog_published,
                payload={"post_id": str(post.id), "title": post.title, "slug": post.slug},
                user_id=author.id,
            )

        return self._build_response(post, author)

    async def update_post(
        self,
        post_id: uuid.UUID,
        body: BlogPostUpdate,
    ) -> BlogPostResponse:
        result = await self.db.execute(
            select(BlogPost)
            .where(BlogPost.id == post_id, BlogPost.deleted_at.is_(None))
            .options(selectinload(BlogPost.author))
        )
        post = result.scalar_one_or_none()
        if not post:
            raise HTTPException(status_code=404, detail="Blog post not found.")

        data = body.model_dump(exclude_unset=True)

        # Handle slug update
        if "slug" in data and data["slug"]:
            new_slug = data["slug"].strip()
            if new_slug != post.slug:
                conflict = await self.db.execute(
                    select(BlogPost.id).where(
                        BlogPost.slug == new_slug,
                        BlogPost.id != post_id,
                        BlogPost.deleted_at.is_(None),
                    )
                )
                if conflict.scalar_one_or_none():
                    raise HTTPException(status_code=409, detail=f"Slug '{new_slug}' is already in use.")
                post.slug = new_slug
        elif "title" in data and not post.slug:
            post.slug = await self.generate_unique_slug(data["title"], exclude_id=post_id)

        # Transitioning to published
        was_published = post.is_published
        if data.get("is_published") and not was_published:
            post.published_at = datetime.now(timezone.utc)
            await emit_event(
                db=self.db,
                event_type=NotificationEventType.blog_published,
                payload={"post_id": str(post.id), "title": post.title, "slug": post.slug},
            )

        for k, v in data.items():
            if k != "slug":
                setattr(post, k, v)

        await self.db.flush()
        return self._build_response(post, post.author)

    async def get_by_slug(self, slug: str) -> BlogPostResponse:
        result = await self.db.execute(
            select(BlogPost)
            .where(
                BlogPost.slug == slug,
                BlogPost.is_published.is_(True),
                BlogPost.deleted_at.is_(None),
            )
            .options(selectinload(BlogPost.author))
        )
        post = result.scalar_one_or_none()
        if not post:
            raise HTTPException(status_code=404, detail="Blog post not found.")

        # Increment view count
        post.view_count += 1
        await self.db.flush()

        return self._build_response(post, post.author)

    async def get_top_articles(self, limit: int = 5) -> list[BlogPostResponse]:
        """
        Top articles of the day:
        Prioritizes admin-pinned featured posts (is_featured=True),
        ordered by published_at DESC. If fewer than `limit`, backfills with
        latest published posts.
        """
        # 1. Fetch featured
        featured_res = await self.db.execute(
            select(BlogPost)
            .where(
                BlogPost.is_published.is_(True),
                BlogPost.is_featured.is_(True),
                BlogPost.deleted_at.is_(None),
            )
            .options(selectinload(BlogPost.author))
            .order_by(BlogPost.published_at.desc())
            .limit(limit)
        )
        posts = list(featured_res.scalars().all())

        # 2. Backfill if needed
        if len(posts) < limit:
            remaining = limit - len(posts)
            existing_ids = [p.id for p in posts]
            backfill_query = (
                select(BlogPost)
                .where(
                    BlogPost.is_published.is_(True),
                    BlogPost.deleted_at.is_(None),
                )
                .options(selectinload(BlogPost.author))
                .order_by(BlogPost.published_at.desc())
                .limit(remaining)
            )
            if existing_ids:
                backfill_query = backfill_query.where(BlogPost.id.notin_(existing_ids))

            backfill_res = await self.db.execute(backfill_query)
            posts.extend(backfill_res.scalars().all())

        return [self._build_response(p, p.author) for p in posts]

    @staticmethod
    def _build_response(post: BlogPost, author: User | None) -> BlogPostResponse:
        author_summary = None
        if author:
            author_summary = AuthorSummary(id=author.id, full_name=author.full_name)
        return BlogPostResponse(
            id=post.id,
            title=post.title,
            slug=post.slug,
            content=post.content,
            excerpt=post.excerpt,
            cover_image_url=post.cover_image_url,
            author_id=post.author_id,
            author=author_summary,
            is_published=post.is_published,
            is_featured=post.is_featured,
            published_at=post.published_at,
            view_count=post.view_count,
            meta_title=post.meta_title,
            meta_description=post.meta_description,
            created_at=post.created_at,
            updated_at=post.updated_at,
        )
