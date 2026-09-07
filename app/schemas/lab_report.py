"""
Pydantic schemas for Lab Reports.
Complies with private bucket storage: returns metadata only,
short-lived signed download URLs via dedicated endpoint.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.lab_report import ReportFileType


class LabReportResponse(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    test_id: uuid.UUID | None
    test_name: str | None = None
    test_code: str | None = None
    appointment_id: uuid.UUID | None
    file_name: str
    file_type: ReportFileType
    is_visible: bool
    uploaded_by: uuid.UUID | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LabReportDownloadResponse(BaseModel):
    download_url: str = Field(..., description="Short-lived presigned URL (10 min expiry)")
    expires_in: int = Field(600, description="Expiration time in seconds")
    file_name: str


class LabReportUpdate(BaseModel):
    is_visible: bool | None = None
    notes: str | None = Field(None, max_length=1000)


class LabReportListResponse(BaseModel):
    items: list[LabReportResponse]
    total: int
    page: int
    limit: int
    pages: int
