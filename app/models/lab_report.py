"""
LabReport model.

A report can be linked to either a LabTest or an Appointment (or both).
file_url points to the uploaded file in storage (S3/local).
Allowed file types: PDF, JPEG, PNG.

uploaded_by tracks which staff member uploaded the report.
is_visible controls patient visibility (staff can toggle).
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class ReportFileType(str, enum.Enum):
    pdf = "pdf"
    jpeg = "jpeg"
    png = "png"


class LabReport(TimestampMixin, Base):
    __tablename__ = "lab_reports"
    __table_args__ = (
        CheckConstraint(
            "appointment_id IS NOT NULL OR test_id IS NOT NULL",
            name="ck_lab_reports_has_linkage",
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

    # Either test_id or appointment_id (or both) should be set
    test_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lab_tests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Internal private storage path (e.g. reports/{patient_id}/{uuid}.pdf) — signed URL generated on demand
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[ReportFileType] = mapped_column(
        Enum(ReportFileType, name="reportfiletype"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Staff who uploaded — stored as UUID; nullable if auto-generated
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────
    patient: Mapped["User"] = relationship(  # noqa: F821
        back_populates="lab_reports", foreign_keys=[patient_id]
    )
    test: Mapped["LabTest | None"] = relationship(  # noqa: F821
        back_populates="lab_reports", foreign_keys=[test_id]
    )
    appointment: Mapped["Appointment | None"] = relationship(  # noqa: F821
        back_populates="lab_reports", foreign_keys=[appointment_id]
    )

    def __repr__(self) -> str:
        return f"<LabReport patient={self.patient_id} type={self.file_type}>"
