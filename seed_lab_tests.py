"""
seed_lab_tests.py — Seed 10 clinical lab tests and 2 sample patient reports.

Usage:
    python seed_lab_tests.py

Requirements:
  - Branches must already exist in database
  - Seeded patient (+923001234567) exists in database

Idempotent: skips tests and reports that already exist.
NOTE: Marked as throwaway test fixtures in pre-delivery cleanup register.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.branch import Branch
from app.models.lab_report import LabReport, ReportFileType
from app.models.lab_test import LabTest
from app.models.user import User, UserRole
from app.utils.storage import get_storage

LAB_TESTS = [
    {
        "name": "Complete Blood Count (CBC)",
        "code": "LAB-CBC",
        "description": "Analyzes 14 blood components including RBC, WBC, Hemoglobin, and Platelets to screen for anemia, infections, and general vitality.",
        "price": Decimal("950.00"),
        "home_sampling_available": True,
        "home_sampling_fee": Decimal("300.00"),
        "turnaround_hours": 12,
    },
    {
        "name": "Lipid Profile Panel",
        "code": "LAB-LIPID",
        "description": "Comprehensive cardiac risk panel measuring Total Cholesterol, HDL, LDL, VLDL, and Triglycerides. 12-hour overnight fasting required.",
        "price": Decimal("1800.00"),
        "home_sampling_available": True,
        "home_sampling_fee": Decimal("300.00"),
        "turnaround_hours": 24,
    },
    {
        "name": "Liver Function Test (LFT)",
        "code": "LAB-LFT",
        "description": "Assesses hepatic health and bile duct enzyme activity including Bilirubin, SGPT/ALT, SGOT/AST, and Alkaline Phosphatase.",
        "price": Decimal("2200.00"),
        "home_sampling_available": True,
        "home_sampling_fee": Decimal("300.00"),
        "turnaround_hours": 24,
    },
    {
        "name": "Renal / Kidney Function Test (RFT)",
        "code": "LAB-RFT",
        "description": "Evaluates kidney filtration capacity via Serum Creatinine, Blood Urea Nitrogen (BUN), and Serum Uric Acid.",
        "price": Decimal("1600.00"),
        "home_sampling_available": True,
        "home_sampling_fee": Decimal("300.00"),
        "turnaround_hours": 24,
    },
    {
        "name": "HbA1c (Glycated Hemoglobin)",
        "code": "LAB-HBA1C",
        "description": "Gold-standard 3-month average blood glucose biomarker for diagnosing and managing pre-diabetes and diabetes.",
        "price": Decimal("1400.00"),
        "home_sampling_available": True,
        "home_sampling_fee": Decimal("300.00"),
        "turnaround_hours": 12,
    },
    {
        "name": "Thyroid Profile (TSH, FT3, FT4)",
        "code": "LAB-THYROID",
        "description": "Screens for hyperthyroidism and hypothyroidism by measuring Thyroid Stimulating Hormone and free thyroid hormones.",
        "price": Decimal("2800.00"),
        "home_sampling_available": True,
        "home_sampling_fee": Decimal("300.00"),
        "turnaround_hours": 24,
    },
    {
        "name": "Vitamin D (25-OH Total)",
        "code": "LAB-VITD",
        "description": "High-precision assay detecting Vitamin D deficiency for bone density health, fatigue investigation, and immune support.",
        "price": Decimal("3500.00"),
        "home_sampling_available": True,
        "home_sampling_fee": Decimal("300.00"),
        "turnaround_hours": 48,
    },
    {
        "name": "Urine Detailed Report (D/R)",
        "code": "LAB-URINE-DR",
        "description": "Routine physical, chemical, and microscopic examination screening for urinary tract infections, renal stones, and proteinuria.",
        "price": Decimal("650.00"),
        "home_sampling_available": False,
        "home_sampling_fee": Decimal("0.00"),
        "turnaround_hours": 8,
    },
    {
        "name": "COVID-19 Real-Time PCR",
        "code": "LAB-COVID-PCR",
        "description": "Accredited molecular swab test with QR-coded digital report accepted for international airline travel and hospitalization.",
        "price": Decimal("4500.00"),
        "home_sampling_available": True,
        "home_sampling_fee": Decimal("500.00"),
        "turnaround_hours": 24,
    },
    {
        "name": "Executive Wellness Package (All-in-One)",
        "code": "LAB-PKG-EXEC",
        "description": "Comprehensive annual screening comprising CBC, LFT, RFT, Lipid Profile, Fasting Blood Sugar, and Urine D/R.",
        "price": Decimal("8500.00"),
        "home_sampling_available": True,
        "home_sampling_fee": Decimal("300.00"),
        "turnaround_hours": 48,
    },
]

# Minimal valid PDF payload for sample reports
SAMPLE_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n4 0 obj\n<< /Length 53 >>\nstream\nBT\n/F1 24 Tf\n100 700 Td\n(Dr. Karachi Diagnostic Lab Report) Tj\nET\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000210 00000 n \ntrailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n313\n%%EOF"


async def main() -> None:
    print("Connecting to database...")
    async with AsyncSessionLocal() as session:
        # 1. Verify branches exist
        branch_res = await session.execute(select(Branch).where(Branch.is_active.is_(True)))
        branches = branch_res.scalars().all()
        if not branches:
            print("ERROR: No active branches found. Run seed_data.py first.")
            return

        print(f"Found {len(branches)} active branches.")

        # 2. Seed Lab Tests
        seeded_count = 0
        skipped_count = 0
        for data in LAB_TESTS:
            existing = await session.execute(
                select(LabTest).where(LabTest.code == data["code"], LabTest.deleted_at.is_(None))
            )
            if existing.scalar_one_or_none():
                print(f"  SKIP  {data['name']} ({data['code']} already exists)")
                skipped_count += 1
                continue

            test = LabTest(
                name=data["name"],
                code=data["code"],
                description=data["description"],
                price=data["price"],
                branch_id=None,  # Universal test offered at all branches
                home_sampling_available=data["home_sampling_available"],
                home_sampling_fee=data["home_sampling_fee"],
                turnaround_hours=data["turnaround_hours"],
                is_active=True,
            )
            session.add(test)
            seeded_count += 1
            print(f"  SEED  {data['name']} ({data['code']})")

        await session.commit()
        print(f"Tests seeded: {seeded_count}, skipped: {skipped_count}.")

        # 3. Seed 2 Sample Reports for Test Patient
        patient_res = await session.execute(
            select(User).where(User.phone == "+923001234567")
        )
        patient = patient_res.scalar_one_or_none()
        if not patient:
            print("Notice: Test patient (+923001234567) not found. Skipping sample reports.")
            return

        # Fetch tests for linkage
        cbc_test_res = await session.execute(select(LabTest).where(LabTest.code == "LAB-CBC"))
        cbc_test = cbc_test_res.scalar_one_or_none()

        lipid_test_res = await session.execute(select(LabTest).where(LabTest.code == "LAB-LIPID"))
        lipid_test = lipid_test_res.scalar_one_or_none()

        # Check existing reports
        rep_res = await session.execute(
            select(LabReport).where(LabReport.patient_id == patient.id)
        )
        existing_reports = rep_res.scalars().all()
        if existing_reports:
            print(f"Test patient already has {len(existing_reports)} lab report(s). Skipping report seed.")
            return

        # Upload sample PDF files to storage backend
        storage = get_storage()
        cbc_path = f"reports/{patient.id}/cbc_sample_report.pdf"
        lipid_path = f"reports/{patient.id}/lipid_sample_report.pdf"

        # Mock direct write to S3 or local
        try:
            from app.config import settings
            if settings.STORAGE_BACKEND == "s3":
                import boto3
                from botocore.config import Config
                s3 = boto3.client(
                    "s3",
                    endpoint_url=settings.S3_ENDPOINT_URL,
                    aws_access_key_id=settings.S3_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
                    config=Config(signature_version="s3v4"),
                )
                s3.put_object(Bucket=settings.S3_BUCKET_NAME, Key=cbc_path, Body=SAMPLE_PDF_BYTES, ContentType="application/pdf")
                s3.put_object(Bucket=settings.S3_BUCKET_NAME, Key=lipid_path, Body=SAMPLE_PDF_BYTES, ContentType="application/pdf")
                print("Uploaded sample PDFs to Cloudflare R2 / S3.")
            else:
                from pathlib import Path
                base_dir = Path(settings.LOCAL_UPLOAD_DIR)
                dest1 = base_dir / cbc_path
                dest2 = base_dir / lipid_path
                dest1.parent.mkdir(parents=True, exist_ok=True)
                dest1.write_bytes(SAMPLE_PDF_BYTES)
                dest2.write_bytes(SAMPLE_PDF_BYTES)
                print("Wrote sample PDFs to local upload directory.")
        except Exception as e:
            print(f"Warning: Could not upload sample PDFs to storage: {e}")

        # Insert DB records
        rep1 = LabReport(
            patient_id=patient.id,
            test_id=cbc_test.id if cbc_test else None,
            file_path=cbc_path,
            file_type=ReportFileType.pdf,
            file_name="CBC_Diagnostic_Report.pdf",
            is_visible=True,
            notes="All 14 parameters within normal reference ranges. Hemoglobin: 14.1 g/dL, Platelets: 245,000 /mcL.",
        )
        rep2 = LabReport(
            patient_id=patient.id,
            test_id=lipid_test.id if lipid_test else None,
            file_path=lipid_path,
            file_type=ReportFileType.pdf,
            file_name="Lipid_Profile_Report.pdf",
            is_visible=True,
            notes="Total Cholesterol: 195 mg/dL (Borderline), HDL: 48 mg/dL. Follow-up recommended in 6 months.",
        )

        session.add_all([rep1, rep2])
        await session.commit()
        print("Seeded 2 sample patient lab reports for test patient account.")


if __name__ == "__main__":
    asyncio.run(main())
