"""Safe WhatsApp message sender guard.

Wraps WhatsAppClient to enforce patient consent permissions (can_send)
before executing any outbound dispatch. Raises ConsentBlockedError if the
target recipient has opted out of the message category.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.consent_service import ConsentService
from app.services.whatsapp.exceptions import ConsentBlockedError
from app.utils.phone import normalize_phone_number


def _mask_phone(phone: str) -> str:
    if len(phone) >= 7:
        return f"{phone[:5]}***{phone[-2:]}"
    return "***"


class SafeSender:
    """Consent-enforcing wrapper around WhatsAppClient.

    Guarantees:
    - Never dispatches promotional templates or marketing messages to users without opt-in.
    - Honors complete opt-outs (STOP ALL) for transactional messages.
    - Raises typed ConsentBlockedError on violation before any HTTP call.
    """

    def __init__(self, client: WhatsAppClient | None = None) -> None:
        self.client = client or WhatsAppClient()

    async def send_template(
        self,
        db: AsyncSession,
        to: str,
        template_key: str,
        params: dict[str, Any] | None = None,
        language: str = "en",
    ) -> str:
        """Evaluate consent and dispatch a registered template message.

        Raises:
            ConsentBlockedError: If recipient preferences block this category.
        """
        template_cfg = self.client.registry.get(template_key)
        category = template_cfg.category

        allowed = await ConsentService.can_send(db, phone=to, category=category)
        if not allowed:
            masked = _mask_phone(to)
            raise ConsentBlockedError(
                f"Outbound WhatsApp template '{template_key}' ({category}) to {masked} "
                "blocked by user consent preferences.",
                phone=to,
                category=category,
            )

        return await self.client.send_template(
            to=to,
            template_key=template_key,
            params=params,
            language=language,
        )

    async def send_text(
        self,
        db: AsyncSession,
        to: str,
        text: str,
        preview_url: bool = False,
        category: str = "transactional",
    ) -> str:
        """Evaluate consent and dispatch a free-form text message.

        Raises:
            ConsentBlockedError: If recipient preferences block this category.
        """
        allowed = await ConsentService.can_send(db, phone=to, category=category)
        if not allowed:
            masked = _mask_phone(to)
            raise ConsentBlockedError(
                f"Outbound WhatsApp text ({category}) to {masked} "
                "blocked by user consent preferences.",
                phone=to,
                category=category,
            )

        return await self.client.send_text(
            to=to,
            text=text,
            preview_url=preview_url,
        )

    async def send_interactive(
        self,
        db: AsyncSession,
        to: str,
        category: str = "transactional",
        **kwargs: Any,
    ) -> str:
        """Evaluate consent and dispatch an interactive message.

        Raises:
            ConsentBlockedError: If recipient preferences block this category.
        """
        allowed = await ConsentService.can_send(db, phone=to, category=category)
        if not allowed:
            masked = _mask_phone(to)
            raise ConsentBlockedError(
                f"Outbound WhatsApp interactive ({category}) to {masked} "
                "blocked by user consent preferences.",
                phone=to,
                category=category,
            )

        return await self.client.send_interactive(to=to, **kwargs)

    async def mark_as_read(self, message_id: str) -> bool:
        """Proxy mark_as_read to underlying client (consent check not applicable)."""
        return await self.client.mark_as_read(message_id)


# Module-level singleton
safe_sender = SafeSender()
