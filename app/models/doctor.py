"""
Doctor models:
  - Doctor            â€” profile linked to a User account
  - DoctorBranch      â€” many-to-many: which branches a doctor serves
  - DoctorAvailabilityâ€” weekly schedule (day + start/end time)
"""
from __future__ import annotations

import enum
import uuid
from datetime import time

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base
from app.models.base import SoftDeleteMixin, TimestampMixin


class DayOfWeek(int, enum.Enum):
    monday = 0
    tuesday = 1
    wednesday = 2
    thursday = 3
    friday = 4
    saturday = 5
    sunday = 6


class Doctor(SoftDeleteMixin, Base):
    __tablename__ = "doctors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    specialization: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    qualification: Mapped[str] = mapped_column(String(500), nullable=False)
    experience_years: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consultation_fee: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # â”€â”€ Relationships â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    user: Mapped[User] = relationship(back_populates="doctor_profile")  # noqa: F821
    doctor_branches: Mapped[list[DoctorBranch]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )
    availability: Mapped[list[DoctorAvailability]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )
    appointments: Mapped[list[Appointment]] = relationship(  # noqa: F821
        back_populates="doctor", foreign_keys="Appointment.doctor_id"
    )

    def __repr__(self) -> str:
        return f"<Doctor id={self.id} spec={self.specialization!r}>"


class DoctorBranch(TimestampMixin, Base):
    """Association table: doctor â†” branch (many-to-many)."""

    __tablename__ = "doctor_branches"
    __table_args__ = (
        UniqueConstraint("doctor_id", "branch_id", name="uq_doctor_branch"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # â”€â”€ Relationships â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    doctor: Mapped[Doctor] = relationship(back_populates="doctor_branches")
    branch: Mapped[Branch] = relationship(back_populates="doctor_branches")  # noqa: F821


class DoctorAvailability(TimestampMixin, Base):
    """
    Weekly recurring schedule for a doctor at a specific branch.
    Slot duration is governed by APPOINTMENT_SLOT_DURATION_MINUTES (30 min).
    """

    __tablename__ = "doctor_availability"
    __table_args__ = (
        UniqueConstraint(
            "doctor_id", "branch_id", "day_of_week", "start_time",
            name="uq_doctor_availability_slot"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_of_week: Mapped[int] = mapped_column(
        Enum(DayOfWeek, name="dayofweek"), nullable=False
    )
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # â”€â”€ Relationships â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    doctor: Mapped[Doctor] = relationship(back_populates="availability")
    branch: Mapped[Branch] = relationship()  # noqa: F821
