"""
WhatsApp Cloud API webhook router — /api/v1/webhooks/whatsapp

Endpoints (public, no auth, not rate-limited — Meta must always reach them):
  GET  /  → Meta verification handshake (hub.* query params)
  POST /  → Event receiver (X-Hub-Signature-256 verified); events are only logged for now
"""
import hashlib
import hmac
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp Webhook"])


def _signature_valid(body: bytes, header: str | None) -> bool:
    secret = settings.WHATSAPP_APP_SECRET
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@router.get("", response_class=PlainTextResponse, summary="Meta webhook verification (Public)")
async def verify_webhook(
    mode: str | None = Query(None, alias="hub.mode"),
    verify_token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
):
    expected = settings.WHATSAPP_VERIFY_TOKEN
    if (
        mode == "subscribe"
        and challenge is not None
        and expected
        and verify_token is not None
        and hmac.compare_digest(verify_token.encode(), expected.encode())
    ):
        return PlainTextResponse(challenge, status_code=status.HTTP_200_OK)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed")


@router.post("", summary="Meta webhook event receiver (Public)")
async def receive_webhook(request: Request) -> Response:
    body = await request.body()
    if not _signature_valid(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    try:
        payload = await request.json()
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    logger.info(
                        "WhatsApp incoming message id=%s from=%s type=%s",
                        msg.get("id"), msg.get("from"), msg.get("type"),
                    )
                for st in value.get("statuses", []):
                    logger.info(
                        "WhatsApp status id=%s status=%s recipient=%s errors=%s",
                        st.get("id"), st.get("status"), st.get("recipient_id"), st.get("errors"),
                    )
    except Exception:  # never fail the webhook: Meta would retry
        logger.exception("Failed to parse WhatsApp webhook payload")

    return Response(status_code=status.HTTP_200_OK)
