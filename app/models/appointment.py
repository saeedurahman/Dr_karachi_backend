"""
Appointment model.

Slots are fixed 30-minute duration (APPOINTMENT_SLOT_DURATION_MINUTES = 30).
Conflict checking is enforced at the service layer, not at the DB level,
to provide a friendly error message. A composite index on
(doctor_id, slot_datetime, status) speeds up conflict queries.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base
from app.models.base import TimestampMixin


class AppointmentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"
    no_show = "no_show"


class Appointment(TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointment_doctor_slot", "doctor_id", "slot_datetime"),
        Index(
            "uq_active_doctor_slot",
            "doctor_id",
            "slot_datetime",
            unique=True,
            postgresql_where=text("status NOT IN ('cancelled', 'no_show')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Exact datetime of the 30-min slot start
    slot_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, name="appointmentstatus"),
        default=AppointmentStatus.pending,
        nullable=False,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # â”€â”€ Relationships â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    patient: Mapped[User] = relationship(  # noqa: F821
        back_populates="appointments", foreign_keys=[patient_id]
    )
    doctor: Mapped[Doctor] = relationship(  # noqa: F821
        back_populates="appointments", foreign_keys=[doctor_id]
    )
    branch: Mapped[Branch] = relationship(back_populates="appointments")  # noqa: F821
    lab_reports: Mapped[list[LabReport]] = relationship(  # noqa: F821
        back_populates="appointment", foreign_keys="LabReport.appointment_id"
    )

    def __repr__(self) -> str:
        return f"<Appointment id={self.id} slot={self.slot_datetime} status={self.status}>"
