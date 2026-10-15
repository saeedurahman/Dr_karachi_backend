"""Typed WhatsApp Cloud API domain exceptions mapped from Meta Graph API error codes."""
from __future__ import annotations

from typing import Any


class WhatsAppError(Exception):
    """Base exception for all WhatsApp Cloud API errors."""

    def __init__(
        self,
        message: str,
        error_code: int | None = None,
        error_subcode: int | None = None,
        fbtrace_id: str | None = None,
        status_code: int | None = None,
        details: str | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.error_subcode = error_subcode
        self.fbtrace_id = fbtrace_id
        self.status_code = status_code
        self.details = details
        self.raw_response = raw_response

    def __str__(self) -> str:
        parts = [self.message]
        if self.error_code is not None:
            parts.append(f"(code: {self.error_code})")
        if self.error_subcode is not None:
            parts.append(f"(subcode: {self.error_subcode})")
        if self.fbtrace_id:
            parts.append(f"[fbtrace_id: {self.fbtrace_id}]")
        return " ".join(parts)


class WhatsAppAuthError(WhatsAppError):
    """Raised when authentication fails (e.g. Meta error 190 expired or invalid access token)."""
    pass


class WhatsAppRateLimitError(WhatsAppError):
    """Raised when throughput or Cloud API rate limit is exceeded (e.g. Meta error 130429 or HTTP 429)."""
    pass


class WhatsAppWindowClosedError(WhatsAppError):
    """Raised when attempting a free-form message outside the 24-hour customer care window (Meta error 131047)."""
    pass


class WhatsAppUndeliverableError(WhatsAppError):
    """Raised when message is undeliverable (Meta error 131026: recipient not on WhatsApp, blocked, etc.)."""
    pass


class WhatsAppTemplateError(WhatsAppError):
    """Raised when template lookup, parameters, or Meta validation fails (Meta 132000-series errors)."""
    pass


class WhatsAppSpamRateLimitError(WhatsAppError):
    """Raised when Meta flags spam rate limits or account restrictions (Meta error 131048)."""
    pass


class WhatsAppTransientError(WhatsAppError):
    """Raised on transient network timeouts or server errors (HTTP 500, 502, 503, 504) that can be retried."""
    pass


class WhatsAppClientError(WhatsAppError):
    """Raised on generic non-retryable 4xx client errors from Meta Graph API."""
    pass


def map_meta_error(
    status_code: int,
    response_data: dict[str, Any] | None = None,
    default_message: str | None = None,
) -> WhatsAppError:
    """Map HTTP status code and Meta Graph API error payload to a typed WhatsApp exception."""
    error_dict: dict[str, Any] = {}
    if isinstance(response_data, dict) and "error" in response_data:
        raw_err = response_data["error"]
        if isinstance(raw_err, dict):
            error_dict = raw_err

    code: int | None = error_dict.get("code")
    subcode: int | None = error_dict.get("error_subcode")
    fbtrace_id: str | None = error_dict.get("fbtrace_id")
    error_data = error_dict.get("error_data")
    details: str | None = (
        error_data.get("details") if isinstance(error_data, dict) else (str(error_data) if error_data else None)
    )

    msg = error_dict.get("message") or default_message or f"WhatsApp API HTTP {status_code}"

    kwargs = {
        "message": msg,
        "error_code": code,
        "error_subcode": subcode,
        "fbtrace_id": fbtrace_id,
        "status_code": status_code,
        "details": details,
        "raw_response": response_data,
    }

    # Meta Error 190: Invalid/Expired OAuth Token, or HTTP 401
    if code == 190 or status_code == 401:
        return WhatsAppAuthError(**kwargs)

    # Meta Error 130429: Cloud API Rate Limit Hit, or HTTP 429
    if code == 130429 or status_code == 429:
        return WhatsAppRateLimitError(**kwargs)

    # Meta Error 131047: Outside 24h customer care window
    if code == 131047:
        return WhatsAppWindowClosedError(**kwargs)

    # Meta Error 131026: Message Undeliverable
    if code == 131026:
        return WhatsAppUndeliverableError(**kwargs)

    # Meta Error 131048: Spam rate limit
    if code == 131048:
        return WhatsAppSpamRateLimitError(**kwargs)

    # Meta 132000-series: Template errors (e.g. 132000, 132001, 132005, 132007, 132012, 132015)
    if (code is not None and 132000 <= code <= 132999) or (
        status_code == 400 and "template" in msg.lower()
    ):
        return WhatsAppTemplateError(**kwargs)

    # Transient 5xx server errors
    if status_code in (500, 502, 503, 504):
        return WhatsAppTransientError(**kwargs)

    # Other 4xx client errors
    if 400 <= status_code < 500:
        return WhatsAppClientError(**kwargs)

    return WhatsAppError(**kwargs)
