"""WhatsApp Cloud API transactional message dispatch handlers.

Handles:
- Appointment confirmed
- Appointment cancelled
- Order placed
- Order status changed
- Lab report ready (with secure signed link and privacy guard)

Guarantees:
- Deduplication via MessageLogService dedupe_keys
- Asia/Karachi (PKT) timestamp rendering (never UTC)
- Consent verification via SafeSender (category transactional)
- Isolated database session execution so dispatches never block or fail user requests
- Safe error handling: logs and records failure via mark_failed without crashing
- Absolute medical privacy: zero test names or medical details in lab report messages
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.appointment import Appointment, AppointmentStatus
from app.models.branch import Branch
from app.models.doctor import Doctor
from app.models.lab_report import LabReport
from app.models.order import Order
from app.models.user import User
from app.models.whatsapp_message import WhatsAppMessage, WhatsAppMessageType
from app.services.whatsapp.consent_service import ConsentService
from app.services.whatsapp.exceptions import ConsentBlockedError, DuplicateMessageError
from app.services.whatsapp.message_log_service import MessageLogService
from app.services.whatsapp.sender import SafeSender
from app.utils.phone import normalize_phone_number
from app.utils.report_security import generate_report_access_url

PKT = ZoneInfo("Asia/Karachi")

# Strong references set to prevent garbage-collection of un-awaited fallback tasks
_background_tasks: set[asyncio.Task] = set()


def _schedule_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def format_pkt_datetime(dt: datetime) -> str:
    """Format UTC datetime into Pakistan Standard Time string.

    Example: '18 Oct 2026, 10:30 AM'
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    pkt_dt = dt.astimezone(PKT)
    return f"{pkt_dt.day} {pkt_dt.strftime('%b %Y, %I:%M %p')}"


def _mask_phone(phone: str) -> str:
    if len(phone) >= 7:
        return f"{phone[:5]}***{phone[-2:]}"
    return "***"


async def dispatch_transactional_message(
    db: AsyncSession,
    *,
    recipient_phone: str | None,
    template_key: str,
    params: dict[str, Any],
    related_entity_type: str,
    related_entity_id: uuid.UUID,
    dedupe_key: str,
    sender: SafeSender | None = None,
) -> WhatsAppMessage | None:
    """Core dispatcher for transactional WhatsApp messages.

    Validates phone, checks consent, handles deduplication, dispatches via SafeSender,
    and records status (sent/failed) in whatsapp_messages audit log.
    """
    if not recipient_phone:
        logger.warning(
            "Missing recipient phone for transactional WhatsApp | dedupe_key={}",
            dedupe_key,
        )
        return None

    try:
        normalized_phone = normalize_phone_number(recipient_phone)
    except Exception as exc:
        logger.warning(
            "Invalid recipient phone for transactional WhatsApp | phone={} dedupe_key={} err={}",
            _mask_phone(str(recipient_phone)),
            dedupe_key,
            exc,
        )
        return None

    # Check consent (transactional category)
    try:
        can_send = await ConsentService.can_send(db, normalized_phone, category="transactional")
        if not can_send:
            logger.warning(
                "Transactional WhatsApp blocked by user consent | phone={} dedupe_key={}",
                _mask_phone(normalized_phone),
                dedupe_key,
            )
            return None
    except Exception as exc:
        logger.error(
            "Error checking consent for transactional WhatsApp | phone={} err={}",
            _mask_phone(normalized_phone),
            exc,
        )
        return None

    # Deduplicate via MessageLogService
    try:
        msg = await MessageLogService.create_outbound(
            db=db,
            recipient_phone=normalized_phone,
            message_type=WhatsAppMessageType.template,
            template_name=template_key,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            dedupe_key=dedupe_key,
            raise_on_duplicate=True,
        )
        await db.commit()
    except DuplicateMessageError:
        logger.warning(
            "Duplicate transactional WhatsApp message skipped | dedupe_key={}",
            dedupe_key,
        )
        return None
    except Exception as exc:
        logger.error(
            "Failed to log outbound message | dedupe_key={} err={}",
            dedupe_key,
            exc,
        )
        return None

    # Dispatch via SafeSender
    safe_sender = sender or SafeSender()
    try:
        wamid = await safe_sender.send_template(
            db=db,
            to=normalized_phone,
            template_key=template_key,
            params=params,
        )
        await MessageLogService.mark_sent(db, msg.id, wamid=wamid)
        await db.commit()
        logger.info(
            "Transactional WhatsApp message dispatched | id={} wamid={} dedupe_key={}",
            msg.id,
            wamid,
            dedupe_key,
        )
        return msg
    except ConsentBlockedError as exc:
        logger.warning(
            "SafeSender blocked transactional message by consent | id={} err={}",
            msg.id,
            exc,
        )
        await MessageLogService.mark_failed(db, msg.id, error_message=str(exc))
        await db.commit()
        return msg
    except Exception as exc:
        logger.error(
            "Failed to dispatch transactional WhatsApp message | id={} dedupe_key={} err={}",
            msg.id,
            dedupe_key,
            exc,
        )
        await MessageLogService.mark_failed(db, msg.id, error_message=str(exc))
        await db.commit()
        return msg


# ── Event Handlers ────────────────────────────────────────────────────────────

async def handle_appointment_confirmed(
    db: AsyncSession,
    appointment_id: uuid.UUID,
    sender: SafeSender | None = None,
) -> WhatsAppMessage | None:
    """Send confirmation template when an appointment is booked or confirmed."""
    stmt = (
        select(Appointment)
        .where(Appointment.id == appointment_id)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.branch),
        )
    )
    appt = (await db.execute(stmt)).scalar_one_or_none()
    if not appt or not appt.patient:
        logger.warning("Appointment not found or missing patient for confirmed event | id={}", appointment_id)
        return None

    patient_name = appt.patient.full_name or "Valued Patient"
    doctor_name = (
        appt.doctor.user.full_name
        if (appt.doctor and appt.doctor.user and appt.doctor.user.full_name)
        else "Doctor"
    )
    slot_formatted = format_pkt_datetime(appt.slot_datetime)
    clinic_branch = appt.branch.name if appt.branch else "Karachi Lab & Clinic"

    params = {
        "patient_name": patient_name,
        "doctor_name": doctor_name,
        "slot_datetime": slot_formatted,
        "clinic_branch": clinic_branch,
    }
    dedupe_key = f"appointment:{appt.id}:confirmed"

    return await dispatch_transactional_message(
        db=db,
        recipient_phone=appt.patient.phone,
        template_key="appointment_confirmed",
        params=params,
        related_entity_type="appointment",
        related_entity_id=appt.id,
        dedupe_key=dedupe_key,
        sender=sender,
    )


async def handle_appointment_cancelled(
    db: AsyncSession,
    appointment_id: uuid.UUID,
    sender: SafeSender | None = None,
) -> WhatsAppMessage | None:
    """Send cancellation template when an appointment is cancelled."""
    stmt = (
        select(Appointment)
        .where(Appointment.id == appointment_id)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
        )
    )
    appt = (await db.execute(stmt)).scalar_one_or_none()
    if not appt or not appt.patient:
        logger.warning("Appointment not found or missing patient for cancelled event | id={}", appointment_id)
        return None

    patient_name = appt.patient.full_name or "Valued Patient"
    doctor_name = (
        appt.doctor.user.full_name
        if (appt.doctor and appt.doctor.user and appt.doctor.user.full_name)
        else "Doctor"
    )
    slot_formatted = format_pkt_datetime(appt.slot_datetime)
    reason = appt.cancellation_reason or "Cancelled by clinic"

    params = {
        "patient_name": patient_name,
        "doctor_name": doctor_name,
        "slot_datetime": slot_formatted,
        "reason": reason,
    }
    dedupe_key = f"appointment:{appt.id}:cancelled"

    return await dispatch_transactional_message(
        db=db,
        recipient_phone=appt.patient.phone,
        template_key="appointment_cancelled",
        params=params,
        related_entity_type="appointment",
        related_entity_id=appt.id,
        dedupe_key=dedupe_key,
        sender=sender,
    )


async def handle_order_placed(
    db: AsyncSession,
    order_id: uuid.UUID,
    sender: SafeSender | None = None,
) -> WhatsAppMessage | None:
    """Send order placement confirmation template."""
    order = await db.get(Order, order_id)
    if not order:
        logger.warning("Order not found for placed event | id={}", order_id)
        return None

    patient = await db.get(User, order.patient_id)
    if not patient:
        logger.warning("Patient not found for order placed event | order_id={}", order_id)
        return None

    customer_name = patient.full_name or "Valued Customer"
    order_number = getattr(order, "order_number", None) or f"ORD-{str(order.id)[:8].upper()}"
    total_amount = f"PKR {order.grand_total:.2f}"

    params = {
        "customer_name": customer_name,
        "order_number": order_number,
        "total_amount": total_amount,
    }
    dedupe_key = f"order:{order.id}:placed"

    return await dispatch_transactional_message(
        db=db,
        recipient_phone=patient.phone,
        template_key="order_confirmed",
        params=params,
        related_entity_type="order",
        related_entity_id=order.id,
        dedupe_key=dedupe_key,
        sender=sender,
    )


async def handle_order_status_changed(
    db: AsyncSession,
    order_id: uuid.UUID,
    new_status: str,
    sender: SafeSender | None = None,
) -> WhatsAppMessage | None:
    """Send order status update notification."""
    order = await db.get(Order, order_id)
    if not order:
        logger.warning("Order not found for status update event | id={}", order_id)
        return None

    patient = await db.get(User, order.patient_id)
    if not patient:
        logger.warning("Patient not found for order status update event | order_id={}", order_id)
        return None

    customer_name = patient.full_name or "Valued Customer"
    order_number = getattr(order, "order_number", None) or f"ORD-{str(order.id)[:8].upper()}"
    status_display = str(new_status).replace("_", " ").capitalize()
    total_amount = f"PKR {order.grand_total:.2f}"

    params = {
        "customer_name": customer_name,
        "order_number": order_number,
        "status": status_display,
        "total_amount": total_amount,
    }
    dedupe_key = f"order:{order.id}:status:{new_status}"

    return await dispatch_transactional_message(
        db=db,
        recipient_phone=patient.phone,
        template_key="order_status",
        params=params,
        related_entity_type="order",
        related_entity_id=order.id,
        dedupe_key=dedupe_key,
        sender=sender,
    )


async def handle_lab_report_ready(
    db: AsyncSession,
    report_id: uuid.UUID,
    sender: SafeSender | None = None,
) -> WhatsAppMessage | None:
    """Send lab report notification with secure access link and absolute privacy."""
    report = await db.get(LabReport, report_id)
    if not report:
        logger.warning("Lab report not found for ready event | id={}", report_id)
        return None

    if not report.is_visible:
        logger.warning("Lab report is not marked visible, skipping notification | id={}", report_id)
        return None

    patient = await db.get(User, report.patient_id)
    if not patient:
        logger.warning("Patient not found for lab report ready event | report_id={}", report_id)
        return None

    # Resolve branch name
    branch_name = "Karachi Lab & Clinic"
    if report.appointment_id:
        appt = await db.get(Appointment, report.appointment_id)
        if appt and appt.branch_id:
            branch = await db.get(Branch, appt.branch_id)
            if branch and branch.name:
                branch_name = branch.name

    # Build secure signed report access link
    report_url = generate_report_access_url(
        report_id=report.id,
        patient_id=report.patient_id,
    )

    # Privacy enforcement: zero test names or medical details included
    patient_name = patient.full_name or "Valued Patient"
    params = {
        "patient_name": patient_name,
        "branch_name": branch_name,
        "report_url": report_url,
    }
    dedupe_key = f"lab_report:{report.id}:ready"

    return await dispatch_transactional_message(
        db=db,
        recipient_phone=patient.phone,
        template_key="lab_report_ready",
        params=params,
        related_entity_type="lab_report",
        related_entity_id=report.id,
        dedupe_key=dedupe_key,
        sender=sender,
    )


# ── Background / Asynchronous Runners ─────────────────────────────────────────

async def _run_isolated_handler(
    handler_func,
    *args,
    session_factory=None,
    sender: SafeSender | None = None,
    **kwargs,
) -> None:
    """Execute handler within a fresh isolated session."""
    factory = session_factory or AsyncSessionLocal
    handler_name = getattr(handler_func, "__name__", str(handler_func))
    try:
        logger.info("Executing background transactional dispatch | handler={}", handler_name)
        async with factory() as session:
            await handler_func(session, *args, sender=sender, **kwargs)
    except Exception as exc:
        logger.exception(
            "Unhandled exception in background transactional dispatch | handler={} err={}",
            handler_name,
            exc,
        )


def trigger_appointment_confirmed(
    appointment_id: uuid.UUID,
    background_tasks: BackgroundTasks | None = None,
    session_factory=None,
    sender: SafeSender | None = None,
) -> None:
    """Trigger appointment confirmed dispatch asynchronously."""
    if background_tasks:
        background_tasks.add_task(
            _run_isolated_handler,
            handle_appointment_confirmed,
            appointment_id,
            session_factory=session_factory,
            sender=sender,
        )
    else:
        _schedule_background_task(
            _run_isolated_handler(
                handle_appointment_confirmed,
                appointment_id,
                session_factory=session_factory,
                sender=sender,
            )
        )


def trigger_appointment_cancelled(
    appointment_id: uuid.UUID,
    background_tasks: BackgroundTasks | None = None,
    session_factory=None,
    sender: SafeSender | None = None,
) -> None:
    """Trigger appointment cancelled dispatch asynchronously."""
    if background_tasks:
        background_tasks.add_task(
            _run_isolated_handler,
            handle_appointment_cancelled,
            appointment_id,
            session_factory=session_factory,
            sender=sender,
        )
    else:
        _schedule_background_task(
            _run_isolated_handler(
                handle_appointment_cancelled,
                appointment_id,
                session_factory=session_factory,
                sender=sender,
            )
        )


def trigger_order_placed(
    order_id: uuid.UUID,
    background_tasks: BackgroundTasks | None = None,
    session_factory=None,
    sender: SafeSender | None = None,
) -> None:
    """Trigger order placed dispatch asynchronously."""
    if background_tasks:
        background_tasks.add_task(
            _run_isolated_handler,
            handle_order_placed,
            order_id,
            session_factory=session_factory,
            sender=sender,
        )
    else:
        _schedule_background_task(
            _run_isolated_handler(
                handle_order_placed,
                order_id,
                session_factory=session_factory,
                sender=sender,
            )
        )


def trigger_order_status_changed(
    order_id: uuid.UUID,
    new_status: str,
    background_tasks: BackgroundTasks | None = None,
    session_factory=None,
    sender: SafeSender | None = None,
) -> None:
    """Trigger order status changed dispatch asynchronously."""
    if background_tasks:
        background_tasks.add_task(
            _run_isolated_handler,
            handle_order_status_changed,
            order_id,
            new_status=new_status,
            session_factory=session_factory,
            sender=sender,
        )
    else:
        _schedule_background_task(
            _run_isolated_handler(
                handle_order_status_changed,
                order_id,
                new_status=new_status,
                session_factory=session_factory,
                sender=sender,
            )
        )


def trigger_lab_report_ready(
    report_id: uuid.UUID,
    background_tasks: BackgroundTasks | None = None,
    session_factory=None,
    sender: SafeSender | None = None,
) -> None:
    """Trigger lab report ready dispatch asynchronously."""
    if background_tasks:
        background_tasks.add_task(
            _run_isolated_handler,
            handle_lab_report_ready,
            report_id,
            session_factory=session_factory,
            sender=sender,
        )
    else:
        _schedule_background_task(
            _run_isolated_handler(
                handle_lab_report_ready,
                report_id,
                session_factory=session_factory,
                sender=sender,
            )
        )
