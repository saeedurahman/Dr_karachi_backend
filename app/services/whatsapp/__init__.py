"""WhatsApp Cloud API integration module."""
from app.services.whatsapp.client import WhatsAppClient
from app.services.whatsapp.exceptions import (
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
from app.services.whatsapp.templates import (
    TemplateConfig,
    TemplateRegistry,
    template_registry,
)

__all__ = [
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
    "map_meta_error",
    "template_registry",
]
