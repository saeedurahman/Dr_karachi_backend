"""Unit tests for Pakistani phone number normalization and validation."""
import pytest

from app.utils.phone import InvalidPhoneNumberError, is_valid_phone, normalize_phone_number


class TestPhoneNormalization:
    @pytest.mark.parametrize(
        ("input_phone", "expected"),
        [
            ("03001234567", "923001234567"),
            ("+923001234567", "923001234567"),
            ("923001234567", "923001234567"),
            ("0300-1234567", "923001234567"),
            ("+92 300 1234567", "923001234567"),
            ("+92-300-1234567", "923001234567"),
            ("0300 123 4567", "923001234567"),
            (" 03001234567 ", "923001234567"),
            ("00923001234567", "923001234567"),
            ("(0300) 1234567", "923001234567"),
            ("0345.1234567", "923451234567"),
            # Different mobile prefixes (0311, 0321, 0333, 0345, 0355, etc. - no prefix restriction)
            ("03129876543", "923129876543"),
            ("+923331112233", "923331112233"),
            ("03701234567", "923701234567"),
        ],
    )
    def test_normalize_valid_phone_numbers(self, input_phone: str, expected: str):
        result = normalize_phone_number(input_phone)
        assert result == expected
        assert is_valid_phone(input_phone) is True

    @pytest.mark.parametrize(
        "invalid_phone",
        [
            "",
            "   ",
            None,
            123456789,
            "12345",
            "030012345",         # too short (10 digits after norm)
            "030012345678",       # too long (13 digits after norm)
            "9230012345678",      # 13 digits starting with 92
            "9230012345",         # 10 digits starting with 92
            "0300123456a",        # non-digit character
            "+12025550123",       # US number (+1)
            "447911123456",       # UK number (44)
            "913001234567",       # Starts with 91, not 92
            "00913001234567",     # India country code
            "+92 300 12#4567",    # invalid symbol
        ],
    )
    def test_normalize_invalid_phone_numbers_raise_error(self, invalid_phone):
        with pytest.raises(InvalidPhoneNumberError):
            normalize_phone_number(invalid_phone)  # type: ignore[arg-type]
        assert is_valid_phone(invalid_phone) is False  # type: ignore[arg-type]
