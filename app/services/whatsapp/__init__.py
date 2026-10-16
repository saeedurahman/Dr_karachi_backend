"""WhatsApp Cloud API integration module."""
from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.consent_service import (
    ConsentService,
    can_send,
    get_or_create,
    set_marketing_opt_in,
    set_transactional,
)
from app.services.whatsapp.exceptions import (
    ConsentBlockedError,
    DuplicateMessageError,
    WhatsAppAuthError,
    WhatsAppClientError,
    WhatsAppError,
    WhatsAppRateLimitError,
    WhatsAppSpamRateLimitError,
    WhatsAppTemplateError,
    WhatsAppTransientError,
    WhatsAppUndeliverableError,
    WhatsAppWindowClosedError,
    map_meta_error,
)
from app.services.whatsapp.keyword_handler import handle_inbound_keyword
from app.services.whatsapp.message_log_service import (
    MessageLogService,
    apply_status_update,
    create_outbound,
    log_inbound,
    mark_failed,
    mark_sent,
)
from app.services.whatsapp.auto_reply_service import AutoReplyService
from app.services.whatsapp.reminder_service import (
    AppointmentReminderService,
    process_appointment_reminders,
)
from app.services.whatsapp.sender import SafeSender, safe_sender
from app.services.whatsapp.templates import (
    TemplateConfig,
    TemplateRegistry,
    template_registry,
)
from app.services.whatsapp.transactional_handlers import (
    dispatch_transactional_message,
    format_pkt_datetime,
    handle_appointment_cancelled,
    handle_appointment_confirmed,
    handle_lab_report_ready,
    handle_order_placed,
    handle_order_status_changed,
    trigger_appointment_cancelled,
    trigger_appointment_confirmed,
    trigger_lab_report_ready,
    trigger_order_placed,
    trigger_order_status_changed,
)

__all__ = [
    "AppointmentReminderService",
    "AutoReplyService",
    "ConsentBlockedError",

    "ConsentService",
    "DuplicateMessageError",
    "MessageLogService",
    "SafeSender",
    "TemplateConfig",
    "TemplateRegistry",
    "WhatsAppAuthError",
    "WhatsAppClient",
    "WhatsAppClientError",
    "WhatsAppError",
    "WhatsAppRateLimitError",
    "WhatsAppSpamRateLimitError",
    "WhatsAppTemplateError",
    "WhatsAppTransientError",
    "WhatsAppUndeliverableError",
    "WhatsAppWindowClosedError",
    "apply_status_update",
    "can_send",
    "create_outbound",
    "get_or_create",
    "handle_inbound_keyword",
    "log_inbound",
    "map_meta_error",
    "mark_failed",
    "mark_sent",
    "process_appointment_reminders",
    "safe_sender",

    "set_marketing_opt_in",
    "set_transactional",
    "template_registry",
    "dispatch_transactional_message",
    "format_pkt_datetime",
    "handle_appointment_cancelled",
    "handle_appointment_confirmed",
    "handle_lab_report_ready",
    "handle_order_placed",
    "handle_order_status_changed",
    "trigger_appointment_cancelled",
    "trigger_appointment_confirmed",
    "trigger_lab_report_ready",
    "trigger_order_placed",
    "trigger_order_status_changed",
]
