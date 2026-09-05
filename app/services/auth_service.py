"""
Auth service — all business logic for authentication.

Handles:
  - Patient registration
  - Login (access + refresh token pair)
  - Token refresh
  - Logout (revoke refresh token)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.utils.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.config import settings


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Register ───────────────────────────────────────────────────────────────
    async def register_patient(
        self,
        full_name: str,
        phone: str,
        password: str,
        email: str | None = None,
    ) -> User:
        # Check phone uniqueness
        existing = await self.db.execute(
            select(User).where(User.phone == phone, User.deleted_at.is_(None))
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this phone number already exists.",
            )

        # Check email uniqueness (if provided)
        if email:
            existing_email = await self.db.execute(
                select(User).where(User.email == email, User.deleted_at.is_(None))
            )
            if existing_email.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A user with this email already exists.",
                )

        user = User(
            full_name=full_name,
            phone=phone,
            email=email,
            hashed_password=hash_password(password),
            role=UserRole.patient,
        )
        self.db.add(user)
        await self.db.flush()
        return user

    # ── Login ──────────────────────────────────────────────────────────────────
    async def login(self, phone: str, password: str) -> dict:
        """
        Authenticate by phone + password.
        Returns {access_token, refresh_token, token_type}.
        """
        result = await self.db.execute(
            select(User).where(User.phone == phone, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()

        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect phone number or password.",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is deactivated. Please contact support.",
            )

        return await self._issue_token_pair(user)

    # ── Refresh ────────────────────────────────────────────────────────────────
    async def refresh_tokens(self, raw_refresh_token: str) -> dict:
        """Exchange a valid refresh token for a new token pair."""
        token_hash = hash_refresh_token(raw_refresh_token)

        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        stored = result.scalar_one_or_none()

        if not stored or not stored.is_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token.",
            )

        # Rotate: revoke old token, issue new pair
        stored.revoked_at = datetime.now(timezone.utc)
        await self.db.flush()

        user_result = await self.db.execute(
            select(User).where(User.id == stored.user_id, User.deleted_at.is_(None))
        )
        user = user_result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

        return await self._issue_token_pair(user)

    # ── Logout ─────────────────────────────────────────────────────────────────
    async def logout(self, raw_refresh_token: str) -> None:
        """Revoke a refresh token (real server-side logout)."""
        token_hash = hash_refresh_token(raw_refresh_token)
        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        stored = result.scalar_one_or_none()
        if stored and not stored.is_revoked:
            stored.revoked_at = datetime.now(timezone.utc)

    # ── Admin: create staff / doctor user ─────────────────────────────────────
    async def create_staff_user(
        self,
        full_name: str,
        phone: str,
        password: str,
        role: UserRole,
        email: str | None = None,
    ) -> User:
        if role == UserRole.patient:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use /register to create patient accounts.",
            )
        return await self.register_patient(full_name, phone, password, email)

    # ── Internal ───────────────────────────────────────────────────────────────
    async def _issue_token_pair(self, user: User) -> dict:
        access_token, _ = create_access_token(str(user.id), user.role.value)
        raw_refresh, token_hash = generate_refresh_token()
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
        refresh_record = RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self.db.add(refresh_record)
        await self.db.flush()
        return {
            "access_token": access_token,
            "refresh_token": raw_refresh,
            "token_type": "bearer",
        }
