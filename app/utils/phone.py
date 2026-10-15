"""Phone normalization and validation utilities for Pakistani numbers.

Converts local and international variations of Pakistani phone numbers into
Meta-compatible E.164 digits without the plus sign (e.g. 923001234567).
"""
from __future__ import annotations

import re


class InvalidPhoneNumberError(ValueError):
    """Raised when a phone number cannot be normalized to a valid Pakistani E.164 number."""
    pass


def normalize_phone_number(raw_phone: str | None) -> str:
    """Normalize a Pakistani phone number to 12 digits starting with '92'.

    Accepts formats such as:
    - 03001234567
    - +923001234567
    - 923001234567
    - 0300-1234567
    - +92 300 1234567
    - 00923001234567

    Validation is strictly by length (12 digits starting with '92' after normalization).
    Specific network prefixes are deliberately NOT validated to support any valid Pakistani number.

    Raises:
        InvalidPhoneNumberError: If the phone number is empty, contains invalid characters,
            or fails length/prefix criteria.
    """
    if not isinstance(raw_phone, str) or not raw_phone.strip():
        raise InvalidPhoneNumberError("Phone number must be a non-empty string.")

    # Remove all whitespace, hyphens, dots, and parentheses
    cleaned = re.sub(r"[\s\-\.\(\)]+", "", raw_phone.strip())

    # Handle international prefixes
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    elif cleaned.startswith("0092"):
        cleaned = "92" + cleaned[4:]
    elif cleaned.startswith("0"):
        cleaned = "92" + cleaned[1:]

    # Check for non-digit characters
    if not cleaned.isdigit():
        raise InvalidPhoneNumberError(
            f"Phone number '{raw_phone}' contains invalid non-digit characters."
        )

    # Validate length and Pakistani country code (92)
    if len(cleaned) != 12 or not cleaned.startswith("92"):
        raise InvalidPhoneNumberError(
            f"Invalid Pakistani phone number: '{raw_phone}'. "
            f"Expected 12 digits starting with '92' after normalization (got '{cleaned}')."
        )

    return cleaned


def is_valid_phone(raw_phone: str | None) -> bool:
    """Return True if the raw phone number can be normalized, False otherwise."""
    try:
        normalize_phone_number(raw_phone)
        return True
    except InvalidPhoneNumberError:
        return False
