"""
seed_doctors.py — Seed 4 sample doctors with branch assignments and branch-scoped availability.

Usage (from backend/ directory):
    python seed_doctors.py

Requirements:
  - DATABASE_URL env var set (or .env file loaded by app.config)
  - Branches must already exist in the database (from previous seeding)

Idempotent: re-running skips doctors whose phone numbers already exist.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Bootstrap app config for DATABASE_URL
# ---------------------------------------------------------------------------
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.config import settings  # noqa: E402
from app.database_base import Base  # noqa: E402 — ensures models are registered
from app.models.branch import Branch  # noqa: E402
from app.models.doctor import DayOfWeek, Doctor, DoctorAvailability, DoctorBranch  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.utils.security import hash_password  # noqa: E402

# ---------------------------------------------------------------------------
# Sample doctor profiles
# ---------------------------------------------------------------------------
DOCTORS = [
    {
        "full_name": "Dr. Ahmed Siddiqui",
        "phone": "+923001110001",
        "specialization": "Cardiologist",
        "qualification": "MBBS, FCPS (Cardiology)",
        "experience_years": 12,
        "consultation_fee": 1500,
        "bio": "Dr. Ahmed Siddiqui is a senior cardiologist with over 12 years of experience in interventional cardiology and heart failure management.",
        # branch indices into the fetched branches list (0-indexed)
        "branch_indices": [0, 1],
        # availability: list of (day_of_week, start_time, end_time) per branch
        "availability": [
            (DayOfWeek.monday,    time(9, 0),  time(17, 0)),
            (DayOfWeek.tuesday,   time(9, 0),  time(17, 0)),
            (DayOfWeek.wednesday, time(9, 0),  time(17, 0)),
            (DayOfWeek.thursday,  time(9, 0),  time(17, 0)),
            (DayOfWeek.friday,    time(9, 0),  time(17, 0)),
        ],
    },
    {
        "full_name": "Dr. Sara Malik",
        "phone": "+923001110002",
        "specialization": "General Physician",
        "qualification": "MBBS, MCPS (Family Medicine)",
        "experience_years": 8,
        "consultation_fee": 800,
        "bio": "Dr. Sara Malik specialises in primary care, preventive medicine, and chronic disease management. She is known for her thorough patient consultations.",
        "branch_indices": [0, 2],
        "availability": [
            (DayOfWeek.monday,    time(10, 0), time(16, 0)),
            (DayOfWeek.tuesday,   time(10, 0), time(16, 0)),
            (DayOfWeek.wednesday, time(10, 0), time(16, 0)),
            (DayOfWeek.thursday,  time(10, 0), time(16, 0)),
            (DayOfWeek.friday,    time(10, 0), time(16, 0)),
            (DayOfWeek.saturday,  time(10, 0), time(14, 0)),
        ],
    },
    {
        "full_name": "Dr. Imran Chaudhry",
        "phone": "+923001110003",
        "specialization": "Dermatologist",
        "qualification": "MBBS, DDV (Dermatology)",
        "experience_years": 6,
        "consultation_fee": 1200,
        "bio": "Dr. Imran Chaudhry is a dermatologist specialising in acne, eczema, psoriasis, and cosmetic skin treatments.",
        "branch_indices": [1],
        "availability": [
            (DayOfWeek.tuesday,   time(11, 0), time(15, 0)),
            (DayOfWeek.thursday,  time(11, 0), time(15, 0)),
            (DayOfWeek.saturday,  time(11, 0), time(15, 0)),
        ],
    },
    {
        "full_name": "Dr. Nadia Hussain",
        "phone": "+923001110004",
        "specialization": "Pediatrician",
        "qualification": "MBBS, FCPS (Pediatrics)",
        "experience_years": 10,
        "consultation_fee": 1000,
        "bio": "Dr. Nadia Hussain is a dedicated pediatrician with a decade of experience caring for children from newborns to teenagers.",
        "branch_indices": [0, 3],
        "availability": [
            (DayOfWeek.monday,    time(9, 0),  time(13, 0)),
            (DayOfWeek.tuesday,   time(9, 0),  time(13, 0)),
            (DayOfWeek.wednesday, time(9, 0),  time(13, 0)),
            (DayOfWeek.thursday,  time(9, 0),  time(13, 0)),
            (DayOfWeek.friday,    time(9, 0),  time(13, 0)),
        ],
    },
]

PASSWORD = "DoctorPass123!"  # shared seed password — change before production


async def main() -> None:
    print("Connecting to database…")
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session: sessionmaker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as db:
        # Fetch existing branches (ordered by creation date for stable indexing)
        branch_result = await db.execute(
            select(Branch).where(Branch.is_active == True).order_by(Branch.created_at)
        )
        branches: list[Branch] = list(branch_result.scalars().all())

        if not branches:
            print("ERROR: No active branches found. Please seed branches first.")
            return

        print(f"Found {len(branches)} branch(es): {[b.name for b in branches]}")

        seeded = 0
        skipped = 0

        for doc_data in DOCTORS:
            phone = doc_data["phone"]

            # Idempotency: skip if user with this phone already exists
            existing_user_result = await db.execute(
                select(User).where(User.phone == phone)
            )
            if existing_user_result.scalar_one_or_none():
                print(f"  SKIP  {doc_data['full_name']} (phone {phone} already exists)")
                skipped += 1
                continue

            # 1. Create the User account with role=doctor
            user = User(
                id=uuid.uuid4(),
                full_name=doc_data["full_name"],
                phone=phone,
                hashed_password=hash_password(PASSWORD),
                role=UserRole.doctor,
                is_active=True,
            )
            db.add(user)
            await db.flush()  # get user.id

            # 2. Create the Doctor profile
            doctor = Doctor(
                id=uuid.uuid4(),
                user_id=user.id,
                specialization=doc_data["specialization"],
                qualification=doc_data["qualification"],
                experience_years=doc_data["experience_years"],
                consultation_fee=doc_data["consultation_fee"],
                bio=doc_data["bio"],
                is_active=True,
            )
            db.add(doctor)
            await db.flush()  # get doctor.id

            # 3. Assign to branches
            assigned_branches: list[Branch] = []
            for idx in doc_data["branch_indices"]:
                if idx >= len(branches):
                    print(f"  WARN  Branch index {idx} out of range, skipping")
                    continue
                branch = branches[idx]
                is_primary = (idx == doc_data["branch_indices"][0])
                db.add(DoctorBranch(
                    id=uuid.uuid4(),
                    doctor_id=doctor.id,
                    branch_id=branch.id,
                    is_primary=is_primary,
                ))
                assigned_branches.append(branch)

            await db.flush()

            # 4. Create branch-scoped availability rows
            for branch in assigned_branches:
                for day, start, end in doc_data["availability"]:
                    db.add(DoctorAvailability(
                        id=uuid.uuid4(),
                        doctor_id=doctor.id,
                        branch_id=branch.id,
                        day_of_week=day,
                        start_time=start,
                        end_time=end,
                        is_active=True,
                    ))

            await db.commit()
            branch_names = [b.name for b in assigned_branches]
            print(f"  OK    {doc_data['full_name']} ({doc_data['specialization']}) → {branch_names}")
            seeded += 1

    await engine.dispose()
    print(f"\nDone. Seeded {seeded} doctor(s), skipped {skipped}.")


if __name__ == "__main__":
    asyncio.run(main())
