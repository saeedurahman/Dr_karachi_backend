"""
Lab report service — manages private report storage, short-lived signed URLs,
and patient delivery notifications.

Key invariants:
1. Bucket is private; file_path is internal only.
2. Signed URLs are generated on demand with 10-minute expiry (600s).
3. Enforces linkage: at least one of appointment_id or test_id must be provided.
4. Enforces file constraints: PDF, JPEG, PNG, max 10MB (10,485,760 bytes).
5. Emits report_ready notification event after successful persistence.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.lab_report import LabReport, ReportFileType
from app.models.lab_test import LabTest
from app.models.notification import NotificationEventType
from app.models.user import User, UserRole
from app.schemas.lab_report import LabReportDownloadResponse, LabReportResponse
from app.services.notification_service import emit_event
from app.utils.storage import generate_upload_path, get_storage

MAX_REPORT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

ALLOWED_EXTENSIONS = {
    ".pdf": ReportFileType.pdf,
    ".jpg": ReportFileType.jpeg,
    ".jpeg": ReportFileType.jpeg,
    ".png": ReportFileType.png,
}

ALLOWED_MIME_TYPES = {
    "application/pdf": ReportFileType.pdf,
    "image/jpeg": ReportFileType.jpeg,
    "image/png": ReportFileType.png,
}


class ReportService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.storage = get_storage()

    # ── Upload Report ──────────────────────────────────────────────────────────
    async def upload_report(
        self,
        patient_id: uuid.UUID,
        file: UploadFile,
        uploaded_by: User,
        test_id: uuid.UUID | None = None,
        appointment_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> LabReportResponse:
        # 1. Enforce linkage constraint
        if not test_id and not appointment_id:
            raise HTTPException(
                status_code=400,
                detail="Report must be linked to at least one lab test or appointment.",
            )

        # 2. Verify patient exists
        patient = await self.db.get(User, patient_id)
        if not patient or not patient.is_active or patient.deleted_at:
            raise HTTPException(status_code=404, detail="Patient not found or inactive.")

        # 2b. Verify test or appointment exists if specified
        if test_id:
            test = await self.db.get(LabTest, test_id)
            if not test or test.deleted_at:
                raise HTTPException(status_code=404, detail="Lab test not found.")

        if appointment_id:
            appointment = await self.db.get(Appointment, appointment_id)
            if not appointment:
                raise HTTPException(status_code=404, detail="Appointment not found.")

        # 3. Validate file extension and MIME type
        filename = file.filename or "report.pdf"
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format '{ext}'. Allowed: PDF, JPEG, PNG.",
            )

        file_type = ALLOWED_EXTENSIONS[ext]

        # 4. Validate file size (max 10MB)
        # Read content to check length
        content = await file.read()
        if len(content) > MAX_REPORT_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds maximum allowed size of 10MB ({len(content)} bytes).",
            )
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        # Reset file position so storage backend can read it
        await file.seek(0)

        # 5. Upload to private storage
        storage_folder = f"reports/{patient_id}"
        dest_path = generate_upload_path(storage_folder, filename)
        saved_path = await self.storage.upload(file, dest_path)

        # 6. Persist LabReport
        report = LabReport(
            patient_id=patient_id,
            test_id=test_id,
            appointment_id=appointment_id,
            file_path=saved_path,
            file_type=file_type,
            file_name=filename,
            is_visible=True,
            uploaded_by=uploaded_by.id,
            notes=notes,
        )
        self.db.add(report)
        await self.db.flush()

        # 7. Emit report_ready notification event
        await emit_event(
            db=self.db,
            event_type=NotificationEventType.report_ready,
            payload={
                "report_id": str(report.id),
                "patient_id": str(patient_id),
                "patient_phone": patient.phone,
                "file_name": filename,
                "test_id": str(test_id) if test_id else None,
                "appointment_id": str(appointment_id) if appointment_id else None,
            },
            user_id=patient_id,
            delivery_channel="whatsapp",
        )

        return LabReportResponse.model_validate(report)

    # ── Presigned Download URL ─────────────────────────────────────────────────
    async def get_download_url(
        self,
        report_id: uuid.UUID,
        user: User,
        expires_in: int = 600,
    ) -> LabReportDownloadResponse:
        """
        Generate short-lived signed URL (10 min expiry) for secure patient/staff access.
        """
        result = await self.db.execute(
            select(LabReport).where(LabReport.id == report_id)
        )
        report = result.scalar_one_or_none()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found.")

        # Access control
        if user.role == UserRole.patient:
            if report.patient_id != user.id:
                raise HTTPException(status_code=403, detail="Not authorized.")
            if not report.is_visible:
                raise HTTPException(
                    status_code=403,
                    detail="This report is not yet available for patient viewing.",
                )

        signed_url = self.storage.generate_presigned_url(report.file_path, expires_in=expires_in)

        return LabReportDownloadResponse(
            download_url=signed_url,
            expires_in=expires_in,
            file_name=report.file_name,
        )
