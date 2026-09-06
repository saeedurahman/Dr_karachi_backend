"""
FranchiseLead model — simple lead capture form submissions.
No auth required on creation; admin reads via dashboard.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database_base import Base
from app.models.base import TimestampMixin


class InvestmentRange(str, enum.Enum):
    under_1m = "under_1m"       # Under 1 million PKR
    one_to_3m = "1m_to_3m"
    three_to_5m = "3m_to_5m"
    five_to_10m = "5m_to_10m"
    above_10m = "above_10m"


class FranchiseLeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    closed = "closed"


class FranchiseLead(TimestampMixin, Base):
    __tablename__ = "franchise_leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    investment_range: Mapped[InvestmentRange] = mapped_column(
        Enum(InvestmentRange, name="investmentrange"), nullable=False
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[FranchiseLeadStatus] = mapped_column(
        Enum(FranchiseLeadStatus, name="franchiseleadstatus"),
        default=FranchiseLeadStatus.new,
        nullable=False,
        index=True,
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<FranchiseLead name={self.full_name!r} city={self.city!r} status={self.status}>"

