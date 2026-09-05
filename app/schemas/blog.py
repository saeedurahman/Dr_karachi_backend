"""
Pydantic schemas for Blog Posts.
Includes auto-slug support, is_featured for Top Articles,
view counts, and SEO meta tags.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AuthorSummary(BaseModel):
    id: uuid.UUID
    full_name: str

    class Config:
        from_attributes = True


class BlogPostCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=500)
    slug: str | None = Field(None, max_length=500, description="Optional; auto-generated from title if omitted")
    content: str = Field(..., min_length=10)
    excerpt: str | None = Field(None, max_length=1000)
    cover_image_url: str | None = None
    is_featured: bool = False
    is_published: bool = False
    meta_title: str | None = Field(None, max_length=255)
    meta_description: str | None = Field(None, max_length=500)


class BlogPostUpdate(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=500)
    slug: str | None = Field(None, max_length=500)
    content: str | None = Field(None, min_length=10)
    excerpt: str | None = None
    cover_image_url: str | None = None
    is_featured: bool | None = None
    is_published: bool | None = None
    meta_title: str | None = None
    meta_description: str | None = None


class BlogPostResponse(BaseModel):
    id: uuid.UUID
    title: str
    slug: str
    content: str
    excerpt: str | None
    cover_image_url: str | None
    author_id: uuid.UUID | None
    author: AuthorSummary | None = None
    is_published: bool
    is_featured: bool
    published_at: datetime | None
    view_count: int
    meta_title: str | None
    meta_description: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BlogPostListResponse(BaseModel):
    items: list[BlogPostResponse]
    total: int
    page: int
    limit: int
    pages: int
