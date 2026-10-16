"""WhatsApp template registry and parameter mapping engine.

Provides config-driven template definitions mapping internal business keys
(e.g. appointment_confirmed, appointment_reminder_1h) to Meta registered template names,
supported language codes (en, ur with fallback to en), and ordered parameter names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.services.whatsapp.exceptions import WhatsAppTemplateError

# Fallback definitions in case the YAML file is absent
DEFAULT_TEMPLATES_DICT: dict[str, dict[str, Any]] = {
    "appointment_confirmed": {
        "template_name": "appointment_confirmed",
        "languages": {"en": "en", "ur": "ur"},
        "body_params": ["patient_name", "doctor_name", "slot_datetime", "clinic_branch"],
    },
    "appointment_reminder_1h": {
        "template_name": "appointment_reminder_1h",
        "languages": {"en": "en", "ur": "ur"},
        "body_params": ["patient_name", "doctor_name", "slot_datetime", "clinic_branch"],
    },
    "appointment_cancelled": {
        "template_name": "appointment_cancelled",
        "languages": {"en": "en", "ur": "ur"},
        "body_params": ["patient_name", "doctor_name", "slot_datetime", "reason"],
    },
    "order_status": {
        "template_name": "order_status_update",
        "languages": {"en": "en", "ur": "ur"},
        "body_params": ["customer_name", "order_number", "status", "total_amount"],
    },
    "order_confirmed": {
        "template_name": "order_confirmed",
        "languages": {"en": "en", "ur": "ur"},
        "body_params": ["customer_name", "order_number", "total_amount"],
    },
    "lab_report_ready": {
        "template_name": "lab_report_ready",
        "category": "transactional",
        "languages": {"en": "en", "ur": "ur"},
        "body_params": ["patient_name", "branch_name", "report_url"],
    },
    "promotional_offer": {
        "template_name": "promotional_offer",
        "category": "marketing",
        "opt_out_hint": "Reply STOP to unsubscribe",
        "languages": {"en": "en", "ur": "ur"},
        "body_params": ["customer_name", "offer_title", "discount_details", "expiry_date"],
    },
}


@dataclass(frozen=True)
class TemplateConfig:
    """Configuration for an individual WhatsApp message template."""

    key: str
    template_name: str
    category: str = "transactional"  # "transactional" | "marketing"
    opt_out_hint: str | None = None
    languages: dict[str, str] = field(default_factory=lambda: {"en": "en"})
    body_params: list[str] = field(default_factory=list)
    header_params: list[str] = field(default_factory=list)
    default_language: str = "en"

    def get_language_code(self, lang: str | None = None) -> str:
        """Resolve language code. If requested language is not supported, falls back to 'en'."""
        if lang and lang in self.languages:
            return self.languages[lang]
        # Missing language falls back to default ('en')
        return self.languages.get(self.default_language, "en")


class TemplateRegistry:
    """Config-driven WhatsApp message template registry."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        custom_dict: dict[str, Any] | None = None,
    ) -> None:
        self._templates: dict[str, TemplateConfig] = {}
        self._load(config_path, custom_dict)

    def _load(
        self,
        config_path: str | Path | None = None,
        custom_dict: dict[str, Any] | None = None,
    ) -> None:
        source_data: dict[str, Any] = {}

        if custom_dict is not None:
            source_data = custom_dict
        else:
            path = (
                Path(config_path)
                if config_path
                else Path(__file__).parent / "templates.yaml"
            )
            if path.is_file():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        loaded = yaml.safe_load(f)
                        if isinstance(loaded, dict):
                            source_data = loaded
                except Exception:
                    source_data = DEFAULT_TEMPLATES_DICT
            else:
                source_data = DEFAULT_TEMPLATES_DICT

        for key, entry in source_data.items():
            if not isinstance(entry, dict):
                continue
            template_name = entry.get("template_name", key)
            category = entry.get("category", "transactional")
            opt_out_hint = entry.get("opt_out_hint") or entry.get("note")
            languages = entry.get("languages", {"en": "en"})
            body_params = entry.get("body_params", [])
            header_params = entry.get("header_params", [])
            default_language = entry.get("default_language", "en")

            self._templates[key] = TemplateConfig(
                key=key,
                template_name=template_name,
                category=category,
                opt_out_hint=opt_out_hint,
                languages=languages,
                body_params=body_params,
                header_params=header_params,
                default_language=default_language,
            )

    def has(self, key: str) -> bool:
        """Check if a template key is registered."""
        return key in self._templates

    def get(self, key: str) -> TemplateConfig:
        """Get template configuration by internal business key.

        Raises:
            WhatsAppTemplateError: If the template key is not found.
        """
        if key not in self._templates:
            raise WhatsAppTemplateError(
                f"WhatsApp template key '{key}' not found in template registry."
            )
        return self._templates[key]

    def format_components(
        self,
        key: str,
        params: dict[str, Any] | None = None,
        language: str = "en",
    ) -> tuple[str, str, list[dict[str, Any]]]:
        """Format Meta Graph API template components for sending.

        Returns:
            tuple of (template_name, language_code, components_list)

        Raises:
            WhatsAppTemplateError: If key is unknown or required parameters are missing.
        """
        template = self.get(key)
        lang_code = template.get_language_code(language)
        param_dict = params or {}

        # Validate body parameters
        missing_body = [p for p in template.body_params if p not in param_dict]
        if missing_body:
            raise WhatsAppTemplateError(
                f"Missing required parameter(s) for WhatsApp template '{key}': "
                f"{', '.join(missing_body)}"
            )

        components: list[dict[str, Any]] = []

        # Header component (if defined)
        if template.header_params:
            missing_header = [p for p in template.header_params if p not in param_dict]
            if missing_header:
                raise WhatsAppTemplateError(
                    f"Missing required header parameter(s) for WhatsApp template '{key}': "
                    f"{', '.join(missing_header)}"
                )
            components.append({
                "type": "header",
                "parameters": [
                    {"type": "text", "text": str(param_dict[p])}
                    for p in template.header_params
                ],
            })

        # Body component
        if template.body_params:
            components.append({
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(param_dict[p])}
                    for p in template.body_params
                ],
            })

        return template.template_name, lang_code, components


# Default module-level registry singleton
template_registry = TemplateRegistry()
