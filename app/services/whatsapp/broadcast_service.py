"""WhatsApp broadcast campaign service.

Handles segmentation resolution, consent pre-checks, campaign queueing,
worker batch execution with rate limiting and daily caps, and real-time statistics.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.order import Order
from app.models.user import User
from app.models.whatsapp_broadcast import (
    BroadcastRecipientStatus,
    BroadcastStatus,
    WhatsAppBroadcast,
    WhatsAppBroadcastRecipient,
)
from app.models.whatsapp_consent import WhatsAppConsent
from app.models.whatsapp_message import (
    WhatsAppMessage,
    WhatsAppMessageDirection,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.consent_service import ConsentService
from app.services.whatsapp.exceptions import (
    ConsentBlockedError,
    WhatsAppRateLimitError,
    WhatsAppSpamRateLimitError,
    WhatsAppTemplateError,
)
from app.services.whatsapp.message_log_service import MessageLogService
from app.services.whatsapp.sender import SafeSender
from app.utils.phone import InvalidPhoneNumberError, normalize_phone_number


class BroadcastService:
    """Service for managing WhatsApp marketing broadcasts and batch delivery."""

    @staticmethod
    def validate_marketing_template(
        client: WhatsAppClient,
        template_key: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Validate that template exists, has category 'marketing', and required parameters are supplied."""
        if not client.registry.has(template_key):
            raise WhatsAppTemplateError(
                f"Template key '{template_key}' not found in template registry."
            )

        template_cfg = client.registry.get(template_key)
        if template_cfg.category != "marketing":
            raise ValueError(
                f"Only marketing templates are allowed for broadcasts. "
                f"Template '{template_key}' has category '{template_cfg.category}'."
            )

        # Validate parameters against registered body and header params
        all_allowed_params = set(template_cfg.body_params) | set(template_cfg.header_params)
        given_params = params or {}

        unknown = set(given_params.keys()) - all_allowed_params
        if unknown:
            raise ValueError(f"Unknown template parameters provided: {sorted(unknown)}")

        # Check required static body params (names like customer_name/patient_name can be resolved per-recipient)
        required_static = [
            p for p in template_cfg.body_params
            if p not in ("customer_name", "patient_name")
        ]
        missing = [p for p in required_static if p not in given_params or not str(given_params[p]).strip()]
        if missing:
            raise ValueError(f"Missing required template parameters: {sorted(missing)}")

    @staticmethod
    async def resolve_segment_recipients(
        db: AsyncSession,
        segment_filters: dict[str, Any],
        branch_id: uuid.UUID | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Resolve candidates based on segmentation criteria and evaluate consent.

        marketing_opt_in is strictly non-overridable and must ALWAYS be true.

        Returns:
            (eligible_recipients, skipped_recipients)
        """
        # Base query for users
        query = select(User).where(User.deleted_at.is_(None))

        # 1. Branch filter
        eff_branch_id = branch_id or segment_filters.get("branch_id")
        if eff_branch_id:
            branch_uuid = (
                eff_branch_id
                if isinstance(eff_branch_id, uuid.UUID)
                else uuid.UUID(str(eff_branch_id))
            )
            appt_users = select(Appointment.patient_id).where(Appointment.branch_id == branch_uuid)
            order_users = select(Order.patient_id).where(Order.branch_id == branch_uuid)
            query = query.where(
                or_(
                    User.branch_id == branch_uuid,
                    User.id.in_(appt_users),
                    User.id.in_(order_users),
                )
            )

        # 2. Doctor filter
        doctor_id = segment_filters.get("doctor_id")
        if doctor_id:
            doc_uuid = (
                doctor_id
                if isinstance(doctor_id, uuid.UUID)
                else uuid.UUID(str(doctor_id))
            )
            appt_users = select(Appointment.patient_id).where(Appointment.doctor_id == doc_uuid)
            query = query.where(User.id.in_(appt_users))

        # 3. Specialty filter
        specialty = segment_filters.get("specialty")
        if specialty and str(specialty).strip():
            specialty_docs = select(Doctor.id).where(
                Doctor.specialization.ilike(f"%{str(specialty).strip()}%")
            )
            appt_users = select(Appointment.patient_id).where(Appointment.doctor_id.in_(specialty_docs))
            query = query.where(User.id.in_(appt_users))

        # 4. Active within last N days filter
        active_days = segment_filters.get("active_within_days")
        if active_days is not None and int(active_days) > 0:
            cutoff = datetime.now(UTC) - timedelta(days=int(active_days))
            active_appts = select(Appointment.patient_id).where(
                or_(Appointment.created_at >= cutoff, Appointment.slot_datetime >= cutoff)
            )
            active_orders = select(Order.patient_id).where(Order.created_at >= cutoff)
            query = query.where(
                or_(
                    User.created_at >= cutoff,
                    User.updated_at >= cutoff,
                    User.id.in_(active_appts),
                    User.id.in_(active_orders),
                )
            )

        # 5. Has placed order filter
        has_placed_order = segment_filters.get("has_placed_order")
        if has_placed_order is True:
            ordered_users = select(Order.patient_id)
            query = query.where(User.id.in_(ordered_users))

        user_candidates = (await db.execute(query)).scalars().all()

        eligible_recipients: list[dict[str, Any]] = []
        skipped_recipients: list[dict[str, Any]] = []
        seen_phones: set[str] = set()

        # Collect user-based candidates
        raw_candidates: list[tuple[str, uuid.UUID | None]] = [
            (u.phone, u.id) for u in user_candidates if u.phone
        ]

        # If no strict user filters are specified, also consider consented phones without user_id
        if not (eff_branch_id or doctor_id or specialty or active_days or has_placed_order):
            extra_consents_stmt = select(WhatsAppConsent.phone, WhatsAppConsent.user_id).where(
                WhatsAppConsent.marketing_opt_in.is_(True),
                WhatsAppConsent.opt_out_at.is_(None),
                WhatsAppConsent.user_id.is_(None),
            )
            extra_consents = (await db.execute(extra_consents_stmt)).all()
            for row in extra_consents:
                raw_candidates.append((row[0], row[1]))

        # Pre-fetch marketing consents for deduplicated candidate phones
        all_raw_phones = [p for p, _ in raw_candidates if p]
        consents_map: dict[str, WhatsAppConsent] = {}
        if all_raw_phones:
            consents_stmt = select(WhatsAppConsent).where(WhatsAppConsent.phone.in_(all_raw_phones))
            fetched_consents = (await db.execute(consents_stmt)).scalars().all()
            consents_map = {c.phone: c for c in fetched_consents}

        for raw_phone, user_id in raw_candidates:
            # 1. Normalize phone
            try:
                clean_phone = normalize_phone_number(raw_phone)
            except (InvalidPhoneNumberError, Exception):
                skipped_recipients.append({
                    "phone": str(raw_phone or "")[:20],
                    "user_id": user_id,
                    "status": BroadcastRecipientStatus.skipped,
                    "skip_reason": "invalid_phone",
                })
                continue

            # 2. Deduplication
            if clean_phone in seen_phones:
                skipped_recipients.append({
                    "phone": clean_phone,
                    "user_id": user_id,
                    "status": BroadcastRecipientStatus.skipped,
                    "skip_reason": "duplicate",
                })
                continue
            seen_phones.add(clean_phone)

            # 3. Check marketing consent
            consent = consents_map.get(clean_phone)
            if not consent:
                # Query in case of different formatting
                stmt = select(WhatsAppConsent).where(WhatsAppConsent.phone == clean_phone)
                consent = (await db.execute(stmt)).scalar_one_or_none()

            has_marketing_consent = bool(
                consent and consent.marketing_opt_in and (consent.opt_out_at is None)
            )

            if not has_marketing_consent:
                skipped_recipients.append({
                    "phone": clean_phone,
                    "user_id": user_id,
                    "status": BroadcastRecipientStatus.skipped,
                    "skip_reason": "no_marketing_opt_in",
                })
                continue

            eligible_recipients.append({
                "phone": clean_phone,
                "user_id": user_id,
                "status": BroadcastRecipientStatus.pending,
                "skip_reason": None,
            })

        return eligible_recipients, skipped_recipients

    @staticmethod
    async def create_broadcast(
        db: AsyncSession,
        name: str,
        template_key: str,
        created_by: uuid.UUID,
        language: str = "en",
        params: dict[str, Any] | None = None,
        segment_filters: dict[str, Any] | None = None,
        branch_id: uuid.UUID | None = None,
        scheduled_at: datetime | None = None,
        client: WhatsAppClient | None = None,
    ) -> WhatsAppBroadcast:
        """Create and persist a new broadcast draft or scheduled campaign."""
        wa_client = client or WhatsAppClient()
        effective_params = params or {}
        effective_filters = dict(segment_filters or {})

        # Marketing opt-in is always forced to True in segment_filters
        effective_filters["marketing_opt_in"] = True

        if branch_id:
            effective_filters["branch_id"] = str(branch_id)

        # Validate template
        BroadcastService.validate_marketing_template(
            client=wa_client,
            template_key=template_key,
            params=effective_params,
        )

        now = datetime.now(UTC)
        is_scheduled = scheduled_at is not None and scheduled_at > now
        status = BroadcastStatus.scheduled if is_scheduled else BroadcastStatus.draft

        broadcast = WhatsAppBroadcast(
            name=name,
            template_key=template_key,
            language=language,
            params=effective_params,
            segment_filters=effective_filters,
            branch_id=branch_id,
            status=status,
            scheduled_at=scheduled_at if is_scheduled else None,
            created_by=created_by,
        )
        db.add(broadcast)
        await db.commit()
        await db.refresh(broadcast)
        return broadcast

    @staticmethod
    async def preview_broadcast(
        db: AsyncSession,
        broadcast: WhatsAppBroadcast,
    ) -> dict[str, Any]:
        """Compute candidate and eligible counts for preview without dispatching."""
        eligible, skipped = await BroadcastService.resolve_segment_recipients(
            db=db,
            segment_filters=broadcast.segment_filters or {},
            branch_id=broadcast.branch_id,
        )

        skip_counter = Counter(item["skip_reason"] for item in skipped if item.get("skip_reason"))

        return {
            "broadcast_id": broadcast.id,
            "total_candidates": len(eligible) + len(skipped),
            "eligible_count": len(eligible),
            "skipped_count": len(skipped),
            "skip_reasons": dict(skip_counter),
        }

    @staticmethod
    async def start_broadcast(
        db: AsyncSession,
        broadcast_id: uuid.UUID,
    ) -> WhatsAppBroadcast:
        """Resolve recipients, populate recipient queue, and transition broadcast to 'sending'."""
        broadcast = await db.get(WhatsAppBroadcast, broadcast_id)
        if not broadcast:
            raise ValueError("Broadcast not found.")

        # Idempotency: if already sending, return without re-queueing
        if broadcast.status == BroadcastStatus.sending:
            return broadcast

        if broadcast.status in (BroadcastStatus.completed, BroadcastStatus.cancelled):
            raise ValueError(f"Cannot start broadcast with status '{broadcast.status}'.")

        # Resolve segment
        eligible, skipped = await BroadcastService.resolve_segment_recipients(
            db=db,
            segment_filters=broadcast.segment_filters or {},
            branch_id=broadcast.branch_id,
        )

        # Insert eligible recipients as pending (deduplicating phones)
        recipients_to_insert: list[WhatsAppBroadcastRecipient] = []
        inserted_phones: set[str] = set()

        for item in eligible:
            phone = item["phone"]
            if phone not in inserted_phones:
                inserted_phones.add(phone)
                recipients_to_insert.append(
                    WhatsAppBroadcastRecipient(
                        broadcast_id=broadcast.id,
                        phone=phone,
                        user_id=item["user_id"],
                        status=BroadcastRecipientStatus.pending,
                        skip_reason=None,
                    )
                )

        # Also persist unique skipped recipients for complete auditing and stats
        for item in skipped:
            phone = item["phone"]
            if phone not in inserted_phones:
                inserted_phones.add(phone)
                recipients_to_insert.append(
                    WhatsAppBroadcastRecipient(
                        broadcast_id=broadcast.id,
                        phone=phone,
                        user_id=item["user_id"],
                        status=BroadcastRecipientStatus.skipped,
                        skip_reason=item.get("skip_reason"),
                    )
                )

        db.add_all(recipients_to_insert)

        broadcast.total_recipients = len(eligible)
        broadcast.status = BroadcastStatus.sending
        broadcast.started_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(broadcast)
        return broadcast

    @staticmethod
    async def cancel_broadcast(
        db: AsyncSession,
        broadcast_id: uuid.UUID,
    ) -> WhatsAppBroadcast:
        """Cancel an in-progress or scheduled broadcast immediately."""
        broadcast = await db.get(WhatsAppBroadcast, broadcast_id)
        if not broadcast:
            raise ValueError("Broadcast not found.")

        broadcast.status = BroadcastStatus.cancelled
        await db.commit()
        await db.refresh(broadcast)
        logger.info("WhatsApp broadcast '{}' ({}) cancelled.", broadcast.name, broadcast.id)
        return broadcast

    @staticmethod
    async def get_broadcast_stats(
        db: AsyncSession,
        broadcast_id: uuid.UUID,
    ) -> dict[str, int]:
        """Compute real-time broadcast delivery stats from recipients and whatsapp_messages."""
        stmt = (
            select(
                WhatsAppBroadcastRecipient.status,
                WhatsAppBroadcastRecipient.message_id,
                WhatsAppMessage.status.label("msg_status"),
            )
            .outerjoin(
                WhatsAppMessage,
                WhatsAppBroadcastRecipient.message_id == WhatsAppMessage.id,
            )
            .where(WhatsAppBroadcastRecipient.broadcast_id == broadcast_id)
        )
        rows = (await db.execute(stmt)).all()

        pending = 0
        sent = 0
        delivered = 0
        read = 0
        failed = 0
        skipped = 0

        for r_status, msg_id, msg_status in rows:
            if r_status == BroadcastRecipientStatus.pending:
                pending += 1
            elif r_status == BroadcastRecipientStatus.skipped:
                skipped += 1
            elif r_status == BroadcastRecipientStatus.failed:
                failed += 1
            elif r_status == BroadcastRecipientStatus.sent:
                if msg_status == WhatsAppMessageStatus.delivered:
                    delivered += 1
                elif msg_status == WhatsAppMessageStatus.read:
                    read += 1
                elif msg_status == WhatsAppMessageStatus.failed:
                    failed += 1
                else:
                    sent += 1

        total = pending + sent + delivered + read + failed + skipped
        return {
            "total": total,
            "pending": pending,
            "sent": sent,
            "delivered": delivered,
            "read": read,
            "failed": failed,
            "skipped": skipped,
        }

    @staticmethod
    async def process_broadcast_batch(
        db: AsyncSession,
        broadcast_id: uuid.UUID | None = None,
        sender: SafeSender | None = None,
        batch_size: int | None = None,
        delay_seconds: float | None = None,
        daily_limit: int | None = None,
        now: datetime | None = None,
    ) -> int:
        """Worker execution tick for broadcast queues.

        Processes pending recipients using SELECT ... FOR UPDATE SKIP LOCKED,
        enforcing batch sizes, delay intervals, hard daily marketing caps,
        pre-send consent verification, and rate limit error handling.
        """
        current_time = now or datetime.now(UTC)
        eff_batch_size = (
            batch_size
            if batch_size is not None
            else settings.WHATSAPP_BROADCAST_BATCH_SIZE
        )
        eff_delay = (
            delay_seconds
            if delay_seconds is not None
            else settings.WHATSAPP_BROADCAST_DELAY_SECONDS
        )
        eff_daily_limit = (
            daily_limit
            if daily_limit is not None
            else settings.WHATSAPP_DAILY_MARKETING_LIMIT
        )
        safe_sender = sender or SafeSender()

        # 1. Auto-start scheduled broadcasts whose time has arrived
        sched_stmt = select(WhatsAppBroadcast).where(
            WhatsAppBroadcast.status == BroadcastStatus.scheduled,
            WhatsAppBroadcast.scheduled_at <= current_time,
        )
        if broadcast_id:
            sched_stmt = sched_stmt.where(WhatsAppBroadcast.id == broadcast_id)

        due_scheduled = (await db.execute(sched_stmt)).scalars().all()
        for b in due_scheduled:
            try:
                await BroadcastService.start_broadcast(db, b.id)
            except Exception as exc:
                logger.error("Failed to auto-start scheduled broadcast {}: {}", b.id, exc)

        # 2. Check daily marketing limit (count outbound broadcast messages created today in UTC)
        today_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count_stmt = (
            select(func.count())
            .select_from(WhatsAppMessage)
            .where(
                WhatsAppMessage.direction == WhatsAppMessageDirection.outbound,
                WhatsAppMessage.related_entity_type == "broadcast",
                WhatsAppMessage.created_at >= today_start,
            )
        )
        daily_sent = (await db.scalar(daily_count_stmt)) or 0
        if daily_sent >= eff_daily_limit:
            logger.warning(
                "Daily WhatsApp marketing limit reached ({}/{}). Pausing broadcasts until next day.",
                daily_sent,
                eff_daily_limit,
            )
            return 0

        # 3. Find active sending broadcasts
        active_stmt = (
            select(WhatsAppBroadcast)
            .where(WhatsAppBroadcast.status == BroadcastStatus.sending)
            .order_by(WhatsAppBroadcast.started_at.asc(), WhatsAppBroadcast.created_at.asc())
        )
        if broadcast_id:
            active_stmt = active_stmt.where(WhatsAppBroadcast.id == broadcast_id)

        active_broadcasts = (await db.execute(active_stmt)).scalars().all()
        if not active_broadcasts:
            return 0

        total_sent_in_tick = 0

        for broadcast in active_broadcasts:
            remaining_quota = eff_daily_limit - (daily_sent + total_sent_in_tick)
            if remaining_quota <= 0:
                logger.warning("Daily marketing cap reached during tick. Pausing.")
                break

            limit_count = min(eff_batch_size, remaining_quota)

            # Query pending recipients with FOR UPDATE SKIP LOCKED
            recipient_stmt = (
                select(WhatsAppBroadcastRecipient)
                .where(
                    WhatsAppBroadcastRecipient.broadcast_id == broadcast.id,
                    WhatsAppBroadcastRecipient.status == BroadcastRecipientStatus.pending,
                )
                .order_by(WhatsAppBroadcastRecipient.created_at.asc())
                .limit(limit_count)
                .with_for_update(skip_locked=True)
            )
            recipients = (await db.execute(recipient_stmt)).scalars().all()

            if not recipients:
                # Check if there are any remaining pending recipients across all workers
                pending_check = await db.scalar(
                    select(func.count()).where(
                        WhatsAppBroadcastRecipient.broadcast_id == broadcast.id,
                        WhatsAppBroadcastRecipient.status == BroadcastRecipientStatus.pending,
                    )
                )
                if (pending_check or 0) == 0:
                    broadcast.status = BroadcastStatus.completed
                    broadcast.completed_at = datetime.now(UTC)
                    await db.commit()
                    logger.info("Broadcast '{}' ({}) marked completed.", broadcast.name, broadcast.id)
                continue

            for recipient in recipients:
                # Check if broadcast was cancelled mid-batch
                current_broadcast_status = await db.scalar(
                    select(WhatsAppBroadcast.status).where(WhatsAppBroadcast.id == broadcast.id)
                )
                if current_broadcast_status == BroadcastStatus.cancelled:
                    logger.info("Broadcast {} is cancelled, stopping batch dispatch.", broadcast.id)
                    break

                # Check daily quota again
                if (daily_sent + total_sent_in_tick) >= eff_daily_limit:
                    logger.warning("Daily marketing quota exhausted. Pausing broadcast processing.")
                    return total_sent_in_tick

                # 4. Pre-send consent verification (opt-outs mid-broadcast must be respected)
                can_send = await ConsentService.can_send(
                    db, phone=recipient.phone, category="marketing"
                )
                if not can_send:
                    recipient.status = BroadcastRecipientStatus.skipped
                    recipient.skip_reason = "opted_out_before_send"
                    await db.commit()
                    logger.info(
                        "Recipient {} opted out before broadcast dispatch. Skipped.",
                        recipient.phone,
                    )
                    continue

                # 5. Idempotency via dedupe_key: "broadcast:{id}:{phone}"
                dedupe_key = f"broadcast:{broadcast.id}:{recipient.phone}"
                msg = await MessageLogService.create_outbound(
                    db=db,
                    recipient_phone=recipient.phone,
                    message_type=WhatsAppMessageType.template,
                    template_name=broadcast.template_key,
                    related_entity_type="broadcast",
                    related_entity_id=broadcast.id,
                    dedupe_key=dedupe_key,
                )

                if msg.status in (
                    WhatsAppMessageStatus.sent,
                    WhatsAppMessageStatus.delivered,
                    WhatsAppMessageStatus.read,
                ):
                    recipient.message_id = msg.id
                    recipient.status = BroadcastRecipientStatus.sent
                    await db.commit()
                    continue

                # Build parameters
                dispatch_params = dict(broadcast.params or {})
                if "customer_name" not in dispatch_params and "patient_name" not in dispatch_params:
                    user_name = "Valued Customer"
                    if recipient.user_id:
                        u = await db.get(User, recipient.user_id)
                        if u and u.full_name:
                            user_name = u.full_name
                    dispatch_params["customer_name"] = user_name

                # 6. Dispatch
                try:
                    wamid = await safe_sender.send_template(
                        db=db,
                        to=recipient.phone,
                        template_key=broadcast.template_key,
                        params=dispatch_params,
                        language=broadcast.language,
                    )
                    await MessageLogService.mark_sent(db, msg.id, wamid=wamid)
                    recipient.message_id = msg.id
                    recipient.status = BroadcastRecipientStatus.sent
                    recipient.skip_reason = None
                    total_sent_in_tick += 1
                    await db.commit()
                except ConsentBlockedError:
                    recipient.status = BroadcastRecipientStatus.skipped
                    recipient.skip_reason = "opted_out_before_send"
                    await db.commit()
                except Exception as exc:
                    # Check for rate limit or quality errors (e.g. 131048, 130429, HTTP 429)
                    is_rate_limit = (
                        isinstance(exc, (WhatsAppRateLimitError, WhatsAppSpamRateLimitError))
                        or getattr(exc, "error_code", None) in (131048, 130429)
                        or getattr(exc, "status_code", None) == 429
                    )
                    if is_rate_limit:
                        logger.warning(
                            "Broadcast {} paused due to WhatsApp rate limit/quality error (code={}): {}",
                            broadcast.id,
                            getattr(exc, "error_code", None),
                            exc,
                        )
                        # Keep recipient pending; exit tick so worker doesn't hammer API
                        await db.rollback()
                        return total_sent_in_tick
                    else:
                        logger.error(
                            "Failed to dispatch broadcast to {}: {}", recipient.phone, exc
                        )
                        await MessageLogService.mark_failed(
                            db, msg.id, error_code="send_error", error_message=str(exc)
                        )
                        recipient.message_id = msg.id
                        recipient.status = BroadcastRecipientStatus.failed
                        recipient.skip_reason = str(exc)[:255]
                        await db.commit()

                # Configurable delay between sends
                if eff_delay > 0:
                    await asyncio.sleep(eff_delay)

            # Check if broadcast completed after this batch
            remaining_pending = await db.scalar(
                select(func.count()).where(
                    WhatsAppBroadcastRecipient.broadcast_id == broadcast.id,
                    WhatsAppBroadcastRecipient.status == BroadcastRecipientStatus.pending,
                )
            )
            if (remaining_pending or 0) == 0:
                broadcast.status = BroadcastStatus.completed
                broadcast.completed_at = datetime.now(UTC)
                await db.commit()
                logger.info("Broadcast '{}' ({}) completed.", broadcast.name, broadcast.id)

        return total_sent_in_tick
