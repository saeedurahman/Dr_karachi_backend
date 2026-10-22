"""
Security utilities:
  - Password hashing (bcrypt via passlib)
  - JWT access token creation / decoding
  - Refresh token generation and hashing (SHA-256)
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# ── Bcrypt context ─────────────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── JWT ────────────────────────────────────────────────────────────────────────
def create_access_token(subject: str, role: str) -> tuple[str, datetime]:
    """
    Create a short-lived JWT access token.
    Returns (token_str, expires_at).
    """
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": subject,       # user UUID as string
        "role": role,
        "type": "access",
        "exp": expires_at,
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, expires_at


def decode_access_token(token: str) -> dict:
    """
    Decode and validate a JWT access token.
    Raises JWTError on invalid / expired tokens.
    """
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    if payload.get("type") != "access":
        raise JWTError("Not an access token")
    return payload


# ── Refresh token ──────────────────────────────────────────────────────────────
def generate_refresh_token() -> tuple[str, str]:
    """
    Generate a cryptographically secure refresh token.
    Returns (raw_token, sha256_hex_hash).
    Raw token is sent to the client; only the hash is stored in the DB.
    """
    raw = secrets.token_urlsafe(64)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Password reset OTP & Token ────────────────────────────────────────────────
import hmac


def generate_otp_code() -> str:
    """Generate a cryptographically secure 6-digit OTP code using secrets."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(otp: str) -> str:
    """Compute HMAC-SHA256 hex digest of the OTP code using SECRET_KEY."""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        otp.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_otp(plain_otp: str, hashed_otp: str) -> bool:
    """Constant-time verification of OTP against stored HMAC-SHA256 hash."""
    computed = hash_otp(plain_otp)
    return secrets.compare_digest(computed, hashed_otp)


def create_password_reset_token(user_id: str, otp_id: str) -> tuple[str, int]:
    """
    Create a short-lived (10 min) single-use JWT reset token.
    Returns (token_str, expires_in_seconds).
    """
    expires_in = 600  # 10 minutes
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    payload = {
        "sub": user_id,
        "otp_id": otp_id,
        "purpose": "pwd_reset",
        "jti": secrets.token_hex(16),
        "exp": expires_at,
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, expires_in


def decode_password_reset_token(token: str) -> dict:
    """
    Decode and validate a password reset JWT token.
    Enforces purpose == 'pwd_reset'.
    Raises JWTError on invalid / expired tokens or mismatched purpose.
    """
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    if payload.get("purpose") != "pwd_reset":
        raise JWTError("Invalid token purpose: expected 'pwd_reset'")
    return payload
