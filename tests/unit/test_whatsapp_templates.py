"""Unit tests for WhatsApp TemplateRegistry and parameter formatting."""
import pytest

from app.services.whatsapp.exceptions import WhatsAppTemplateError
from app.services.whatsapp.templates import TemplateConfig, TemplateRegistry


@pytest.fixture
def registry():
    return TemplateRegistry()


def test_registry_contains_default_templates(registry: TemplateRegistry):
    expected_keys = [
        "appointment_confirmed",
        "appointment_reminder_1h",
        "order_status",
        "lab_report_ready",
    ]
    for key in expected_keys:
        assert registry.has(key) is True
        tmpl = registry.get(key)
        assert isinstance(tmpl, TemplateConfig)
        assert tmpl.template_name


def test_registry_unknown_template_raises(registry: TemplateRegistry):
    with pytest.raises(WhatsAppTemplateError) as exc_info:
        registry.get("non_existent_key")
    assert "not found in template registry" in str(exc_info.value)


def test_format_components_appointment_confirmed(registry: TemplateRegistry):
    params = {
        "patient_name": "Ali Khan",
        "doctor_name": "Dr. Fatima",
        "slot_datetime": "15 Oct 2026, 11:00 AM PKT",
        "clinic_branch": "Clifton Branch",
    }
    name, lang, components = registry.format_components("appointment_confirmed", params, language="en")
    assert name == "appointment_confirmed"
    assert lang == "en"
    assert len(components) == 1
    assert components[0]["type"] == "body"
    assert len(components[0]["parameters"]) == 4
    assert components[0]["parameters"][0]["text"] == "Ali Khan"
    assert components[0]["parameters"][1]["text"] == "Dr. Fatima"


def test_format_components_urdu_language(registry: TemplateRegistry):
    params = {
        "patient_name": "علی خان",
        "doctor_name": "ڈاکٹر فاطمہ",
        "slot_datetime": "15 اکتوبر 2026",
        "clinic_branch": "کلفٹن برانچ",
    }
    name, lang, components = registry.format_components("appointment_confirmed", params, language="ur")
    assert name == "appointment_confirmed"
    assert lang == "ur"
    assert components[0]["parameters"][0]["text"] == "علی خان"


def test_language_fallback_to_english_for_unsupported_language(registry: TemplateRegistry):
    params = {
        "customer_name": "Sara Ahmed",
        "order_number": "ORD-12345",
        "status": "Out for Delivery",
        "total_amount": "Rs. 1,450",
    }
    # Request unsupported language (e.g. Spanish 'es')
    name, lang, components = registry.format_components("order_status", params, language="es")
    assert name == "order_status_update"
    assert lang == "en"  # fell back to 'en'
    assert len(components[0]["parameters"]) == 4


def test_missing_parameter_raises_template_error(registry: TemplateRegistry):
    incomplete_params = {
        "patient_name": "Ali Khan",
        "doctor_name": "Dr. Fatima",
        # missing slot_datetime and clinic_branch
    }
    with pytest.raises(WhatsAppTemplateError) as exc_info:
        registry.format_components("appointment_confirmed", incomplete_params)
    assert "Missing required parameter" in str(exc_info.value)
    assert "slot_datetime" in str(exc_info.value)
    assert "clinic_branch" in str(exc_info.value)


def test_custom_dict_registry():
    custom = {
        "custom_alert": {
            "template_name": "meta_custom_alert",
            "languages": {"en": "en_US"},
            "header_params": ["headline"],
            "body_params": ["message_body"],
        }
    }
    reg = TemplateRegistry(custom_dict=custom)
    assert reg.has("custom_alert")
    assert not reg.has("appointment_confirmed")

    name, lang, components = reg.format_components(
        "custom_alert",
        {"headline": "Notice", "message_body": "Clinic is closed today."},
    )
    assert name == "meta_custom_alert"
    assert lang == "en_US"
    assert len(components) == 2
    assert components[0]["type"] == "header"
    assert components[1]["type"] == "body"
