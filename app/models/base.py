"""
Shared base mixin for soft-delete pattern.

Any model that inherits SoftDeleteMixin gets a `deleted_at` column.
Use Model.query_active() or filter manually: WHERE deleted_at IS NULL.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Adds created_at and updated_at to any model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin(TimestampMixin):
    """
    Adds soft-delete support via deleted_at column.

    Deleted records remain in the DB but are excluded from
    active queries by filtering WHERE deleted_at IS NULL.
    Partial unique indexes reference this column so that
    soft-deleted slugs/SKUs can be reused.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(UTC)

    def restore(self) -> None:
        self.deleted_at = None
