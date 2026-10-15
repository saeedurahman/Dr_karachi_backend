"""Unit tests for WhatsApp error mapping and typed exceptions."""
import pytest

from app.services.whatsapp.exceptions import (
    WhatsAppAuthError,
    WhatsAppClientError,
    WhatsAppError,
    WhatsAppRateLimitError,
    WhatsAppSpamRateLimitError,
    WhatsAppTemplateError,
    WhatsAppTransientError,
    WhatsAppUndeliverableError,
    WhatsAppWindowClosedError,
    map_meta_error,
)


def _meta_err(code: int, message: str = "Error occurred", subcode: int | None = None, details: str | None = None):
    err = {"message": message, "code": code, "type": "OAuthException", "fbtrace_id": "trace-123"}
    if subcode is not None:
        err["error_subcode"] = subcode
    if details is not None:
        err["error_data"] = {"details": details}
    return {"error": err}


def test_auth_error_code_190():
    exc = map_meta_error(400, _meta_err(190, "Error validating access token: Session has expired"))
    assert isinstance(exc, WhatsAppAuthError)
    assert exc.error_code == 190
    assert exc.fbtrace_id == "trace-123"
    assert "Session has expired" in str(exc)


def test_auth_error_http_401():
    exc = map_meta_error(401, {"error": {"message": "Unauthorized"}})
    assert isinstance(exc, WhatsAppAuthError)


def test_rate_limit_error_130429():
    exc = map_meta_error(400, _meta_err(130429, "Cloud API message throughput limit reached"))
    assert isinstance(exc, WhatsAppRateLimitError)
    assert exc.error_code == 130429


def test_rate_limit_error_http_429():
    exc = map_meta_error(429, {"error": {"message": "Too Many Requests"}})
    assert isinstance(exc, WhatsAppRateLimitError)


def test_window_closed_error_131047():
    exc = map_meta_error(400, _meta_err(131047, "Re-engagement message outside 24h window"))
    assert isinstance(exc, WhatsAppWindowClosedError)
    assert exc.error_code == 131047


def test_undeliverable_error_131026():
    exc = map_meta_error(400, _meta_err(131026, "Message undeliverable to recipient"))
    assert isinstance(exc, WhatsAppUndeliverableError)
    assert exc.error_code == 131026


def test_spam_rate_limit_131048():
    exc = map_meta_error(400, _meta_err(131048, "Spam rate limit reached"))
    assert isinstance(exc, WhatsAppSpamRateLimitError)
    assert exc.error_code == 131048


@pytest.mark.parametrize("code", [132000, 132001, 132005, 132015, 132999])
def test_template_errors_132000_series(code):
    exc = map_meta_error(400, _meta_err(code, f"Template error code {code}"))
    assert isinstance(exc, WhatsAppTemplateError)
    assert exc.error_code == code


def test_template_error_fallback_text():
    exc = map_meta_error(400, {"error": {"message": "Invalid template parameter count"}})
    assert isinstance(exc, WhatsAppTemplateError)


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_transient_server_errors(status_code):
    exc = map_meta_error(status_code, {"error": {"message": "Server temporarily unavailable"}})
    assert isinstance(exc, WhatsAppTransientError)
    assert exc.status_code == status_code


def test_generic_client_error():
    exc = map_meta_error(400, {"error": {"code": 100, "message": "Invalid parameter"}})
    assert isinstance(exc, WhatsAppClientError)
    assert exc.error_code == 100


def test_base_fallback_error():
    exc = map_meta_error(200, default_message="Unknown issue")
    assert type(exc) is WhatsAppError
