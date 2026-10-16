"""WhatsApp appointment reminder and marketing broadcast background worker.

Runs as a separate worker process independent of FastAPI web processes.
Polls for:
1. Upcoming confirmed appointments needing a 1-hour reminder.
2. Scheduled and active WhatsApp marketing broadcast campaigns.

Features:
- Entry point: `python -m app.worker`
- Graceful shutdown on SIGTERM and SIGINT (handles both POSIX and Windows)
- Robust fault tolerance: errors in any tick are logged and never kill the loop
- Configurable interval via settings.WHATSAPP_REMINDER_POLL_SECONDS and settings.WHATSAPP_BROADCAST_POLL_SECONDS
"""
from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

from loguru import logger

from app.config import settings
from app.database import AsyncSessionLocal
from app.services.whatsapp.broadcast_service import BroadcastService
from app.services.whatsapp.reminder_service import AppointmentReminderService


def _setup_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """Attach graceful shutdown handlers for SIGINT and SIGTERM."""
    def _handle_signal(sig_name: str) -> None:
        logger.info("Received shutdown signal ({}), stopping worker gracefully...", sig_name)
        shutdown_event.set()

    # POSIX event-loop signal handling
    loop = asyncio.get_running_loop()
    signals_registered = False

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig.name)
            signals_registered = True
        except (NotImplementedError, AttributeError):
            # ProactorEventLoop on Windows does not support add_signal_handler
            pass

    if not signals_registered:
        # Fallback for Windows / platforms without event loop signal handlers
        def _sync_handler(signum: int, frame: Any) -> None:
            sig_name = "SIGINT" if signum == signal.SIGINT else str(signum)
            logger.info("Received sync signal ({}), stopping worker...", sig_name)
            shutdown_event.set()

        try:
            signal.signal(signal.SIGINT, _sync_handler)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, _sync_handler)
        except (ValueError, AttributeError):
            pass


async def run_broadcast_loop(
    poll_seconds: float | None = None,
    shutdown_event: asyncio.Event | None = None,
    max_ticks: int | None = None,
) -> None:
    """Run the broadcast campaign processor loop until signaled or max_ticks reached."""
    interval = (
        poll_seconds
        if poll_seconds is not None
        else float(settings.WHATSAPP_BROADCAST_POLL_SECONDS)
    )
    stop_event = shutdown_event or asyncio.Event()

    logger.info(
        "WhatsApp broadcast loop started | poll_interval={}s batch_size={} daily_limit={}",
        interval,
        settings.WHATSAPP_BROADCAST_BATCH_SIZE,
        settings.WHATSAPP_DAILY_MARKETING_LIMIT,
    )

    tick_count = 0
    while not stop_event.is_set():
        if max_ticks is not None and tick_count >= max_ticks:
            break

        tick_count += 1
        try:
            async with AsyncSessionLocal() as db:
                sent = await BroadcastService.process_broadcast_batch(db=db)
                if sent > 0:
                    logger.info("Broadcast tick #{} sent {} messages", tick_count, sent)
        except Exception as exc:
            logger.exception("Error during broadcast tick #{}, continuing: {}", tick_count, exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass


async def run_worker(
    poll_seconds: float | None = None,
    shutdown_event: asyncio.Event | None = None,
    max_ticks: int | None = None,
) -> None:
    """Run the unified WhatsApp worker loop (reminders + broadcast batches).

    Args:
        poll_seconds: Seconds to wait between ticks. Defaults to settings.WHATSAPP_REMINDER_POLL_SECONDS.
        shutdown_event: Optional asyncio.Event to control graceful shutdown.
        max_ticks: Optional maximum number of loop ticks to run (useful in tests).
    """
    interval = (
        poll_seconds
        if poll_seconds is not None
        else float(settings.WHATSAPP_REMINDER_POLL_SECONDS)
    )
    stop_event = shutdown_event or asyncio.Event()

    # Only register system signals when running the real worker (not in mocked unit tests)
    if shutdown_event is None:
        _setup_signal_handlers(stop_event)

    logger.info(
        "WhatsApp worker started | poll_interval={}s reminder_batch={} broadcast_batch={}",
        interval,
        settings.WHATSAPP_REMINDER_BATCH_SIZE,
        settings.WHATSAPP_BROADCAST_BATCH_SIZE,
    )

    tick_count = 0
    while not stop_event.is_set():
        if max_ticks is not None and tick_count >= max_ticks:
            logger.info("Reached max_ticks={}, terminating worker loop", max_ticks)
            break

        tick_count += 1
        logger.debug("Worker tick #{} started", tick_count)

        try:
            async with AsyncSessionLocal() as db:
                sent_reminders = await AppointmentReminderService.process_reminders(db=db)
                sent_broadcasts = await BroadcastService.process_broadcast_batch(db=db)
                logger.info(
                    "Worker tick #{} completed | reminders_sent={} broadcasts_sent={}",
                    tick_count,
                    sent_reminders,
                    sent_broadcasts,
                )
        except Exception as exc:
            # Errors in one tick must never terminate the worker process
            logger.exception(
                "Unhandled error during worker tick #{}, continuing loop: {}",
                tick_count,
                exc,
            )

        # Sleep for poll interval or awaken immediately if shutdown signaled
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            logger.info("Shutdown event received, exiting loop.")
            break
        except asyncio.TimeoutError:
            pass

    logger.info("WhatsApp worker stopped cleanly.")


def main() -> None:
    """Entry point for `python -m app.worker`."""
    logger.info("Initializing WhatsApp background worker process...")
    try:
        asyncio.run(run_worker())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker process exited.")


if __name__ == "__main__":
    main()
