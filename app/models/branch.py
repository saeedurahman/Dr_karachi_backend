"""
Branch model — represents a physical clinic/pharmacy/lab location.

working_hours and services are stored as JSON for flexibility.
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class Branch(TimestampMixin, Base):
    __tablename__ = "branches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    google_maps_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # JSON shape: {"mon": {"open": "09:00", "close": "21:00"}, ...}
    working_hours: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # JSON shape: ["pharmacy", "lab", "consultations"]
    services: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # ── Relationships ──────────────────────────────────────────
    doctor_branches: Mapped[list[DoctorBranch]] = relationship(  # noqa: F821
        back_populates="branch"
    )
    appointments: Mapped[list[Appointment]] = relationship(  # noqa: F821
        back_populates="branch"
    )
    branch_stock: Mapped[list[BranchStock]] = relationship(  # noqa: F821
        back_populates="branch"
    )
    cart_items: Mapped[list[CartItem]] = relationship(back_populates="branch")  # noqa: F821
    orders: Mapped[list[Order]] = relationship(back_populates="branch")  # noqa: F821
    lab_tests: Mapped[list[LabTest]] = relationship(back_populates="branch")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Branch name={self.name!r} city={self.city!r}>"
