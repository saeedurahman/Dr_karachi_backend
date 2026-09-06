"""
LabTest model — catalog of tests and packages offered per branch.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin


class LabTest(SoftDeleteMixin, Base):
    __tablename__ = "lab_tests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    home_sampling_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    home_sampling_fee: Mapped[float] = mapped_column(
        Numeric(10, 2), default=0.00, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    turnaround_hours: Mapped[int | None] = mapped_column(nullable=True)

    # ── Relationships ──────────────────────────────────────────
    branch: Mapped[Branch | None] = relationship(back_populates="lab_tests")  # noqa: F821
    lab_reports: Mapped[list[LabReport]] = relationship(  # noqa: F821
        back_populates="test", foreign_keys="LabReport.test_id"
    )

    def __repr__(self) -> str:
        return f"<LabTest code={self.code!r} name={self.name!r}>"
