"""WhatsApp automated reply service with interactive list menu and human handoff.

Handles:
- Detecting greeting triggers (English, Roman Urdu, Urdu script)
- Sending interactive List menus with 4 structured options
- Interactive list_reply selections:
    * Branches & contacts (fetched from branches table)
    * Timings (operating schedule from branches/constants)
    * Book appointment (public web portal booking link)
    * Speak with staff (human handoff with 12h expiry & staff notification)
- Returning from human_agent mode to bot mode via 'menu' or 'bot'
- Unmatched text fallback in patient's language (Urdu or English)
- Rate limiting: max 3 automated replies per 10 minutes per phone
- Audit logging via MessageLogService and marking inbound messages read
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.branch import Branch
from app.models.notification import NotificationEventType
from app.models.whatsapp_conversation import ConversationMode, WhatsAppConversation
from app.models.whatsapp_message import WhatsAppMessageType
from app.services.notification_service import emit_event
from app.services.whatsapp.constants import (
    BOOK_APPOINTMENT_TEXT_EN,
    BOOK_APPOINTMENT_TEXT_UR,
    BOT_MODE_RESET_KEYWORDS,
    BRANCHES_FOOTER_EN,
    BRANCHES_FOOTER_UR,
    BRANCHES_HEADER_EN,
    BRANCHES_HEADER_UR,
    DEFAULT_BRANCHES_INFO_EN,
    DEFAULT_BRANCHES_INFO_UR,
    DEFAULT_CLINIC_TIMINGS_EN,
    DEFAULT_CLINIC_TIMINGS_UR,
    FALLBACK_TEXT_EN,
    FALLBACK_TEXT_UR,
    GREETING_TRIGGERS,
    MENU_BODY_EN,
    MENU_BODY_UR,
    MENU_BUTTON_TEXT_EN,
    MENU_BUTTON_TEXT_UR,
    MENU_FOOTER_EN,
    MENU_FOOTER_UR,
    MENU_HEADER_EN,
    MENU_HEADER_UR,
    MENU_ROW_BOOK_APP_DESC_EN,
    MENU_ROW_BOOK_APP_DESC_UR,
    MENU_ROW_BOOK_APP_TITLE_EN,
    MENU_ROW_BOOK_APP_TITLE_UR,
    MENU_ROW_BRANCHES_DESC_EN,
    MENU_ROW_BRANCHES_DESC_UR,
    MENU_ROW_BRANCHES_TITLE_EN,
    MENU_ROW_BRANCHES_TITLE_UR,
    MENU_ROW_SPEAK_STAFF_DESC_EN,
    MENU_ROW_SPEAK_STAFF_DESC_UR,
    MENU_ROW_SPEAK_STAFF_TITLE_EN,
    MENU_ROW_SPEAK_STAFF_TITLE_UR,
    MENU_ROW_TIMINGS_DESC_EN,
    MENU_ROW_TIMINGS_DESC_UR,
    MENU_ROW_TIMINGS_TITLE_EN,
    MENU_ROW_TIMINGS_TITLE_UR,
    MENU_SECTION_TITLE_EN,
    MENU_SECTION_TITLE_UR,
    ROW_BOOK_APPOINTMENT,
    ROW_BRANCHES,
    ROW_SPEAK_STAFF,
    ROW_TIMINGS,
    SPEAK_WITH_STAFF_TEXT_EN,
    SPEAK_WITH_STAFF_TEXT_UR,
    TIMINGS_HEADER_EN,
    TIMINGS_HEADER_UR,
)
from app.services.whatsapp.message_log_service import MessageLogService
from app.services.whatsapp.sender import SafeSender, safe_sender
from app.utils.phone import normalize_phone_number

RATE_LIMIT_WINDOW_MINUTES = 10
RATE_LIMIT_MAX_REPLIES = 3
HUMAN_HANDOFF_DURATION_HOURS = 12


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return "***"
    p = str(phone)
    if len(p) >= 7:
        return f"{p[:5]}***{p[-2:]}"
    return "***"


def is_urdu_script(text: str | None) -> bool:
    """Detect whether text contains characters from the Arabic/Urdu Unicode block."""
    if not text:
        return False
    return bool(re.search(r"[\u0600-\u06FF\u0750-\u077F]", text))


def build_list_menu_payload(is_urdu: bool = False) -> dict[str, Any]:
    """Construct the WhatsApp interactive list message payload with 4 options."""
    if is_urdu:
        header_text = MENU_HEADER_UR
        body_text = MENU_BODY_UR
        button_text = MENU_BUTTON_TEXT_UR
        section_title = MENU_SECTION_TITLE_UR
        footer_text = MENU_FOOTER_UR
        rows = [
            {
                "id": ROW_BRANCHES,
                "title": MENU_ROW_BRANCHES_TITLE_UR,
                "description": MENU_ROW_BRANCHES_DESC_UR,
            },
            {
                "id": ROW_TIMINGS,
                "title": MENU_ROW_TIMINGS_TITLE_UR,
                "description": MENU_ROW_TIMINGS_DESC_UR,
            },
            {
                "id": ROW_BOOK_APPOINTMENT,
                "title": MENU_ROW_BOOK_APP_TITLE_UR,
                "description": MENU_ROW_BOOK_APP_DESC_UR,
            },
            {
                "id": ROW_SPEAK_STAFF,
                "title": MENU_ROW_SPEAK_STAFF_TITLE_UR,
                "description": MENU_ROW_SPEAK_STAFF_DESC_UR,
            },
        ]
    else:
        header_text = MENU_HEADER_EN
        body_text = MENU_BODY_EN
        button_text = MENU_BUTTON_TEXT_EN
        section_title = MENU_SECTION_TITLE_EN
        footer_text = MENU_FOOTER_EN
        rows = [
            {
                "id": ROW_BRANCHES,
                "title": MENU_ROW_BRANCHES_TITLE_EN,
                "description": MENU_ROW_BRANCHES_DESC_EN,
            },
            {
                "id": ROW_TIMINGS,
                "title": MENU_ROW_TIMINGS_TITLE_EN,
                "description": MENU_ROW_TIMINGS_DESC_EN,
            },
            {
                "id": ROW_BOOK_APPOINTMENT,
                "title": MENU_ROW_BOOK_APP_TITLE_EN,
                "description": MENU_ROW_BOOK_APP_DESC_EN,
            },
            {
                "id": ROW_SPEAK_STAFF,
                "title": MENU_ROW_SPEAK_STAFF_TITLE_EN,
                "description": MENU_ROW_SPEAK_STAFF_DESC_EN,
            },
        ]

    return {
        "type": "list",
        "header": {"type": "text", "text": header_text},
        "body": {"text": body_text},
        "footer": {"text": footer_text},
        "action": {
            "button": button_text,
            "sections": [
                {
                    "title": section_title,
                    "rows": rows,
                }
            ],
        },
    }


async def _format_branches_reply(db: AsyncSession, is_urdu: bool = False) -> str:
    """Fetch active branches from DB and format into a concise WhatsApp message."""
    stmt = (
        select(Branch)
        .where(Branch.is_active.is_(True))
        .order_by(Branch.name.asc())
    )
    branches = (await db.execute(stmt)).scalars().all()

    if not branches:
        info = DEFAULT_BRANCHES_INFO_UR if is_urdu else DEFAULT_BRANCHES_INFO_EN
        header = BRANCHES_HEADER_UR if is_urdu else BRANCHES_HEADER_EN
        footer = BRANCHES_FOOTER_UR if is_urdu else BRANCHES_FOOTER_EN
        return f"{header}{info}{footer}"

    lines: list[str] = []
    for idx, b in enumerate(branches, start=1):
        lines.append(f"• *{b.name}*\n  📍 {b.address}\n  📞 {b.phone}")

    header = BRANCHES_HEADER_UR if is_urdu else BRANCHES_HEADER_EN
    footer = BRANCHES_FOOTER_UR if is_urdu else BRANCHES_FOOTER_EN
    return f"{header}" + "\n\n".join(lines) + f"{footer}"


async def _format_timings_reply(db: AsyncSession, is_urdu: bool = False) -> str:
    """Format operating schedule from branches working_hours if present, else fallback."""
    stmt = select(Branch).where(Branch.is_active.is_(True)).order_by(Branch.name.asc())
    branches = (await db.execute(stmt)).scalars().all()

    timings_lines: list[str] = []
    for b in branches:
        if b.working_hours and isinstance(b.working_hours, dict):
            # Check if has standard schedule keys
            wh = b.working_hours
            if "mon" in wh and isinstance(wh["mon"], dict):
                open_t = wh["mon"].get("open", "09:00")
                close_t = wh["mon"].get("close", "21:00")
                timings_lines.append(f"• *{b.name}*: Mon–Sat: {open_t} – {close_t}")

    header = TIMINGS_HEADER_UR if is_urdu else TIMINGS_HEADER_EN
    footer = BRANCHES_FOOTER_UR if is_urdu else BRANCHES_FOOTER_EN

    if timings_lines:
        return f"{header}" + "\n".join(timings_lines) + f"{footer}"

    info = DEFAULT_CLINIC_TIMINGS_UR if is_urdu else DEFAULT_CLINIC_TIMINGS_EN
    return f"{header}{info}{footer}"


class AutoReplyService:
    """Coordinates inbound menu dispatch, selection handling, and human handoff."""

    @classmethod
    async def handle_inbound(
        cls,
        db: AsyncSession,
        from_phone: str,
        wamid: str,
        text_body: str | None = None,
        interactive_id: str | None = None,
        sender: SafeSender | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Evaluate inbound message and execute auto-reply if applicable.

        Args:
            db: Database async session.
            from_phone: Inbound caller phone number.
            wamid: Inbound Meta message ID.
            text_body: Inbound text body if type == text.
            interactive_id: List selection ID or button payload if type == interactive/button.
            sender: Optional SafeSender instance.
            now: Optional current UTC datetime override (for testing).

        Returns:
            True if an auto-reply was dispatched or state transitioned; False otherwise.
        """
        now_utc = now or datetime.now(UTC)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=UTC)

        try:
            normalized_phone = normalize_phone_number(from_phone)
        except Exception:
            normalized_phone = re.sub(r"[^\d]", "", str(from_phone or ""))

        if not normalized_phone:
            logger.warning("AutoReplyService: Empty or invalid normalized phone {}", from_phone)
            return False

        active_sender = sender or safe_sender

        # 1. Retrieve or initialize conversation record
        conv = await db.get(WhatsAppConversation, normalized_phone)
        if not conv:
            conv = WhatsAppConversation(
                phone=normalized_phone,
                mode=ConversationMode.bot,
                last_inbound_at=now_utc,
            )
            db.add(conv)
            await db.flush()
        else:
            conv.last_inbound_at = now_utc

        # 2. Check and handle Human Agent mode
        if conv.mode == ConversationMode.human_agent:
            # Check for handoff expiry (default: 12h)
            if conv.handoff_expires_at and now_utc >= conv.handoff_expires_at:
                logger.info(
                    "WhatsApp human handoff expired for {}, reverting to bot mode",
                    _mask_phone(normalized_phone),
                )
                conv.mode = ConversationMode.bot
                conv.handoff_requested_at = None
                conv.handoff_expires_at = None
            else:
                # Check for explicit reset keyword ('menu' or 'bot')
                cleaned_text = (text_body or "").strip().lower()
                cleaned_id = (interactive_id or "").strip().lower()
                if cleaned_text in BOT_MODE_RESET_KEYWORDS or cleaned_id in BOT_MODE_RESET_KEYWORDS:
                    logger.info(
                        "WhatsApp conversation reset to bot mode by keyword '{}' for {}",
                        cleaned_text or cleaned_id,
                        _mask_phone(normalized_phone),
                    )
                    conv.mode = ConversationMode.bot
                    conv.handoff_requested_at = None
                    conv.handoff_expires_at = None
                    # Fall through to process greeting / menu below
                else:
                    # In human mode: stay silent, do not send menus or fallbacks
                    logger.debug(
                        "In human_agent mode for {}, skipping automated bot reply",
                        _mask_phone(normalized_phone),
                    )
                    await db.flush()
                    return False

        # 3. Check 10-minute rate limit (max 3 auto-replies per 10m)
        if (
            conv.auto_reply_count_window_start is None
            or (now_utc - conv.auto_reply_count_window_start) > timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
        ):
            conv.auto_reply_count_window_start = now_utc
            conv.auto_reply_count = 0

        if conv.auto_reply_count >= RATE_LIMIT_MAX_REPLIES:
            logger.info(
                "WhatsApp auto-reply rate limit reached for {} ({}/{} in 10m), staying silent",
                _mask_phone(normalized_phone),
                conv.auto_reply_count,
                RATE_LIMIT_MAX_REPLIES,
            )
            await db.flush()
            return False

        # 4. Determine response action
        urdu_detected = is_urdu_script(text_body) or is_urdu_script(interactive_id)

        # Case A: Interactive list selection
        if interactive_id:
            return await cls._handle_interactive_selection(
                db=db,
                conv=conv,
                normalized_phone=normalized_phone,
                interactive_id=interactive_id,
                inbound_wamid=wamid,
                sender=active_sender,
                now_utc=now_utc,
                is_urdu=urdu_detected,
            )

        # Case B: Inbound text message
        if text_body is not None:
            cleaned_text = re.sub(r"\s+", " ", text_body.strip()).lower()

            # Check greeting trigger
            if cleaned_text in GREETING_TRIGGERS:
                return await cls._send_main_menu(
                    db=db,
                    conv=conv,
                    normalized_phone=normalized_phone,
                    inbound_wamid=wamid,
                    sender=active_sender,
                    is_urdu=urdu_detected,
                )

            # Unmatched text: send one short fallback in user's language
            fallback_text = FALLBACK_TEXT_UR if urdu_detected else FALLBACK_TEXT_EN
            return await cls._dispatch_text_reply(
                db=db,
                conv=conv,
                normalized_phone=normalized_phone,
                text=fallback_text,
                inbound_wamid=wamid,
                sender=active_sender,
            )

        return False

    @classmethod
    async def _send_main_menu(
        cls,
        db: AsyncSession,
        conv: WhatsAppConversation,
        normalized_phone: str,
        inbound_wamid: str,
        sender: SafeSender,
        is_urdu: bool = False,
    ) -> bool:
        """Send the 4-row interactive list menu."""
        interactive_payload = build_list_menu_payload(is_urdu=is_urdu)

        # Audit log entry for outbound interactive message
        out_msg = await MessageLogService.create_outbound(
            db=db,
            recipient_phone=normalized_phone,
            message_type=WhatsAppMessageType.interactive,
            template_name="auto_reply_menu",
            related_entity_type="conversation",
            related_entity_id=None,
        )

        try:
            reply_wamid = await sender.send_interactive(
                db=db,
                to=normalized_phone,
                interactive=interactive_payload,
            )
            await MessageLogService.mark_sent(db, out_msg.id, wamid=reply_wamid)
            conv.auto_reply_count += 1
            await db.flush()

            # Mark inbound message read
            try:
                await sender.mark_as_read(inbound_wamid)
            except Exception:
                pass

            logger.info(
                "WhatsApp main menu sent to {} | wamid={}",
                _mask_phone(normalized_phone),
                reply_wamid,
            )
            return True
        except Exception as exc:
            logger.error("Failed to send WhatsApp main menu to {}: {}", _mask_phone(normalized_phone), exc)
            await MessageLogService.mark_failed(db, out_msg.id, error_message=str(exc))
            await db.flush()
            return False

    @classmethod
    async def _handle_interactive_selection(
        cls,
        db: AsyncSession,
        conv: WhatsAppConversation,
        normalized_phone: str,
        interactive_id: str,
        inbound_wamid: str,
        sender: SafeSender,
        now_utc: datetime,
        is_urdu: bool = False,
    ) -> bool:
        """Route list selection row id to the appropriate reply or handoff."""
        if interactive_id == ROW_BRANCHES:
            reply_text = await _format_branches_reply(db, is_urdu=is_urdu)
            return await cls._dispatch_text_reply(
                db=db,
                conv=conv,
                normalized_phone=normalized_phone,
                text=reply_text,
                inbound_wamid=inbound_wamid,
                sender=sender,
            )

        elif interactive_id == ROW_TIMINGS:
            reply_text = await _format_timings_reply(db, is_urdu=is_urdu)
            return await cls._dispatch_text_reply(
                db=db,
                conv=conv,
                normalized_phone=normalized_phone,
                text=reply_text,
                inbound_wamid=inbound_wamid,
                sender=sender,
            )

        elif interactive_id == ROW_BOOK_APPOINTMENT:
            base_url = (settings.PUBLIC_APP_URL or "https://karachilabpharmacy.com").rstrip("/")
            appointment_url = f"{base_url}/appointments"
            template = BOOK_APPOINTMENT_TEXT_UR if is_urdu else BOOK_APPOINTMENT_TEXT_EN
            reply_text = template.format(appointment_url=appointment_url)
            return await cls._dispatch_text_reply(
                db=db,
                conv=conv,
                normalized_phone=normalized_phone,
                text=reply_text,
                inbound_wamid=inbound_wamid,
                sender=sender,
            )

        elif interactive_id == ROW_SPEAK_STAFF:
            # 1. Update conversation state to human_agent
            conv.mode = ConversationMode.human_agent
            conv.handoff_requested_at = now_utc
            conv.handoff_expires_at = now_utc + timedelta(hours=HUMAN_HANDOFF_DURATION_HOURS)

            # 2. Emit internal NotificationEvent so staff/reception are alerted with patient phone
            try:
                await emit_event(
                    db=db,
                    event_type=NotificationEventType.whatsapp_human_handoff,
                    payload={
                        "phone": normalized_phone,
                        "action": "human_handoff_requested",
                        "requested_at": now_utc.isoformat(),
                        "expires_at": conv.handoff_expires_at.isoformat(),
                    },
                    delivery_channel="whatsapp",
                )
                logger.info(
                    "Emitted human handoff notification event for phone {}",
                    _mask_phone(normalized_phone),
                )
            except Exception as ev_exc:
                logger.warning("Failed to emit human handoff notification event: {}", ev_exc)

            # 3. Reply to patient with staff handoff confirmation
            reply_text = SPEAK_WITH_STAFF_TEXT_UR if is_urdu else SPEAK_WITH_STAFF_TEXT_EN
            return await cls._dispatch_text_reply(
                db=db,
                conv=conv,
                normalized_phone=normalized_phone,
                text=reply_text,
                inbound_wamid=inbound_wamid,
                sender=sender,
            )

        else:
            # Unknown interactive ID: fallback to menu
            return await cls._send_main_menu(
                db=db,
                conv=conv,
                normalized_phone=normalized_phone,
                inbound_wamid=inbound_wamid,
                sender=sender,
                is_urdu=is_urdu,
            )

    @classmethod
    async def _dispatch_text_reply(
        cls,
        db: AsyncSession,
        conv: WhatsAppConversation,
        normalized_phone: str,
        text: str,
        inbound_wamid: str,
        sender: SafeSender,
    ) -> bool:
        """Dispatch a free-form text auto-reply within the 24h service window."""
        out_msg = await MessageLogService.create_outbound(
            db=db,
            recipient_phone=normalized_phone,
            message_type=WhatsAppMessageType.text,
            template_name="auto_reply_text",
            related_entity_type="conversation",
            related_entity_id=None,
        )

        try:
            reply_wamid = await sender.send_text(
                db=db,
                to=normalized_phone,
                text=text,
            )
            await MessageLogService.mark_sent(db, out_msg.id, wamid=reply_wamid)
            conv.auto_reply_count += 1
            await db.flush()

            try:
                await sender.mark_as_read(inbound_wamid)
            except Exception:
                pass

            logger.info(
                "WhatsApp auto-reply sent to {} | wamid={}",
                _mask_phone(normalized_phone),
                reply_wamid,
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to dispatch WhatsApp auto-reply to {}: {}",
                _mask_phone(normalized_phone),
                exc,
            )
            await MessageLogService.mark_failed(db, out_msg.id, error_message=str(exc))
            await db.flush()
            return False
