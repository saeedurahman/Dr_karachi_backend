"""
scripts/import_lab_tests.py — Idempotent importer for laboratory tests price list.

Usage:
    python scripts/import_lab_tests.py [--file path/to/Test_Charges.xlsx]

Features:
  - Strips hidden zero-width character (\\u200b) and trims whitespace on all fields.
  - Uses "Test name" for the test name/display name.
  - Maps category, specimen ("None" -> null), reporting_time, turnaround_hours, and price.
  - Zero-price rows imported as inactive (is_active=False).
  - Upserts by test code (idempotent, re-runnable).
  - Retires old fake seeded tests: deactivates if referenced by lab_bookings/lab_reports,
    otherwise deletes them.
  - Prints summary: inserted, updated, deactivated, skipped, zero-price names.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

# Add backend directory to path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import openpyxl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.lab_booking import LabBooking
from app.models.lab_report import LabReport
from app.models.lab_test import LabTest


def find_excel_file(provided_path: str | None = None) -> Path:
    if provided_path and os.path.exists(provided_path):
        return Path(provided_path)

    candidates = [
        BACKEND_DIR / "data" / "Test_Charges.xlsx",
        BACKEND_DIR / "data" / "Test Charges46662.xlsx",
        BACKEND_DIR.parent / "documents" / "Test_Charges.xlsx",
    ]
    for c in candidates:
        if c.exists():
            return c

    raise FileNotFoundError(
        f"Test charges Excel file not found. Checked: {[str(c) for c in candidates]}"
    )


async def import_lab_tests(file_path: Path) -> dict:
    print(f"Loading lab tests from: {file_path}")
    wb = openpyxl.load_workbook(str(file_path), data_only=True)
    ws = wb["sheet1"] if "sheet1" in wb.sheetnames else wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("Excel file is empty.")

    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    data_rows = rows[1:]

    # Track metrics
    inserted = 0
    updated = 0
    deactivated = 0
    deleted = 0
    skipped = 0
    zero_price_names: list[str] = []

    imported_codes: set[str] = set()

    async with AsyncSessionLocal() as session:
        # Load all existing tests from DB
        stmt = select(LabTest)
        res = await session.execute(stmt)
        existing_tests = {t.code: t for t in res.scalars().all()}

        for row_idx, raw_vals in enumerate(data_rows, start=2):
            row = dict(zip(headers, raw_vals))

            raw_code = row.get("Test code")
            if not raw_code:
                skipped += 1
                continue

            # Strip zero-width space and trim
            code = str(raw_code).replace("\u200b", "").strip()
            if not code:
                skipped += 1
                continue

            imported_codes.add(code)

            # Test name (use 'Test name', NOT 'Test display name')
            name = str(row.get("Test name") or "").strip()
            if not name:
                name = code

            # Category
            raw_cat = row.get("Test category")
            category = str(raw_cat).strip() if raw_cat else None

            # Specimen ('None' -> null)
            raw_spec = row.get("Test specimen")
            specimen = str(raw_spec).strip() if raw_spec else None
            if specimen and specimen.lower() in ("none", "null", "n/a", "-"):
                specimen = None

            # Reporting time & Turnaround
            raw_rep = row.get("Reporting time")
            reporting_time = str(raw_rep).strip() if raw_rep else None

            try:
                eta_d = int(row.get("ETA day") or 0)
            except (ValueError, TypeError):
                eta_d = 0
            try:
                eta_h = int(row.get("ETA hours") or 0)
            except (ValueError, TypeError):
                eta_h = 0
            try:
                eta_m = int(row.get("ETA minutes") or 0)
            except (ValueError, TypeError):
                eta_m = 0

            th = (eta_d * 24) + eta_h
            if eta_m > 0 and th == 0:
                th = 1
            turnaround_hours = th if th > 0 else None

            # Price
            try:
                raw_price = row.get("Test charges")
                price = Decimal(str(raw_price or 0).strip())
            except Exception:
                price = Decimal("0.00")

            is_active = price > 0
            if price == 0:
                zero_price_names.append(name)

            if code in existing_tests:
                test = existing_tests[code]
                test.name = name
                test.category = category
                test.specimen = specimen
                test.reporting_time = reporting_time
                test.turnaround_hours = turnaround_hours
                test.price = price
                test.is_active = is_active
                test.deleted_at = None
                updated += 1
            else:
                test = LabTest(
                    id=uuid.uuid4(),
                    code=code,
                    name=name,
                    category=category,
                    specimen=specimen,
                    reporting_time=reporting_time,
                    turnaround_hours=turnaround_hours,
                    price=price,
                    is_active=is_active,
                    home_sampling_available=False,
                    home_sampling_fee=Decimal("0.00"),
                    branch_id=None,
                )
                session.add(test)
                existing_tests[code] = test
                inserted += 1

        # Handle old fake seeded tests not present in real list
        for code, test in list(existing_tests.items()):
            if code not in imported_codes:
                # Check if referenced by lab bookings
                booking_count = (
                    await session.scalar(
                        select(func.count(LabBooking.id)).where(LabBooking.test_id == test.id)
                    )
                    or 0
                )
                report_count = (
                    await session.scalar(
                        select(func.count(LabReport.id)).where(LabReport.test_id == test.id)
                    )
                    or 0
                )

                if booking_count > 0 or report_count > 0:
                    test.is_active = False
                    deactivated += 1
                else:
                    await session.delete(test)
                    deleted += 1

        await session.commit()

    summary = {
        "inserted": inserted,
        "updated": updated,
        "deactivated": deactivated,
        "deleted": deleted,
        "skipped": skipped,
        "zero_price_names": zero_price_names,
    }

    print("\n" + "=" * 50)
    print("LAB TESTS IMPORT SUMMARY")
    print("=" * 50)
    print(f"  Inserted:        {inserted}")
    print(f"  Updated:         {updated}")
    print(f"  Deactivated:     {deactivated} (referenced fake tests)")
    print(f"  Deleted:         {deleted} (unreferenced fake tests)")
    print(f"  Skipped:         {skipped}")
    print(f"  Zero-price (2):  {len(zero_price_names)}")
    for z in zero_price_names:
        print(f"    - {z} (marked inactive)")
    print("=" * 50 + "\n")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Import lab test price list from Excel.")
    parser.add_argument("--file", help="Path to Test_Charges.xlsx", default=None)
    args = parser.parse_args()

    excel_path = find_excel_file(args.file)
    asyncio.run(import_lab_tests(excel_path))


if __name__ == "__main__":
    main()
