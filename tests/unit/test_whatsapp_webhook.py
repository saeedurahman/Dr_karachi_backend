"""WhatsApp webhook tests — no database required."""
import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import create_app

URL = "/api/v1/webhooks/whatsapp"
TOKEN = "test-verify-token"
SECRET = "test-app-secret"


@pytest.fixture(autouse=True)
def _wa_settings(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", SECRET)


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        yield c


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


PAYLOAD = {
    "entry": [{"changes": [{"value": {
        "messages": [{"id": "wamid.1", "from": "923001234567", "type": "text"}],
        "statuses": [{"id": "wamid.2", "status": "delivered", "recipient_id": "923001234567"}],
    }}]}]
}


async def test_verification_ok(client):
    r = await client.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "12345"})
    assert r.status_code == 200
    assert r.text == "12345"
    assert r.headers["content-type"].startswith("text/plain")


async def test_verification_wrong_token(client):
    r = await client.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "12345"})
    assert r.status_code == 403


async def test_post_valid_signature(client, caplog):
    body = json.dumps(PAYLOAD).encode()
    with caplog.at_level("INFO"):
        r = await client.post(URL, content=body, headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"})
    assert r.status_code == 200
    assert "wamid.1" in caplog.text and "delivered" in caplog.text


async def test_post_invalid_signature(client):
    body = json.dumps(PAYLOAD).encode()
    r = await client.post(URL, content=body, headers={"X-Hub-Signature-256": _sign(body, "wrong"), "Content-Type": "application/json"})
    assert r.status_code == 403


async def test_post_missing_signature(client):
    r = await client.post(URL, json=PAYLOAD)
    assert r.status_code == 403
