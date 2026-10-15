"""WhatsApp user consent management service.

Handles:
- Checking messaging permissions (transactional vs promotional)
- Managing opt-in / opt-out states with audit trails
- Default policy: Unknown numbers permit transactional messages, block marketing
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.whatsapp_consent import WhatsAppConsent
from app.utils.phone import normalize_phone_number


def _clean_phone(phone: str) -> str:
    """Normalize phone number to Pakistani E.164 digits, or strip non-digits as fallback."""
    try:
        return normalize_phone_number(phone)
    except Exception:
        return re.sub(r"[^\d]", "", str(phone or ""))


class ConsentService:
    """Service for managing WhatsApp consent preferences and permission evaluations."""

    @staticmethod
    async def get_or_create(
        db: AsyncSession,
        phone: str,
        user_id: uuid.UUID | None = None,
    ) -> WhatsAppConsent:
        """Retrieve existing WhatsAppConsent row or create a default record.

        Default state:
        - transactional_opt_in = True
        - marketing_opt_in = False
        """
        clean_phone = _clean_phone(phone)
        stmt = select(WhatsAppConsent).where(WhatsAppConsent.phone == clean_phone)
        consent = (await db.execute(stmt)).scalar_one_or_none()

        if consent:
            if user_id and not consent.user_id:
                consent.user_id = user_id
                await db.flush()
            return consent

        consent = WhatsAppConsent(
            phone=clean_phone,
            user_id=user_id,
            transactional_opt_in=True,
            marketing_opt_in=False,
            history=[],
        )
        db.add(consent)
        await db.flush()
        return consent

    @staticmethod
    async def set_marketing_opt_in(
        db: AsyncSession,
        phone: str,
        value: bool,
        source: str,
        user_id: uuid.UUID | None = None,
    ) -> WhatsAppConsent:
        """Update promotional/marketing opt-in status with audit entry."""
        consent = await ConsentService.get_or_create(db, phone, user_id=user_id)
        now = datetime.now(UTC)
        action = "marketing_opt_in" if value else "marketing_opt_out"

        consent.marketing_opt_in = value
        if value:
            consent.opt_in_source = source
            consent.opt_out_at = None
        else:
            consent.opt_out_at = now

        entry = {
            "at": now.isoformat(),
            "action": action,
            "source": source,
        }
        # Reassign history to trigger SQLAlchemy dirty check on JSON column
        current_history = list(consent.history or [])
        current_history.append(entry)
        consent.history = current_history

        await db.flush()
        logger.info(
            "WhatsApp marketing consent updated | phone={} value={} source={}",
            consent.phone,
            value,
            source,
        )
        return consent

    @staticmethod
    async def set_transactional(
        db: AsyncSession,
        phone: str,
        value: bool,
        source: str,
    ) -> WhatsAppConsent:
        """Update transactional messaging opt-in status with audit entry."""
        consent = await ConsentService.get_or_create(db, phone)
        now = datetime.now(UTC)
        action = "transactional_opt_in" if value else "transactional_opt_out"

        consent.transactional_opt_in = value

        entry = {
            "at": now.isoformat(),
            "action": action,
            "source": source,
        }
        current_history = list(consent.history or [])
        current_history.append(entry)
        consent.history = current_history

        await db.flush()
        logger.info(
            "WhatsApp transactional consent updated | phone={} value={} source={}",
            consent.phone,
            value,
            source,
        )
        return consent

    @staticmethod
    async def can_send(
        db: AsyncSession,
        phone: str,
        category: str = "transactional",
    ) -> bool:
        """Evaluate if an outbound WhatsApp message of the given category can be sent.

        Rules:
        - "transactional": requires transactional_opt_in=True (allowed by default for unknown phones)
        - "marketing": requires marketing_opt_in=True (blocked by default for unknown phones)
        """
        clean_phone = _clean_phone(phone)
        cat = (category or "transactional").strip().lower()

        stmt = select(WhatsAppConsent).where(WhatsAppConsent.phone == clean_phone)
        consent = (await db.execute(stmt)).scalar_one_or_none()

        # Unknown phone: transactional allowed, marketing blocked
        if not consent:
            return cat == "transactional"

        if cat == "marketing":
            return bool(consent.marketing_opt_in)
        if cat == "transactional":
            return bool(consent.transactional_opt_in)

        # Unknown category defaults to False for safety
        return False


# Module-level convenience functions
get_or_create = ConsentService.get_or_create
set_marketing_opt_in = ConsentService.set_marketing_opt_in
set_transactional = ConsentService.set_transactional
can_send = ConsentService.can_send
