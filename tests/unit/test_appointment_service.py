"""
Unit tests for appointment service slot generation.
"""
from datetime import date, time, timedelta, timezone, datetime
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.models.appointment import AppointmentStatus
from app.models.doctor import DayOfWeek, DoctorAvailability
from app.services.appointment_service import AppointmentService


@pytest.mark.asyncio
async def test_slot_generation_intervals():
    # Mock DB session
    mock_db = AsyncMock()

    # Create mock availability window: 09:00 to 11:00 (4 x 30-min slots)
    avail = DoctorAvailability(
        id=uuid.uuid4(),
        doctor_id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        day_of_week=DayOfWeek.monday,
        start_time=time(9, 0),
        end_time=time(11, 0),
        is_active=True,
    )

    # Mock DB returns this availability window
    mock_avail_res = MagicMock()
    mock_avail_res.scalars.return_value.all.return_value = [avail]

    # Mock DB returns 1 booked appointment at 09:30
    booked_slot = datetime(2026, 9, 7, 9, 30, tzinfo=timezone.utc)
    mock_booked_res = MagicMock()
    mock_booked_res.all.return_value = [(booked_slot,)]

    mock_db.execute.side_effect = [mock_avail_res, mock_booked_res]

    service = AppointmentService(mock_db)
    slots = await service.get_available_slots(
        doctor_id=avail.doctor_id,
        branch_id=avail.branch_id,
        target_date=date(2026, 9, 7),
    )

    # 4 intervals: 09:00, 09:30, 10:00, 10:30
    assert len(slots) == 4
    # 09:00 is free
    assert slots[0].is_available is True
    # 09:30 was booked -> False
    assert slots[1].is_available is False
    # 10:00 is free
    assert slots[2].is_available is True
    # 10:30 is free
    assert slots[3].is_available is True
