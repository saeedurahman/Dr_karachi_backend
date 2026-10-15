"""Unit tests for WhatsAppClient using httpx.MockTransport.

Tests cover:
- Sending template messages and wamid return
- Sending text messages
- Sending interactive messages (buttons and lists)
- Marking messages as read
- Retry behavior (exponential backoff on 429, 500, 503, timeouts)
- Immediate failure without retry on non-retryable 4xx client errors
- Safe logging (no token or medical message bodies logged)
"""
import json
from typing import Any

import httpx
import pytest
from loguru import logger

from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.exceptions import (
    WhatsAppAuthError,
    WhatsAppClientError,
    WhatsAppTemplateError,
    WhatsAppTransientError,
)

TEST_PHONE_ID = "123456789012345"
TEST_TOKEN = "EAAGtesttokensecret9876543210"
TEST_API_VERSION = "v21.0"
TEST_WAMID = "wamid.HBgNNzk5OTk5OTk5OQ=="


def _create_mock_response(status_code: int, json_data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        headers={"Content-Type": "application/json"},
    )


@pytest.fixture
def base_client_kwargs():
    return {
        "phone_number_id": TEST_PHONE_ID,
        "access_token": TEST_TOKEN,
        "api_version": TEST_API_VERSION,
        "max_retries": 2,
        "base_delay": 0.01,
        "max_delay": 0.05,
    }


# ── 1. Success Flows ─────────────────────────────────────────────────────────

async def test_send_template_success(base_client_kwargs):
    recorded_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        data = {
            "messaging_product": "whatsapp",
            "contacts": [{"input": "923001234567", "wa_id": "923001234567"}],
            "messages": [{"id": TEST_WAMID}],
        }
        return _create_mock_response(200, data)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        params = {
            "patient_name": "Hamza Tariq",
            "doctor_name": "Dr. Ayesha",
            "slot_datetime": "2026-10-20 10:00 AM PKT",
            "clinic_branch": "Main Branch",
        }
        wamid = await client.send_template("03001234567", "appointment_confirmed", params)

        assert wamid == TEST_WAMID
        assert len(recorded_requests) == 1
        req = recorded_requests[0]
        assert req.method == "POST"
        assert f"/{TEST_PHONE_ID}/messages" in str(req.url)
        assert req.headers["Authorization"] == f"Bearer {TEST_TOKEN}"

        body = json.loads(req.content.decode("utf-8"))
        assert body["to"] == "923001234567"
        assert body["type"] == "template"
        assert body["template"]["name"] == "appointment_confirmed"
        assert body["template"]["language"]["code"] == "en"
        assert len(body["template"]["components"][0]["parameters"]) == 4


async def test_send_text_success(base_client_kwargs):
    recorded_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return _create_mock_response(200, {"messages": [{"id": TEST_WAMID}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        wamid = await client.send_text("+92 300 1234567", "Hello, your order is ready!", preview_url=False)

        assert wamid == TEST_WAMID
        assert len(recorded_requests) == 1
        body = json.loads(recorded_requests[0].content.decode("utf-8"))
        assert body["to"] == "923001234567"
        assert body["type"] == "text"
        assert body["text"]["body"] == "Hello, your order is ready!"


async def test_send_interactive_buttons_success(base_client_kwargs):
    recorded_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return _create_mock_response(200, {"messages": [{"id": TEST_WAMID}]})

    action = {
        "buttons": [
            {"type": "reply", "reply": {"id": "btn_yes", "title": "Confirm"}},
            {"type": "reply", "reply": {"id": "btn_no", "title": "Reschedule"}},
        ]
    }

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        wamid = await client.send_interactive(
            to="0300-1234567",
            body_text="Would you like to confirm your appointment?",
            action=action,
            interactive_type="button",
            footer_text="Reply by selecting an option",
        )

        assert wamid == TEST_WAMID
        body = json.loads(recorded_requests[0].content.decode("utf-8"))
        assert body["type"] == "interactive"
        assert body["interactive"]["type"] == "button"
        assert body["interactive"]["footer"]["text"] == "Reply by selecting an option"
        assert len(body["interactive"]["action"]["buttons"]) == 2


async def test_send_interactive_list_success(base_client_kwargs):
    recorded_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return _create_mock_response(200, {"messages": [{"id": TEST_WAMID}]})

    interactive_payload = {
        "type": "list",
        "header": {"type": "text", "text": "Select Service"},
        "body": {"text": "Choose from our available clinic services:"},
        "action": {
            "button": "View Services",
            "sections": [
                {
                    "title": "Clinical",
                    "rows": [
                        {"id": "srv_1", "title": "General Consultation"},
                        {"id": "srv_2", "title": "Lab Tests"},
                    ],
                }
            ],
        },
    }

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        wamid = await client.send_interactive(to="923001234567", interactive=interactive_payload)

        assert wamid == TEST_WAMID
        body = json.loads(recorded_requests[0].content.decode("utf-8"))
        assert body["interactive"]["type"] == "list"
        assert body["interactive"]["action"]["button"] == "View Services"


async def test_mark_as_read_success(base_client_kwargs):
    recorded_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return _create_mock_response(200, {"success": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        result = await client.mark_as_read(TEST_WAMID)

        assert result is True
        assert len(recorded_requests) == 1
        body = json.loads(recorded_requests[0].content.decode("utf-8"))
        assert body["status"] == "read"
        assert body["message_id"] == TEST_WAMID


# ── 2. Retry Behaviors ───────────────────────────────────────────────────────

async def test_retry_on_http_500_then_success(base_client_kwargs):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _create_mock_response(500, {"error": {"message": "Internal error"}})
        return _create_mock_response(200, {"messages": [{"id": TEST_WAMID}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        wamid = await client.send_text("03001234567", "Test retry")

        assert wamid == TEST_WAMID
        assert call_count == 2


async def test_retry_on_http_429_then_success(base_client_kwargs):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _create_mock_response(429, {"error": {"code": 130429, "message": "Rate limit"}})
        return _create_mock_response(200, {"messages": [{"id": TEST_WAMID}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        wamid = await client.send_text("03001234567", "Test rate limit retry")

        assert wamid == TEST_WAMID
        assert call_count == 2


async def test_retry_on_timeout_then_success(base_client_kwargs):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ReadTimeout("Timeout connecting to Meta")
        return _create_mock_response(200, {"messages": [{"id": TEST_WAMID}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        wamid = await client.send_text("03001234567", "Test timeout retry")

        assert wamid == TEST_WAMID
        assert call_count == 2


async def test_no_retry_on_http_400_client_error(base_client_kwargs):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _create_mock_response(
            400,
            {
                "error": {
                    "code": 132001,
                    "message": "Template name does not exist",
                    "type": "OAuthException",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        with pytest.raises(WhatsAppTemplateError) as exc_info:
            await client.send_text("03001234567", "Fail fast")

        assert exc_info.value.error_code == 132001
        # Crucial requirement: Must NOT retry on 4xx client errors
        assert call_count == 1


async def test_exhausted_retries_on_http_503(base_client_kwargs):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _create_mock_response(503, {"error": {"message": "Service Unavailable"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
        with pytest.raises(WhatsAppTransientError):
            await client.send_text("03001234567", "Will exhaust retries")

        # Initial call + 2 retries = 3 attempts total
        assert call_count == 3


# ── 3. Configuration & Privacy ───────────────────────────────────────────────

async def test_missing_access_token_raises_auth_error():
    client = WhatsAppClient(phone_number_id="123", access_token="", max_retries=0)
    with pytest.raises(WhatsAppAuthError) as exc_info:
        await client.send_text("03001234567", "No token")
    assert "WHATSAPP_ACCESS_TOKEN is not configured" in str(exc_info.value)


async def test_missing_phone_id_raises_client_error():
    client = WhatsAppClient(phone_number_id="", access_token="token", max_retries=0)
    with pytest.raises(WhatsAppClientError) as exc_info:
        await client.send_text("03001234567", "No phone id")
    assert "WHATSAPP_PHONE_NUMBER_ID is not configured" in str(exc_info.value)


async def test_privacy_no_token_or_medical_bodies_logged(base_client_kwargs):
    """Verify that neither the access token nor sensitive medical details appear in logs."""
    logs = []

    def log_sink(message):
        logs.append(message.record["message"])

    sink_id = logger.add(log_sink)

    def handler(request: httpx.Request) -> httpx.Response:
        return _create_mock_response(200, {"messages": [{"id": TEST_WAMID}]})

    transport = httpx.MockTransport(handler)
    try:
        async with httpx.AsyncClient(transport=transport) as mock_http:
            client = WhatsAppClient(**base_client_kwargs, http_client=mock_http)
            sensitive_medical_text = "Diagnosis: Chronic Diabetic Kidney Disease Stage 3. Prescribed Metformin 500mg."
            await client.send_text("03001234567", sensitive_medical_text)

        combined_logs = " ".join(logs)
        # Token must NEVER be logged
        assert TEST_TOKEN not in combined_logs
        # Sensitive medical body must NEVER be logged
        assert "Diabetic Kidney Disease" not in combined_logs
        assert "Metformin 500mg" not in combined_logs
        # Recipient must be masked
        assert "923001234567" not in combined_logs
        assert "92300***67" in combined_logs
    finally:
        logger.remove(sink_id)
