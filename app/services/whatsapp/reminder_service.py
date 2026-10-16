"""WhatsApp appointment reminder service.

Handles:
- Polling for upcoming confirmed appointments within a 1-hour window (default: 55-65 mins before slot)
- Concurrent-safe row locking via SELECT ... FOR UPDATE SKIP LOCKED
- Idempotent outbound message creation using deduplication key "appointment:{id}:reminder_1h"
- Dispatching transactional WhatsApp template 'appointment_reminder_1h' rendered in Asia/Karachi (PKT)
- Safe error handling: consent block, invalid phone, or WhatsApp API failure recorded via mark_failed
  without crashing worker loop or endlessly retrying past slots
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.appointment import Appointment, AppointmentStatus
from app.models.branch import Branch
from app.models.doctor import Doctor
from app.models.user import User
from app.models.whatsapp_message import (
    WhatsAppMessage,
    WhatsAppMessageType,
)
from app.services.whatsapp.consent_service import ConsentService
from app.services.whatsapp.exceptions import (
    ConsentBlockedError,
    DuplicateMessageError,
)
from app.services.whatsapp.message_log_service import MessageLogService
from app.services.whatsapp.sender import SafeSender
from app.services.whatsapp.transactional_handlers import format_pkt_datetime
from app.utils.phone import normalize_phone_number

PKT = ZoneInfo("Asia/Karachi")


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return "***"
    phone_str = str(phone)
    if len(phone_str) >= 7:
        return f"{phone_str[:5]}***{phone_str[-2:]}"
    return "***"


class AppointmentReminderService:
    """Service for selecting upcoming appointments and dispatching 1-hour reminders."""

    @classmethod
    async def process_reminders(
        cls,
        db: AsyncSession,
        sender: SafeSender | None = None,
        now: datetime | None = None,
        window_start_minutes: int | None = None,
        window_end_minutes: int | None = None,
        batch_size: int | None = None,
    ) -> int:
        """Process one batch of appointment reminders.

        Args:
            db: Active async database session.
            sender: Optional custom SafeSender instance (e.g. for testing with mocks).
            now: Optional override for current UTC datetime (useful in tests).
            window_start_minutes: Optional override for window start (default: settings.WHATSAPP_REMINDER_WINDOW_START_MINUTES).
            window_end_minutes: Optional override for window end (default: settings.WHATSAPP_REMINDER_WINDOW_END_MINUTES).
            batch_size: Optional override for batch limit (default: settings.WHATSAPP_REMINDER_BATCH_SIZE).

        Returns:
            Number of reminders successfully dispatched in this tick.
        """
        now_utc = now or datetime.now(UTC)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=UTC)

        w_start = (
            window_start_minutes
            if window_start_minutes is not None
            else settings.WHATSAPP_REMINDER_WINDOW_START_MINUTES
        )
        w_end = (
            window_end_minutes
            if window_end_minutes is not None
            else settings.WHATSAPP_REMINDER_WINDOW_END_MINUTES
        )
        limit = (
            batch_size
            if batch_size is not None
            else settings.WHATSAPP_REMINDER_BATCH_SIZE
        )

        window_start = now_utc + timedelta(minutes=w_start)
        window_end = now_utc + timedelta(minutes=w_end)

        # 1. Subquery to exclude appointments that already have an outbound 1h reminder log
        reminder_exists = (
            select(1)
            .select_from(WhatsAppMessage)
            .where(
                WhatsAppMessage.related_entity_type == "appointment",
                WhatsAppMessage.related_entity_id == Appointment.id,
                WhatsAppMessage.template_name == "appointment_reminder_1h",
            )
            .exists()
        )

        # 2. Select candidate appointment IDs with row-level lock (SKIP LOCKED for concurrent workers)
        select_stmt = (
            select(Appointment.id)
            .where(
                Appointment.status == AppointmentStatus.confirmed,
                Appointment.slot_datetime >= window_start,
                Appointment.slot_datetime <= window_end,
                ~reminder_exists,
            )
            .order_by(Appointment.slot_datetime.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

        result = await db.execute(select_stmt)
        candidate_ids = list(result.scalars().all())

        if not candidate_ids:
            return 0

        # 3. Eager load relations for the locked appointments
        fetch_stmt = (
            select(Appointment)
            .where(Appointment.id.in_(candidate_ids))
            .options(
                selectinload(Appointment.patient),
                selectinload(Appointment.doctor).selectinload(Doctor.user),
                selectinload(Appointment.branch),
            )
            .order_by(Appointment.slot_datetime.asc())
        )
        appts = (await db.execute(fetch_stmt)).scalars().all()

        sent_count = 0
        safe_sender = sender or SafeSender()

        for appt in appts:
            try:
                dispatched = await cls._process_single_appointment(
                    db=db,
                    appt=appt,
                    sender=safe_sender,
                    now_utc=now_utc,
                )
                if dispatched:
                    sent_count += 1
            except Exception as exc:
                # One appointment failure must never break the loop for others
                logger.exception(
                    "Unexpected error processing reminder for appointment_id={}: {}",
                    appt.id,
                    exc,
                )
                try:
                    await db.rollback()
                except Exception:
                    pass

        return sent_count

    @classmethod
    async def _process_single_appointment(
        cls,
        db: AsyncSession,
        appt: Appointment,
        sender: SafeSender,
        now_utc: datetime,
    ) -> bool:
        """Process a single appointment reminder with status verification and idempotency."""
        # 1. Pre-send re-checks: confirmed status & slot strictly in the future
        if appt.status != AppointmentStatus.confirmed:
            logger.info(
                "Appointment {} status is '{}', skipping reminder",
                appt.id,
                appt.status.value if hasattr(appt.status, "value") else appt.status,
            )
            return False

        slot_dt = (
            appt.slot_datetime
            if appt.slot_datetime.tzinfo is not None
            else appt.slot_datetime.replace(tzinfo=UTC)
        )
        if slot_dt <= now_utc:
            logger.info(
                "Appointment {} slot ({}) is in the past, skipping reminder",
                appt.id,
                slot_dt,
            )
            return False

        dedupe_key = f"appointment:{appt.id}:reminder_1h"

        # Ensure relationships are loaded if appt was created/passed without selectinload
        from sqlalchemy import inspect
        insp = inspect(appt)
        if "patient" in insp.unloaded or "doctor" in insp.unloaded or "branch" in insp.unloaded:
            fetch_stmt = (
                select(Appointment)
                .where(Appointment.id == appt.id)
                .options(
                    selectinload(Appointment.patient),
                    selectinload(Appointment.doctor).selectinload(Doctor.user),
                    selectinload(Appointment.branch),
                )
            )
            reloaded = (await db.execute(fetch_stmt)).scalar_one_or_none()
            if reloaded:
                appt = reloaded

        # 2. Validate patient phone number
        raw_phone = appt.patient.phone if appt.patient else None
        if not raw_phone:
            logger.warning(
                "Appointment {} has no patient phone, skipping reminder",
                appt.id,
            )
            return False

        try:
            normalized_phone = normalize_phone_number(raw_phone)
        except Exception as exc:
            logger.warning(
                "Invalid phone '{}' for appointment {}, recording failure: {}",
                _mask_phone(raw_phone),
                appt.id,
                exc,
            )
            # Record failure in audit log so it won't be retried endlessly
            try:
                msg = await MessageLogService.create_outbound(
                    db=db,
                    recipient_phone=raw_phone,
                    message_type=WhatsAppMessageType.template,
                    template_name="appointment_reminder_1h",
                    related_entity_type="appointment",
                    related_entity_id=appt.id,
                    dedupe_key=dedupe_key,
                    raise_on_duplicate=True,
                )
                await MessageLogService.mark_failed(
                    db,
                    msg.id,
                    error_message=f"Invalid phone number: {exc}",
                )
                await db.commit()
            except DuplicateMessageError:
                pass
            except Exception as log_exc:
                logger.error("Failed to record invalid phone failure log: {}", log_exc)
                await db.rollback()
            return False

        # 3. Create outbound message log entry (idempotency barrier)
        try:
            msg = await MessageLogService.create_outbound(
                db=db,
                recipient_phone=normalized_phone,
                message_type=WhatsAppMessageType.template,
                template_name="appointment_reminder_1h",
                related_entity_type="appointment",
                related_entity_id=appt.id,
                dedupe_key=dedupe_key,
                raise_on_duplicate=True,
            )
            await db.commit()
        except DuplicateMessageError:
            logger.info(
                "Reminder already exists for appointment {}, skipping | dedupe_key={}",
                appt.id,
                dedupe_key,
            )
            return False
        except Exception as exc:
            logger.error(
                "Failed to log outbound reminder for appointment {} | err={}",
                appt.id,
                exc,
            )
            await db.rollback()
            return False

        # 4. Prepare template parameters (Asia/Karachi formatted, minimum details, zero medical info)
        patient_name = (
            appt.patient.full_name
            if (appt.patient and appt.patient.full_name)
            else "Valued Patient"
        )
        doctor_name = (
            appt.doctor.user.full_name
            if (appt.doctor and appt.doctor.user and appt.doctor.user.full_name)
            else "Doctor"
        )
        slot_formatted = format_pkt_datetime(appt.slot_datetime)
        clinic_branch = (
            appt.branch.name
            if (appt.branch and appt.branch.name)
            else "Karachi Lab & Clinic"
        )

        params = {
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "slot_datetime": slot_formatted,
            "clinic_branch": clinic_branch,
        }

        # 5. Dispatch via SafeSender (checks transactional consent, calls WhatsAppClient)
        try:
            wamid = await sender.send_template(
                db=db,
                to=normalized_phone,
                template_key="appointment_reminder_1h",
                params=params,
            )
            await MessageLogService.mark_sent(db, msg.id, wamid=wamid)
            await db.commit()
            logger.info(
                "Appointment reminder sent successfully | appt_id={} wamid={} dedupe_key={}",
                appt.id,
                wamid,
                dedupe_key,
            )
            return True
        except ConsentBlockedError as exc:
            logger.info(
                "Appointment reminder blocked by user consent | appt_id={} phone={} err={}",
                appt.id,
                _mask_phone(normalized_phone),
                exc,
            )
            await MessageLogService.mark_failed(db, msg.id, error_message=str(exc))
            await db.commit()
            return False
        except Exception as exc:
            logger.error(
                "Failed to send WhatsApp appointment reminder | appt_id={} err={}",
                appt.id,
                exc,
            )
            await MessageLogService.mark_failed(db, msg.id, error_message=str(exc))
            await db.commit()
            return False


# Convenience module-level function
process_appointment_reminders = AppointmentReminderService.process_reminders
