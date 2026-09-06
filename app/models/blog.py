"""
BlogPost model — health articles for the "Top Articles of the Day" section.

Partial unique index on slug: uniqueness enforced only WHERE deleted_at IS NULL.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin


class BlogPost(SoftDeleteMixin, Base):
    __tablename__ = "blog_posts"
    __table_args__ = (
        # Partial unique index — reuse slugs of deleted posts
        Index(
            "ix_blog_posts_slug_active",
            "slug",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    slug: Mapped[str] = mapped_column(String(500), nullable=False)  # partial unique via index
    content: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    view_count: Mapped[int] = mapped_column(default=0, nullable=False)

    # SEO metadata for Next.js frontend
    meta_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta_description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Relationships ──────────────────────────────────────────
    author: Mapped[User | None] = relationship(back_populates="blog_posts")  # noqa: F821

    def __repr__(self) -> str:
        return f"<BlogPost slug={self.slug!r} published={self.is_published}>"
