"""Secure, tamper-proof, time-limited report access link helpers."""
from __future__ import annotations

import base64
import enum
import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta

from app.config import settings


class TokenStatus(str, enum.Enum):
    VALID = "valid"
    EXPIRED = "expired"
    INVALID = "invalid"


def generate_report_access_token(
    report_id: uuid.UUID | str,
    patient_id: uuid.UUID | str,
    ttl_hours: int | None = None,
    secret: str | None = None,
) -> str:
    """Generate a signed, url-safe token for public report access.

    Format: base64url(report_id:patient_id:expires_at).base64url(hmac_sha256)
    """
    secret_key = secret or settings.REPORT_LINK_SECRET
    hours = ttl_hours if ttl_hours is not None else settings.REPORT_LINK_TTL_HOURS
    expires_at = int((datetime.now(UTC) + timedelta(hours=hours)).timestamp())

    payload = f"{str(report_id)}:{str(patient_id)}:{expires_at}"
    payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8").rstrip("=")

    sig = hmac.new(
        key=secret_key.encode("utf-8"),
        msg=payload_b64.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")

    return f"{payload_b64}.{sig_b64}"


def inspect_report_access_token(
    token: str,
    secret: str | None = None,
) -> tuple[TokenStatus, uuid.UUID | None, uuid.UUID | None]:
    """Validate a signed report access token using constant-time comparison.

    Returns:
        (TokenStatus, report_id, patient_id)
        Never logs the token body or raises unexpected exceptions.
    """
    if not token or not isinstance(token, str) or "." not in token:
        return TokenStatus.INVALID, None, None

    parts = token.split(".")
    if len(parts) != 2:
        return TokenStatus.INVALID, None, None

    payload_b64, sig_b64 = parts[0], parts[1]
    secret_key = secret or settings.REPORT_LINK_SECRET

    # Verify signature in constant time
    expected_sig = hmac.new(
        key=secret_key.encode("utf-8"),
        msg=payload_b64.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    try:
        pad = len(sig_b64) % 4
        sig_padded = sig_b64 + ("=" * ((4 - pad) % 4))
        actual_sig = base64.urlsafe_b64decode(sig_padded.encode("utf-8"))
    except Exception:
        return TokenStatus.INVALID, None, None

    if not hmac.compare_digest(expected_sig, actual_sig):
        return TokenStatus.INVALID, None, None

    # Decode payload
    try:
        pad = len(payload_b64) % 4
        payload_padded = payload_b64 + ("=" * ((4 - pad) % 4))
        payload_str = base64.urlsafe_b64decode(payload_padded.encode("utf-8")).decode("utf-8")
        raw_report_id, raw_patient_id, raw_expires_at = payload_str.split(":")
        expires_at = int(raw_expires_at)
        report_id = uuid.UUID(raw_report_id)
        patient_id = uuid.UUID(raw_patient_id)
    except Exception:
        return TokenStatus.INVALID, None, None

    # Check expiry
    now_ts = int(datetime.now(UTC).timestamp())
    if now_ts > expires_at:
        return TokenStatus.EXPIRED, report_id, patient_id

    return TokenStatus.VALID, report_id, patient_id


def generate_report_access_url(
    report_id: uuid.UUID | str,
    patient_id: uuid.UUID | str,
    ttl_hours: int | None = None,
    secret: str | None = None,
    base_url: str | None = None,
) -> str:
    """Generate the full public report access URL."""
    token = generate_report_access_token(
        report_id=report_id,
        patient_id=patient_id,
        ttl_hours=ttl_hours,
        secret=secret,
    )
    api_base = (base_url or settings.API_PUBLIC_URL).rstrip("/")
    return f"{api_base}/api/v1/lab/reports/access/{token}"
