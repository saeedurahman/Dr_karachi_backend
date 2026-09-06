"""
User model — central identity table.

Role hierarchy:
  super_admin > branch_manager > pharmacy_staff | lab_staff | doctor > patient
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database_base import Base
from app.models.base import SoftDeleteMixin


class UserRole(str, enum.Enum):
    super_admin = "super_admin"
    branch_manager = "branch_manager"
    pharmacy_staff = "pharmacy_staff"
    lab_staff = "lab_staff"
    doctor = "doctor"
    patient = "patient"


class User(SoftDeleteMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(
        String(320), unique=True, nullable=True, index=True
    )
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="userrole"), nullable=False, default=UserRole.patient
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────────
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan", lazy="dynamic"
    )
    doctor_profile: Mapped["Doctor | None"] = relationship(  # noqa: F821
        back_populates="user", uselist=False
    )
    appointments: Mapped[list["Appointment"]] = relationship(  # noqa: F821
        back_populates="patient", foreign_keys="Appointment.patient_id"
    )
    cart_items: Mapped[list["CartItem"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship(back_populates="patient")  # noqa: F821
    lab_reports: Mapped[list["LabReport"]] = relationship(  # noqa: F821
        back_populates="patient", foreign_keys="LabReport.patient_id"
    )
    reviews: Mapped[list["Review"]] = relationship(back_populates="author")  # noqa: F821
    blog_posts: Mapped[list["BlogPost"]] = relationship(back_populates="author")  # noqa: F821
    notification_events: Mapped[list["NotificationEvent"]] = relationship(  # noqa: F821
        back_populates="user"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} phone={self.phone} role={self.role}>"
