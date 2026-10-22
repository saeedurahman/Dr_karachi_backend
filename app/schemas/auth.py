"""Pydantic schemas for auth endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.user import UserRole


# ── Request schemas ────────────────────────────────────────────────────────────
class PatientRegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., pattern=r"^\+?[0-9]{10,15}$")
    password: str = Field(..., min_length=8, max_length=128)
    email: EmailStr | None = None
    marketing_opt_in: bool = False

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter.")
        return v


class LoginRequest(BaseModel):
    phone: str
    password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class CreateStaffUserRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., pattern=r"^\+?[0-9]{10,15}$")
    password: str = Field(..., min_length=8, max_length=128)
    email: EmailStr | None = None
    role: UserRole = UserRole.pharmacy_staff
    branch_id: uuid.UUID | None = None

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter.")
        return v

    @field_validator("role")
    @classmethod
    def role_not_patient(cls, v: UserRole) -> UserRole:
        if v == UserRole.patient:
            raise ValueError("Use /register for patient accounts.")
        return v



class UpdateProfileRequest(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=255)
    email: EmailStr | None = None


class UpdateStaffUserRequest(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=255)
    email: EmailStr | None = None
    role: UserRole | None = None
    branch_id: uuid.UUID | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def role_not_patient(cls, v: UserRole | None) -> UserRole | None:
        if v == UserRole.patient:
            raise ValueError("Role cannot be patient.")
        return v


class ResetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter.")
        return v


class ForgotPasswordRequest(BaseModel):
    phone: str = Field(..., description="User's phone number")


class ForgotPasswordResponse(BaseModel):
    message: str = "If this number is registered, a code has been sent on WhatsApp"


class VerifyOtpRequest(BaseModel):
    phone: str = Field(..., description="User's phone number")
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$", description="6-digit OTP code")


class VerifyOtpResponse(BaseModel):
    reset_token: str
    expires_in: int = 600


class ResetPasswordConfirmRequest(BaseModel):
    reset_token: str = Field(..., description="Short-lived, single-use password reset JWT")
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter.")
        return v


class ResetPasswordResponse(BaseModel):
    message: str = "Password has been successfully reset. Please log in with your new password."



# ── Response schemas ───────────────────────────────────────────────────────────
class UserResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str
    email: str | None
    role: UserRole
    branch_id: uuid.UUID | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse
