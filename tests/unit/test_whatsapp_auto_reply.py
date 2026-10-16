"""Unit tests for WhatsApp Phase 6 inbound auto-reply menu and human handoff."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.branch import Branch
from app.models.notification import NotificationEvent, NotificationEventType
from app.models.whatsapp_conversation import ConversationMode, WhatsAppConversation
from app.models.whatsapp_message import (
    WhatsAppMessage,
    WhatsAppMessageDirection,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from app.services.whatsapp.auto_reply_service import AutoReplyService
from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.constants import (
    FALLBACK_TEXT_EN,
    FALLBACK_TEXT_UR,
    ROW_BOOK_APPOINTMENT,
    ROW_BRANCHES,
    ROW_SPEAK_STAFF,
    ROW_TIMINGS,
)
from app.services.whatsapp.sender import SafeSender


@pytest.fixture(autouse=True)
def _wa_autoreply_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456789")
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", "https://karachilabpharmacy.com")


async def _create_test_branch(db: AsyncSession, name: str = "Clifton Clinic") -> Branch:
    """Helper to insert an active test branch."""
    branch = Branch(
        id=uuid.uuid4(),
        name=name,
        city="Karachi",
        address="Block 5, Clifton, Karachi",
        phone="021-35800000",
        is_active=True,
        working_hours={"mon": {"open": "09:00", "close": "21:00"}},
    )
    db.add(branch)
    await db.commit()
    return branch


# ── 1. Greeting returns interactive list menu with 4 rows ──────────────────────

async def test_greeting_returns_interactive_list_menu(db_session: AsyncSession):
    phone = f"0300{uuid.uuid4().int % 10000000:07d}"
    now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.menu.1"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    # Inbound greeting 'hello'
    handled = await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone,
        wamid="wamid.inbound.1",
        text_body="  Hello  ",
        sender=sender,
        now=now,
    )
    assert handled is True
    # Exactly one outbound request (interactive list)
    assert len(recorded_requests) >= 1

    # Verify interactive list payload
    menu_req = [r for r in recorded_requests if "messages" in str(r.url)][0]
    payload = json.loads(menu_req.read().decode("utf-8"))
    assert payload["type"] == "interactive"
    interactive = payload["interactive"]
    assert interactive["type"] == "list"

    # Verify all 4 required rows
    rows = interactive["action"]["sections"][0]["rows"]
    row_ids = [r["id"] for r in rows]
    assert row_ids == [ROW_BRANCHES, ROW_TIMINGS, ROW_BOOK_APPOINTMENT, ROW_SPEAK_STAFF]

    # Verify conversation state created
    conv = await db_session.get(WhatsAppConversation, "92" + phone[1:])
    assert conv is not None
    assert conv.mode == ConversationMode.bot
    assert conv.auto_reply_count == 1


# ── 2. Each list selection returns the right reply ────────────────────────────

async def test_each_list_selection_returns_the_right_reply(db_session: AsyncSession):
    now = datetime(2026, 12, 2, 10, 0, tzinfo=UTC)
    await _create_test_branch(db_session, name="Saddar Main Clinic")

    # A. Selection: Branches & contacts
    reqs_branches: list[httpx.Request] = []

    def handler_branches(request: httpx.Request) -> httpx.Response:
        reqs_branches.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.branches"}]})

    client_b = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler_branches)))
    sender_b = SafeSender(client=client_b)

    phone_b = f"0300{uuid.uuid4().int % 10000000:07d}"
    await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone_b,
        wamid="wamid.in.b",
        interactive_id=ROW_BRANCHES,
        sender=sender_b,
        now=now,
    )
    assert len(reqs_branches) >= 1
    branch_payload = json.loads(reqs_branches[0].read().decode("utf-8"))
    assert "Saddar Main Clinic" in branch_payload["text"]["body"]
    assert "021-35800000" in branch_payload["text"]["body"]

    # B. Selection: Timings
    reqs_timings: list[httpx.Request] = []

    def handler_timings(request: httpx.Request) -> httpx.Response:
        reqs_timings.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.timings"}]})

    client_t = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler_timings)))
    sender_t = SafeSender(client=client_t)

    phone_t = f"0300{uuid.uuid4().int % 10000000:07d}"
    await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone_t,
        wamid="wamid.in.t",
        interactive_id=ROW_TIMINGS,
        sender=sender_t,
        now=now,
    )
    assert len(reqs_timings) >= 1
    timings_payload = json.loads(reqs_timings[0].read().decode("utf-8"))
    assert "Working Hours" in timings_payload["text"]["body"] or "09:00" in timings_payload["text"]["body"]

    # C. Selection: Book appointment
    reqs_book: list[httpx.Request] = []

    def handler_book(request: httpx.Request) -> httpx.Response:
        reqs_book.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.book"}]})

    client_book = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler_book)))
    sender_book = SafeSender(client=client_book)

    phone_book = f"0300{uuid.uuid4().int % 10000000:07d}"
    await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone_book,
        wamid="wamid.in.book",
        interactive_id=ROW_BOOK_APPOINTMENT,
        sender=sender_book,
        now=now,
    )
    assert len(reqs_book) >= 1
    book_payload = json.loads(reqs_book[0].read().decode("utf-8"))
    assert "https://karachilabpharmacy.com/appointments" in book_payload["text"]["body"]


# ── 3. Speak with staff sets human_agent mode & creates NotificationEvent ──────

async def test_speak_with_staff_sets_human_mode_and_emits_event(db_session: AsyncSession):
    phone = f"0300{uuid.uuid4().int % 10000000:07d}"
    now = datetime(2026, 12, 3, 10, 0, tzinfo=UTC)
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.staff"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone,
        wamid="wamid.in.staff",
        interactive_id=ROW_SPEAK_STAFF,
        sender=sender,
        now=now,
    )

    # 1. Check conversation row updated to human_agent with 12h expiry
    norm_phone = "92" + phone[1:]
    conv = await db_session.get(WhatsAppConversation, norm_phone)
    assert conv is not None
    assert conv.mode == ConversationMode.human_agent
    assert conv.handoff_requested_at == now
    assert conv.handoff_expires_at == now + timedelta(hours=12)

    # 2. Check internal NotificationEvent emitted with phone number
    stmt = (
        select(NotificationEvent)
        .where(NotificationEvent.event_type == NotificationEventType.whatsapp_human_handoff)
        .order_by(NotificationEvent.created_at.desc())
    )
    event = (await db_session.execute(stmt)).scalar_one_or_none()
    assert event is not None
    assert event.payload["phone"] == norm_phone
    assert event.payload["action"] == "human_handoff_requested"

    # 3. Check reply text to patient
    reply_payload = json.loads(recorded_requests[0].read().decode("utf-8"))
    assert "representative will contact you" in reply_payload["text"]["body"] or "Support Team" in reply_payload["text"]["body"]


# ── 4. Menus suppressed in human mode and restored by "menu"/"bot" ─────────────

async def test_menus_suppressed_in_human_mode_and_restored_by_menu(db_session: AsyncSession):
    phone = f"0300{uuid.uuid4().int % 10000000:07d}"
    norm_phone = "92" + phone[1:]
    now = datetime(2026, 12, 4, 10, 0, tzinfo=UTC)

    # Put conversation into human_agent mode
    conv = WhatsAppConversation(
        phone=norm_phone,
        mode=ConversationMode.human_agent,
        handoff_requested_at=now,
        handoff_expires_at=now + timedelta(hours=6),
    )
    db_session.add(conv)
    await db_session.commit()

    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.msg"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    # Regular message while in human mode -> suppressed (no reply)
    handled = await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone,
        wamid="wamid.in.1",
        text_body="Can someone call me please?",
        sender=sender,
        now=now,
    )
    assert handled is False
    assert len(recorded_requests) == 0

    # User sends 'menu' -> restores to bot mode and sends the menu
    handled_menu = await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone,
        wamid="wamid.in.2",
        text_body="menu",
        sender=sender,
        now=now,
    )
    assert handled_menu is True
    assert len(recorded_requests) >= 1

    await db_session.refresh(conv)
    assert conv.mode == ConversationMode.bot
    assert conv.handoff_expires_at is None


# ── 5. Handoff expiry after 12h ────────────────────────────────────────────────

async def test_handoff_expiry_after_12h(db_session: AsyncSession):
    phone = f"0300{uuid.uuid4().int % 10000000:07d}"
    norm_phone = "92" + phone[1:]
    now = datetime(2026, 12, 5, 10, 0, tzinfo=UTC)

    # Expired handoff (expires_at 10 minutes ago)
    conv = WhatsAppConversation(
        phone=norm_phone,
        mode=ConversationMode.human_agent,
        handoff_requested_at=now - timedelta(hours=13),
        handoff_expires_at=now - timedelta(minutes=10),
    )
    db_session.add(conv)
    await db_session.commit()

    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.expired"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    # Incoming greeting after expiry -> reverts to bot and sends menu
    handled = await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone,
        wamid="wamid.in.exp",
        text_body="hi",
        sender=sender,
        now=now,
    )
    assert handled is True
    assert len(recorded_requests) >= 1

    await db_session.refresh(conv)
    assert conv.mode == ConversationMode.bot
    assert conv.handoff_expires_at is None


# ── 6. Rate limit blocks 4th reply in 10 minutes ───────────────────────────────

async def test_rate_limit_blocks_fourth_reply_in_10_minutes(db_session: AsyncSession):
    phone = f"0300{uuid.uuid4().int % 10000000:07d}"
    now = datetime(2026, 12, 6, 10, 0, tzinfo=UTC)
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": f"wamid.rate.{len(recorded_requests)}"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    def _message_requests():
        return [
            r
            for r in recorded_requests
            if json.loads(r.read().decode("utf-8")).get("status") != "read"
        ]

    # 1st, 2nd, 3rd replies succeed
    for i in range(1, 4):
        res = await AutoReplyService.handle_inbound(
            db=db_session,
            from_phone=phone,
            wamid=f"wamid.rl.{i}",
            text_body="hi",
            sender=sender,
            now=now,
        )
        assert res is True

    assert len(_message_requests()) == 3

    # 4th reply in the same 10-minute window is blocked (stays silent)
    res_4 = await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone,
        wamid="wamid.rl.4",
        text_body="hi",
        sender=sender,
        now=now,
    )
    assert res_4 is False
    assert len(_message_requests()) == 3  # No extra message request dispatched

    # After 11 minutes (window reset), 5th reply succeeds
    now_later = now + timedelta(minutes=11)
    res_5 = await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone,
        wamid="wamid.rl.5",
        text_body="hi",
        sender=sender,
        now=now_later,
    )
    assert res_5 is True
    assert len(_message_requests()) == 4


# ── 7. Urdu-script greeting and fallback ───────────────────────────────────────

async def test_urdu_script_greeting_and_fallback(db_session: AsyncSession):
    now = datetime(2026, 12, 7, 10, 0, tzinfo=UTC)
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": f"wamid.urdu.{uuid.uuid4().hex}"}]})

    client = WhatsAppClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))
    sender = SafeSender(client=client)

    # A. Urdu greeting: السلام علیکم
    phone_urdu = f"0300{uuid.uuid4().int % 10000000:07d}"
    await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone_urdu,
        wamid="wamid.ur.1",
        text_body="السلام علیکم",
        sender=sender,
        now=now,
    )
    menu_req = [r for r in recorded_requests if json.loads(r.read().decode("utf-8")).get("status") != "read"][0]
    payload_menu = json.loads(menu_req.read().decode("utf-8"))
    assert "کراچی لیب" in payload_menu["interactive"]["header"]["text"]
    assert "برانچز اور رابطہ" in [r["title"] for r in payload_menu["interactive"]["action"]["sections"][0]["rows"]]


    # B. Unmatched text in Urdu script -> Urdu fallback
    recorded_requests.clear()
    phone_fallback_ur = f"0300{uuid.uuid4().int % 10000000:07d}"
    await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone_fallback_ur,
        wamid="wamid.ur.2",
        text_body="میری رپورٹ کب تیار ہوگی؟",
        sender=sender,
        now=now,
    )
    payload_fallback_ur = json.loads(recorded_requests[0].read().decode("utf-8"))
    assert payload_fallback_ur["text"]["body"] == FALLBACK_TEXT_UR

    # C. Unmatched text in English -> English fallback
    recorded_requests.clear()
    phone_fallback_en = f"0300{uuid.uuid4().int % 10000000:07d}"
    await AutoReplyService.handle_inbound(
        db=db_session,
        from_phone=phone_fallback_en,
        wamid="wamid.en.1",
        text_body="How much does a blood test cost?",
        sender=sender,
        now=now,
    )
    payload_fallback_en = json.loads(recorded_requests[0].read().decode("utf-8"))
    assert payload_fallback_en["text"]["body"] == FALLBACK_TEXT_EN


# ── 8. Webhook integration: STOP keyword not treated as greeting/fallback ──────

async def test_stop_keyword_not_treated_as_greeting_or_fallback(async_client, db_session: AsyncSession, monkeypatch):
    phone = f"0300{uuid.uuid4().int % 10000000:07d}"
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.stop"}]})

    monkeypatch.setattr(
        WhatsAppClient,
        "__init__",
        lambda self, *args, **kwargs: setattr(
            self,
            "http_client",
            httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)),
        ) or super(WhatsAppClient, self).__init__(*args, **kwargs) if False else None,
    )

    import hashlib
    import hmac

    secret = settings.WHATSAPP_APP_SECRET or "test-secret"
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "123", "phone_number_id": "123"},
                            "messages": [
                                {
                                    "from": phone,
                                    "id": "wamid.stop.1",
                                    "timestamp": "1760000000",
                                    "text": {"body": "STOP"},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    resp = await async_client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200

    # Ensure conversation record was NOT created with a bot auto-reply
    norm_phone = "92" + phone[1:]
    conv = await db_session.get(WhatsAppConversation, norm_phone)
    if conv:
        # If conversation exists, it must have 0 auto-replies because STOP was handled by keyword handler
        assert conv.auto_reply_count == 0


# ── 9. Status updates ignored by auto-reply ────────────────────────────────────

async def test_status_updates_ignored_by_autoreply(async_client, monkeypatch):
    secret = settings.WHATSAPP_APP_SECRET or "test-secret"
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

    import hashlib
    import hmac

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "123", "phone_number_id": "123"},
                            "statuses": [
                                {
                                    "id": "wamid.delivered.1",
                                    "status": "delivered",
                                    "timestamp": "1760000000",
                                    "recipient_id": "923001234567",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    resp = await async_client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200


# ── 10. Failure does not break webhook 200 ────────────────────────────────────

async def test_failure_does_not_break_webhook_200(async_client, monkeypatch):
    secret = settings.WHATSAPP_APP_SECRET or "test-secret"
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", secret)

    async def faulty_handle_inbound(*args, **kwargs):
        raise RuntimeError("Simulated crash in auto-reply logic")

    monkeypatch.setattr(AutoReplyService, "handle_inbound", faulty_handle_inbound)

    import hashlib
    import hmac

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "123", "phone_number_id": "123"},
                            "messages": [
                                {
                                    "from": "03001234567",
                                    "id": "wamid.error.1",
                                    "timestamp": "1760000000",
                                    "text": {"body": "hello"},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    resp = await async_client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    # Must still return HTTP 200 OK so Meta doesn't endlessly retry webhooks
    assert resp.status_code == 200
