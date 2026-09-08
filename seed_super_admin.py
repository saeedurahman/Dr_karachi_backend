"""
seed_super_admin.py -- Seed a single super_admin account for manual QA testing.

Usage (from backend/ directory):
    python seed_super_admin.py

Requirements:
  - DATABASE_URL env var set (or .env file loaded by app.config)

Idempotent: skips insertion if +923009999999 already exists.
NOTE: Marked as throwaway test fixture in pre-delivery cleanup register.
      Delete this account before final client delivery.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User, UserRole
from app.utils.security import hash_password

# ---------------------------------------------------------------------------
# Credentials -- change / remove before production handoff
# ---------------------------------------------------------------------------
PHONE = "+923009999999"
PASSWORD = "SuperAdmin@2026"
FULL_NAME = "Test Super Admin"


async def main() -> None:
    print("Connecting to database...")

    async with AsyncSessionLocal() as session:
        # Idempotency: skip if phone already registered
        result = await session.execute(select(User).where(User.phone == PHONE))
        existing = result.scalar_one_or_none()

        if existing:
            print(
                f"  SKIP  {FULL_NAME} (phone {PHONE} already exists, "
                f"role={existing.role}, id={existing.id})"
            )
            return

        user = User(
            id=uuid.uuid4(),
            full_name=FULL_NAME,
            phone=PHONE,
            hashed_password=hash_password(PASSWORD),
            role=UserRole.super_admin,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        print(f"  OK    Created super_admin account")
        print(f"        id        : {user.id}")
        print(f"        full_name : {user.full_name}")
        print(f"        phone     : {user.phone}")
        print(f"        role      : {user.role}")
        print(f"        is_active : {user.is_active}")
        print(f"        created_at: {user.created_at}")

    print("\nDone. Test super_admin seeded successfully.")
    print("REMINDER: Remove this account before final client delivery.")


if __name__ == "__main__":
    asyncio.run(main())
