"""Async WhatsApp Cloud API client with connection pooling, retries, and typed errors."""
from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
from loguru import logger

from app.config import settings
from app.services.whatsapp.exceptions import (
    WhatsAppAuthError,
    WhatsAppClientError,
    WhatsAppError,
    WhatsAppTransientError,
    map_meta_error,
)
from app.services.whatsapp.templates import TemplateRegistry, template_registry as default_registry
from app.utils.phone import normalize_phone_number

# HTTP status codes eligible for automatic retry
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _mask_phone(phone: str) -> str:
    """Mask phone number for safe logging (e.g. 92300***67)."""
    if len(phone) >= 7:
        return f"{phone[:5]}***{phone[-2:]}"
    return "***"


class WhatsAppClient:
    """Async client for Meta WhatsApp Cloud API (Graph API).

    Features:
    - Connection pooling and timeout configuration via httpx.AsyncClient.
    - Automatic exponential backoff with jitter on timeouts and HTTP 429/500/503.
    - Strict avoidance of retries on 4xx client errors (400, 401, 403, 404, etc.).
    - Returns Meta message ID (`wamid`) for outbound messages.
    - Safe logging: NEVER logs access tokens or sensitive message body contents.
    - Phone number normalization to 12-digit Pakistani format without plus sign.
    """

    def __init__(
        self,
        phone_number_id: str | None = None,
        access_token: str | None = None,
        api_version: str | None = None,
        registry: TemplateRegistry | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 5.0,
    ) -> None:
        self.phone_number_id = phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID
        self.access_token = access_token or settings.WHATSAPP_ACCESS_TOKEN
        self.api_version = api_version or settings.WHATSAPP_API_VERSION
        self.registry = registry or default_registry
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

        # HTTP client lifecycle
        self._custom_client = http_client
        self._client: httpx.AsyncClient | None = http_client
        self._owns_client = http_client is None

        # Connection pool and timeout configuration
        self.limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=50,
            keepalive_expiry=30.0,
        )
        self.timeout = httpx.Timeout(
            timeout=10.0,
            connect=5.0,
            read=10.0,
            write=10.0,
        )

    @property
    def base_url(self) -> str:
        """Meta Graph API base URL for the configured phone number ID."""
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}"

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazily initialize or return the existing httpx.AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                limits=self.limits,
                timeout=self.timeout,
            )
            self._owns_client = True
        return self._client

    async def __aenter__(self) -> WhatsAppClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._owns_client and self._client and not self._client.is_closed:
            await self._client.aclose()

    def _get_headers(self) -> dict[str, str]:
        """Construct sanitized request headers containing the Bearer token."""
        if not self.access_token:
            raise WhatsAppAuthError(
                "WHATSAPP_ACCESS_TOKEN is not configured. Outbound calls cannot be authenticated."
            )
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _execute_with_retry(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
        log_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute HTTP request with exponential backoff and jitter on retryable errors."""
        headers = self._get_headers()
        masked_to = _mask_phone(str(log_context.get("to", "")))
        msg_type = log_context.get("type", "unknown")

        attempt = 0
        last_error: Exception | None = None

        while attempt <= self.max_retries:
            attempt += 1
            logger.info(
                "WhatsApp API request | method={} type={} to={} attempt={}/{}",
                method,
                msg_type,
                masked_to,
                attempt,
                self.max_retries + 1,
            )

            try:
                response = await self.client.request(
                    method=method,
                    url=url,
                    json=payload,
                    headers=headers,
                )

                # Successful Meta response (2xx)
                if response.status_code < 400:
                    data = response.json() if response.content else {}
                    logger.info(
                        "WhatsApp API success | status={} to={} type={}",
                        response.status_code,
                        masked_to,
                        msg_type,
                    )
                    return data

                # Check if error is retryable (429, 500, 502, 503, 504)
                if response.status_code in RETRYABLE_STATUS_CODES:
                    resp_data = None
                    try:
                        resp_data = response.json()
                    except Exception:
                        pass

                    mapped_exc = map_meta_error(
                        response.status_code,
                        resp_data,
                        default_message=f"HTTP {response.status_code} from WhatsApp API",
                    )
                    last_error = mapped_exc

                    if attempt <= self.max_retries:
                        delay = min(
                            self.max_delay,
                            self.base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25 * self.base_delay),
                        )
                        logger.warning(
                            "WhatsApp API retryable HTTP error | status={} to={} attempt={}/{} retrying_in={:.2f}s",
                            response.status_code,
                            masked_to,
                            attempt,
                            self.max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Exhausted retries on 429/5xx
                    logger.error(
                        "WhatsApp API retries exhausted | status={} to={} error={}",
                        response.status_code,
                        masked_to,
                        mapped_exc,
                    )
                    raise mapped_exc

                # Non-retryable 4xx client errors (400, 401, 403, 404, 422, etc.)
                resp_data = None
                try:
                    resp_data = response.json()
                except Exception:
                    pass

                client_exc = map_meta_error(
                    response.status_code,
                    resp_data,
                    default_message=f"HTTP {response.status_code} client error from WhatsApp API",
                )
                logger.error(
                    "WhatsApp API client error (non-retryable) | status={} to={} code={} error={}",
                    response.status_code,
                    masked_to,
                    client_exc.error_code,
                    client_exc.message,
                )
                raise client_exc

            except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                last_error = net_err
                if attempt <= self.max_retries:
                    delay = min(
                        self.max_delay,
                        self.base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25 * self.base_delay),
                    )
                    logger.warning(
                        "WhatsApp API network error | to={} error={} attempt={}/{} retrying_in={:.2f}s",
                        masked_to,
                        type(net_err).__name__,
                        attempt,
                        self.max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.error(
                    "WhatsApp API network timeout exhausted retries | to={} error={}",
                    masked_to,
                    net_err,
                )
                raise WhatsAppTransientError(
                    f"WhatsApp API request timed out or network error after {self.max_retries} retries: {net_err}"
                ) from net_err

        # Fallback if loop ends without explicit return/raise
        if last_error:
            if isinstance(last_error, WhatsAppError):
                raise last_error
            raise WhatsAppTransientError(str(last_error)) from last_error
        raise WhatsAppTransientError("Request failed after retries.")

    async def _post_messages_endpoint(
        self,
        payload: dict[str, Any],
        log_context: dict[str, Any],
    ) -> str:
        """Post to the /messages endpoint and extract the returned wamid."""
        if not self.phone_number_id:
            raise WhatsAppClientError("WHATSAPP_PHONE_NUMBER_ID is not configured.")

        url = f"{self.base_url}/messages"
        response_data = await self._execute_with_retry("POST", url, payload, log_context)

        # Extract wamid from Meta standard response: {"messages": [{"id": "wamid.HBg..."}]}
        messages = response_data.get("messages")
        if isinstance(messages, list) and len(messages) > 0:
            wamid = messages[0].get("id")
            if wamid:
                logger.info(
                    "WhatsApp message dispatched | wamid={} to={} type={}",
                    wamid,
                    _mask_phone(str(log_context.get("to", ""))),
                    log_context.get("type", ""),
                )
                return str(wamid)

        # In case Meta response lacks messages array
        logger.warning("WhatsApp API response missing wamid in messages array: {}", response_data)
        return ""

    async def send_template(
        self,
        to: str,
        template_key: str,
        params: dict[str, Any] | None = None,
        language: str = "en",
    ) -> str:
        """Send a registered WhatsApp template message.

        Args:
            to: Recipient phone number (local or international format; will be normalized).
            template_key: Internal business key registered in TemplateRegistry.
            params: Dictionary of parameters required by the template body/header.
            language: Desired language code (e.g. 'en', 'ur'). Falls back to 'en' if not found.

        Returns:
            The WhatsApp message ID (wamid).
        """
        normalized_to = normalize_phone_number(to)
        template_name, lang_code, components = self.registry.format_components(
            template_key,
            params=params,
            language=language,
        )

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": lang_code},
                "components": components,
            },
        }

        log_context = {
            "type": "template",
            "key": template_key,
            "template_name": template_name,
            "to": normalized_to,
        }
        return await self._post_messages_endpoint(payload, log_context)

    async def send_text(
        self,
        to: str,
        text: str,
        preview_url: bool = False,
    ) -> str:
        """Send a free-form text message (valid within 24h window).

        Args:
            to: Recipient phone number.
            text: Text body content.
            preview_url: Whether WhatsApp should generate a web preview for URLs in text.

        Returns:
            The WhatsApp message ID (wamid).
        """
        normalized_to = normalize_phone_number(to)
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_to,
            "type": "text",
            "text": {
                "body": text,
                "preview_url": preview_url,
            },
        }

        log_context = {
            "type": "text",
            "to": normalized_to,
            "preview_url": preview_url,
        }
        return await self._post_messages_endpoint(payload, log_context)

    async def send_interactive(
        self,
        to: str,
        interactive: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        body_text: str | None = None,
        interactive_type: str = "button",
        header: dict[str, Any] | None = None,
        footer_text: str | None = None,
    ) -> str:
        """Send an interactive message (e.g. quick reply buttons or list selection).

        Can accept either a pre-constructed `interactive` dictionary, or discrete helper arguments.

        Returns:
            The WhatsApp message ID (wamid).
        """
        normalized_to = normalize_phone_number(to)

        if interactive is not None:
            interactive_payload = interactive
            resolved_type = interactive.get("type", interactive_type)
        else:
            interactive_payload = {
                "type": interactive_type,
                "body": {"text": body_text or ""},
                "action": action or {},
            }
            if header:
                interactive_payload["header"] = header
            if footer_text:
                interactive_payload["footer"] = {"text": footer_text}
            resolved_type = interactive_type

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_to,
            "type": "interactive",
            "interactive": interactive_payload,
        }

        log_context = {
            "type": "interactive",
            "sub_type": resolved_type,
            "to": normalized_to,
        }
        return await self._post_messages_endpoint(payload, log_context)

    async def mark_as_read(self, message_id: str) -> bool:
        """Mark an incoming WhatsApp message as read.

        Args:
            message_id: The incoming wamid to mark read.

        Returns:
            True if Meta accepted the request.
        """
        if not self.phone_number_id:
            raise WhatsAppClientError("WHATSAPP_PHONE_NUMBER_ID is not configured.")

        url = f"{self.base_url}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }

        log_context = {
            "type": "mark_read",
            "message_id": message_id,
        }
        response_data = await self._execute_with_retry("POST", url, payload, log_context)
        return bool(response_data.get("success", True))
