"""
Auth service — all business logic for authentication.

Handles:
  - Patient registration
  - Login (access + refresh token pair)
  - Token refresh
  - Logout (revoke refresh token)
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from jose import JWTError
from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.appointment import Appointment
from app.models.lab_booking import LabBooking
from app.models.order import Order
from app.models.password_reset_otp import PasswordResetOtp
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.services.whatsapp.sender import SafeSender
from app.utils.phone import normalize_phone_number
from app.utils.security import (
    create_access_token,
    create_password_reset_token,
    decode_password_reset_token,
    generate_otp_code,
    generate_refresh_token,
    hash_otp,
    hash_password,
    hash_refresh_token,
    verify_otp,
    verify_password,
)


def _mask_phone(phone: str) -> str:
    if len(phone) >= 7:
        return f"{phone[:5]}***{phone[-2:]}"
    return "***"


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
        stored.revoked_at = datetime.now(UTC)
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
            stored.revoked_at = datetime.now(UTC)

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
        expires_at = datetime.now(UTC) + timedelta(
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

    # ── Forgot / Reset Password ────────────────────────────────────────────────
    async def request_password_reset(
        self,
        raw_phone: str,
        ip: str | None = None,
        sender: SafeSender | None = None,
    ) -> None:
        """Process forgot password request.

        - Normalizes the phone number.
        - Applies 60s cooldown and max 3 requests/hour per phone regardless of account existence.
        - Always succeeds with the same generic response timing to avoid user enumeration.
        - If active user exists: invalidates prior unused OTPs, creates new OTP, sends WhatsApp template.
        - If send fails (e.g. Meta payment issue 131042): logs safely without OTP and continues.
        """
        try:
            normalized_phone = normalize_phone_number(raw_phone)
        except Exception:
            normalized_phone = re.sub(r"[^\d]", "", str(raw_phone or ""))

        now = datetime.now(UTC)
        one_hour_ago = now - timedelta(hours=1)

        # 1. Phone-based Rate Limiting (independent of account existence)
        stmt = (
            select(PasswordResetOtp)
            .where(
                PasswordResetOtp.phone == normalized_phone,
                PasswordResetOtp.created_at >= one_hour_ago,
            )
            .order_by(PasswordResetOtp.created_at.desc())
        )
        recent_otps = (await self.db.execute(stmt)).scalars().all()

        if recent_otps:
            latest = recent_otps[0]
            time_since_latest = (now - latest.created_at).total_seconds()
            if time_since_latest < 60:
                retry_after = max(1, int(60 - time_since_latest))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Please wait {retry_after} seconds before requesting another code.",
                    headers={"Retry-After": str(retry_after)},
                )

            if len(recent_otps) >= 3:
                oldest_in_window = recent_otps[2]
                time_until_slot = max(
                    1, int(3600 - (now - oldest_in_window.created_at).total_seconds())
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many password reset requests. Please try again later.",
                    headers={"Retry-After": str(time_until_slot)},
                )

        # 2. Check for active user matching phone variants
        phone_variants = [normalized_phone, f"+{normalized_phone}"]
        if normalized_phone.startswith("92") and len(normalized_phone) == 12:
            phone_variants.append(f"0{normalized_phone[2:]}")

        user_stmt = select(User).where(
            User.phone.in_(phone_variants),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
        user = (await self.db.execute(user_stmt)).scalars().first()

        if user:
            # Invalidate previous unused OTPs for this user
            await self.db.execute(
                update(PasswordResetOtp)
                .where(
                    PasswordResetOtp.user_id == user.id,
                    PasswordResetOtp.used_at.is_(None),
                )
                .values(used_at=now)
            )

            # Generate and hash 6-digit OTP
            code = generate_otp_code()
            otp_hash = hash_otp(code)
            expires_at = now + timedelta(minutes=10)

            otp_record = PasswordResetOtp(
                user_id=user.id,
                phone=normalized_phone,
                otp_hash=otp_hash,
                expires_at=expires_at,
                attempts=0,
                ip=ip,
                created_at=now,
            )
            self.db.add(otp_record)
            await self.db.flush()

            # Dispatch WhatsApp message with template password_reset_otp
            try:
                s = sender or SafeSender()
                await s.send_template(
                    db=self.db,
                    to=normalized_phone,
                    template_key="password_reset_otp",
                    params={"code": code},
                )
            except Exception as exc:
                # Log safely without OTP
                logger.error(
                    "WhatsApp password reset OTP send failed | phone={} err={}",
                    _mask_phone(normalized_phone),
                    exc,
                )
        else:
            # User does NOT exist.
            # Insert dummy entry to enforce phone rate limiting and simulate hashing time
            dummy_code = generate_otp_code()
            dummy_hash = hash_otp(dummy_code)
            dummy_record = PasswordResetOtp(
                user_id=None,
                phone=normalized_phone,
                otp_hash=dummy_hash,
                expires_at=now,
                attempts=5,
                used_at=now,
                ip=ip,
                created_at=now,
            )
            self.db.add(dummy_record)
            await self.db.flush()
            _ = hash_password(dummy_code)

    async def verify_password_reset_otp(self, raw_phone: str, otp: str) -> dict:
        """Verify 6-digit OTP for phone.

        Returns {reset_token, expires_in} on success.
        Raises 400 on invalid / expired code or exhausted attempts.
        """
        try:
            normalized_phone = normalize_phone_number(raw_phone)
        except Exception:
            normalized_phone = re.sub(r"[^\d]", "", str(raw_phone or ""))

        now = datetime.now(UTC)
        stmt = (
            select(PasswordResetOtp)
            .where(
                PasswordResetOtp.phone == normalized_phone,
                PasswordResetOtp.user_id.is_not(None),
                PasswordResetOtp.used_at.is_(None),
            )
            .order_by(PasswordResetOtp.created_at.desc())
        )
        otp_record = (await self.db.execute(stmt)).scalars().first()

        if not otp_record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired code",
            )

        if otp_record.expires_at <= now or otp_record.attempts >= 5:
            otp_record.used_at = now
            await self.db.flush()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired code",
            )

        is_valid = verify_otp(otp, otp_record.otp_hash)
        if not is_valid:
            otp_record.attempts += 1
            if otp_record.attempts >= 5:
                otp_record.used_at = now
            await self.db.flush()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired code",
            )

        # Success: generate single-use token and store its hash
        reset_token, expires_in = create_password_reset_token(
            user_id=str(otp_record.user_id),
            otp_id=str(otp_record.id),
        )
        otp_record.reset_token_hash = hash_refresh_token(reset_token)
        await self.db.flush()
        return {"reset_token": reset_token, "expires_in": expires_in}

    async def reset_password_with_token(self, reset_token: str, new_password: str) -> None:
        """Reset password using single-use reset token and revoke all user sessions."""
        try:
            payload = decode_password_reset_token(reset_token)
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token.",
            )

        user_id_str = payload.get("sub")
        otp_id_str = payload.get("otp_id")
        if not user_id_str or not otp_id_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token.",
            )

        try:
            user_uuid = uuid.UUID(user_id_str)
            otp_uuid = uuid.UUID(otp_id_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token.",
            )

        otp_stmt = select(PasswordResetOtp).where(PasswordResetOtp.id == otp_uuid)
        otp_record = (await self.db.execute(otp_stmt)).scalar_one_or_none()

        if not otp_record or otp_record.used_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token.",
            )

        if not otp_record.reset_token_hash or not secrets.compare_digest(
            otp_record.reset_token_hash, hash_refresh_token(reset_token)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token.",
            )

        user_stmt = select(User).where(User.id == user_uuid, User.deleted_at.is_(None))
        user = (await self.db.execute(user_stmt)).scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User account is inactive or not found.",
            )

        now = datetime.now(UTC)
        otp_record.used_at = now
        user.hashed_password = hash_password(new_password)

        # Revoke all refresh tokens / sessions for this user
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await self.db.flush()

    async def admin_reset_password(
        self,
        target_user_id: uuid.UUID,
        new_password: str,
        current_user: User,
    ) -> User:
        """[Admin] Reset password for any user account (patient, staff, doctor) and revoke sessions.

        Rules:
        - super_admin can reset anyone.
        - branch_manager cannot reset super_admin or any branch_manager.
        - branch_manager can reset pharmacy_staff, lab_staff, doctor only if assigned to their branch.
        - branch_manager can reset a patient only if the patient has their branch assigned OR has
          activity (appointment, lab booking, or order) at that branch.
        - Audits every reset action (actor id, target id, timestamp, is_self_reset) without logging passwords.
        """
        user_result = await self.db.execute(
            select(User).where(User.id == target_user_id, User.deleted_at.is_(None))
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

        # Branch scoping for branch_manager
        if current_user.role == UserRole.branch_manager:
            if not current_user.branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Branch manager has no branch assigned.",
                )

            # 1. Never allow resetting super_admin
            if user.role == UserRole.super_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Branch managers cannot reset super admin accounts.",
                )

            # 2. Never allow resetting any branch_manager
            if user.role == UserRole.branch_manager:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Branch managers cannot reset branch manager accounts.",
                )

            # 3. Patient scoping
            if user.role == UserRole.patient:
                patient_matches_branch = (user.branch_id == current_user.branch_id)
                if not patient_matches_branch:
                    # Check for activity (appointment, lab booking, or order) at this branch
                    has_appt = (
                        await self.db.execute(
                            select(func.count(Appointment.id)).where(
                                Appointment.patient_id == user.id,
                                Appointment.branch_id == current_user.branch_id,
                            )
                        )
                    ).scalar() or 0

                    has_booking = (
                        await self.db.execute(
                            select(func.count(LabBooking.id)).where(
                                LabBooking.patient_id == user.id,
                                LabBooking.branch_id == current_user.branch_id,
                            )
                        )
                    ).scalar() or 0

                    has_order = (
                        await self.db.execute(
                            select(func.count(Order.id)).where(
                                Order.patient_id == user.id,
                                Order.branch_id == current_user.branch_id,
                            )
                        )
                    ).scalar() or 0

                    if has_appt == 0 and has_booking == 0 and has_order == 0:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Branch managers can only reset patients with activity (appointments, bookings, or orders) at their branch.",
                        )
            else:
                # 4. Staff / Doctor scoping: must belong to the manager's branch
                if user.branch_id != current_user.branch_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Branch managers can only reset staff in their own branch.",
                    )

        now = datetime.now(UTC)
        user.hashed_password = hash_password(new_password)

        # Revoke all sessions / refresh tokens
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await self.db.flush()

        # Audit log entry (actor id, target id, roles, is_self_reset, timestamp — never logging password)
        is_self_reset = (current_user.id == user.id)
        logger.info(
            "ADMIN_PASSWORD_RESET_AUDIT | actor_id={} target_id={} actor_role={} target_role={} is_self_reset={} timestamp={}",
            current_user.id,
            user.id,
            current_user.role.value,
            user.role.value,
            is_self_reset,
            now.isoformat(),
        )

        return user

