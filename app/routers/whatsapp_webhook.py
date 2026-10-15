"""
WhatsApp Cloud API webhook router — /api/v1/webhooks/whatsapp

Endpoints (public, no auth, not rate-limited — Meta must always reach them):
  GET  /  → Meta verification handshake (hub.* query params)
  POST /  → Event receiver (X-Hub-Signature-256 verified); events are only logged for now
"""
import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services.whatsapp.keyword_handler import handle_inbound_keyword
from app.services.whatsapp.message_log_service import MessageLogService

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
async def receive_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    body = await request.body()
    if not _signature_valid(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

    try:
        payload = await request.json()
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # 1. Monotonic status updates from Meta
                for st in value.get("statuses", []):
                    wamid = st.get("id")
                    raw_status = st.get("status")
                    ts = st.get("timestamp")

                    err_code: int | None = None
                    err_msg: str | None = None
                    errors = st.get("errors")
                    if isinstance(errors, list) and len(errors) > 0 and isinstance(errors[0], dict):
                        raw_code = errors[0].get("code")
                        if raw_code is not None:
                            try:
                                err_code = int(raw_code)
                            except (ValueError, TypeError):
                                err_code = None
                        err_msg = errors[0].get("title") or errors[0].get("message")

                    if wamid and raw_status:
                        logger.info("WhatsApp status id=%s status=%s", wamid, raw_status)
                        await MessageLogService.apply_status_update(
                            db=db,
                            wamid=wamid,
                            status=raw_status,
                            timestamp=ts,
                            error_code=err_code,
                            error_message=err_msg,
                        )

                # 2. Idempotent inbound message logging
                for msg in value.get("messages", []):
                    wamid = msg.get("id")
                    from_phone = msg.get("from")
                    msg_type = msg.get("type", "text")

                    if wamid and from_phone:
                        logger.info("WhatsApp incoming message id=%s from=%s type=%s", wamid, from_phone, msg_type)
                        await MessageLogService.log_inbound(
                            db=db,
                            wamid=wamid,
                            phone=str(from_phone),
                            message_type=msg_type,
                        )

                        # Process consent keywords for text messages
                        if msg_type == "text":
                            text_dict = msg.get("text", {})
                            text_body = text_dict.get("body") if isinstance(text_dict, dict) else None
                            if text_body:
                                await handle_inbound_keyword(
                                    db=db,
                                    from_phone=str(from_phone),
                                    text_body=text_body,
                                )

        await db.commit()
    except Exception:
        # Never fail the webhook endpoint: Meta would re-attempt delivery indefinitely
        try:
            await db.rollback()
        except Exception:
            pass
        logger.exception("Failed to process WhatsApp webhook payload")

    return Response(status_code=status.HTTP_200_OK)
