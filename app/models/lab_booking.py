"""
LabBooking model — handles patient bookings for Diagnostic Lab Tests.

Supports both:
  1. In-clinic sample collection at a branch (clinic_visit)
  2. Home sample collection with home sampling fee and address (home_sampling)
"""
from __future__ import annotations

import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Enum as SaEnum, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base
from app.models.base import TimestampMixin


class CollectionType(str, enum.Enum):
    clinic_visit = "clinic_visit"
    home_sampling = "home_sampling"


class LabBookingStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    sample_collected = "sample_collected"
    completed = "completed"
    cancelled = "cancelled"


# ── Valid status transitions (enforced by the admin bookings router) ──────────
VALID_BOOKING_STATUS_TRANSITIONS: dict[LabBookingStatus, set[LabBookingStatus]] = {
    LabBookingStatus.pending: {LabBookingStatus.confirmed, LabBookingStatus.cancelled},
    LabBookingStatus.confirmed: {LabBookingStatus.sample_collected, LabBookingStatus.cancelled},
    LabBookingStatus.sample_collected: {LabBookingStatus.completed, LabBookingStatus.cancelled},
    LabBookingStatus.completed: set(),
    LabBookingStatus.cancelled: set(),
}


class LabBooking(TimestampMixin, Base):
    __tablename__ = "lab_bookings"
    __table_args__ = (
        Index("ix_lab_bookings_patient_id", "patient_id"),
        Index("ix_lab_bookings_test_id", "test_id"),
        Index("ix_lab_bookings_branch_id", "branch_id"),
        Index("ix_lab_bookings_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lab_tests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    collection_type: Mapped[CollectionType] = mapped_column(
        SaEnum(CollectionType, name="collectiontype"),
        default=CollectionType.clinic_visit,
        nullable=False,
    )
    preferred_date: Mapped[date] = mapped_column(Date, nullable=False)
    time_slot: Mapped[str] = mapped_column(String(50), nullable=False)
    collection_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[LabBookingStatus] = mapped_column(
        SaEnum(LabBookingStatus, name="labbookingstatus"),
        default=LabBookingStatus.pending,
        nullable=False,
    )
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Relationships
    patient = relationship("User", foreign_keys=[patient_id])
    test = relationship("LabTest", foreign_keys=[test_id])
    branch = relationship("Branch", foreign_keys=[branch_id])

    def __repr__(self) -> str:
        return f"<LabBooking id={self.id} test_id={self.test_id} status={self.status}>"
