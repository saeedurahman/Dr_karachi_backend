"""Inbound keyword processing for WhatsApp consent management (STOP, STOP ALL, START)."""
from __future__ import annotations

import re
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.consent_service import ConsentService
from app.services.whatsapp.constants import (
    KEYWORD_START,
    KEYWORD_STOP,
    KEYWORD_STOP_ALL,
    START_CONFIRMATION_EN,
    STOP_ALL_CONFIRMATION_EN,
    STOP_CONFIRMATION_EN,
)


def _mask_phone(phone: str) -> str:
    if len(phone) >= 7:
        return f"{phone[:5]}***{phone[-2:]}"
    return "***"


async def handle_inbound_keyword(
    db: AsyncSession,
    from_phone: str,
    text_body: str | None,
    client: WhatsAppClient | None = None,
) -> bool:
    """Evaluate inbound text message against consent keywords.

    Triggers:
    - STOP, UNSUBSCRIBE: Opts out of marketing only. Transactional remains active.
    - STOP ALL: Opts out of both marketing and transactional messages.
    - START, UNSTOP: Opts in to marketing and resumes transactional messages.

    Does NOT log raw message bodies.
    Returns True if a keyword was recognized and handled, False otherwise.
    """
    if not text_body or not isinstance(text_body, str):
        return False

    # Normalize whitespace: collapse multiple spaces and trim
    cleaned = re.sub(r"\s+", " ", text_body.strip()).upper()

    confirmation_text: str | None = None
    keyword_matched: str | None = None

    if cleaned in KEYWORD_STOP_ALL:
        keyword_matched = "STOP ALL"
        await ConsentService.set_marketing_opt_in(
            db, from_phone, value=False, source="whatsapp_keyword"
        )
        await ConsentService.set_transactional(
            db, from_phone, value=False, source="whatsapp_keyword"
        )
        confirmation_text = STOP_ALL_CONFIRMATION_EN

    elif cleaned in KEYWORD_STOP:
        keyword_matched = "STOP"
        await ConsentService.set_marketing_opt_in(
            db, from_phone, value=False, source="whatsapp_keyword"
        )
        # Transactional stays on
        confirmation_text = STOP_CONFIRMATION_EN

    elif cleaned in KEYWORD_START:
        keyword_matched = "START"
        await ConsentService.set_marketing_opt_in(
            db, from_phone, value=True, source="whatsapp_keyword"
        )
        await ConsentService.set_transactional(
            db, from_phone, value=True, source="whatsapp_keyword"
        )
        confirmation_text = START_CONFIRMATION_EN

    if not keyword_matched:
        return False

    logger.info(
        "WhatsApp consent keyword processed | from={} keyword={}",
        _mask_phone(from_phone),
        keyword_matched,
    )

    # Send confirmation message via WhatsAppClient (inside the 24h window)
    if confirmation_text:
        wa_client = client or WhatsAppClient()
        try:
            await wa_client.send_text(to=from_phone, text=confirmation_text)
        except Exception as e:
            # Never let confirmation dispatch failure break the webhook execution
            logger.warning(
                "Failed to dispatch consent confirmation to {} | error={}",
                _mask_phone(from_phone),
                e,
            )

    return True
