"""
Unit tests for review summary calculations.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.models.review import ReviewTargetType
from app.services.review_service import ReviewService


@pytest.mark.asyncio
async def test_review_summary_average_and_distribution():
    mock_db = AsyncMock()

    # Suppose ratings in DB: 2x 5-star, 1x 4-star, 1x 3-star
    # Total = 4 reviews, sum = 10 + 4 + 3 = 17, avg = 17/4 = 4.25 -> 4.2 rounded
    mock_res = MagicMock()
    mock_res.all.return_value = [
        (5, 2),
        (4, 1),
        (3, 1),
    ]
    mock_db.execute.return_value = mock_res

    service = ReviewService(mock_db)
    summary = await service.get_summary(
        target_type=ReviewTargetType.doctor,
        target_id=uuid.uuid4(),
    )

    assert summary.total_count == 4
    assert summary.avg_rating == 4.2
    assert summary.distribution[5] == 2
    assert summary.distribution[4] == 1
    assert summary.distribution[3] == 1
    assert summary.distribution[2] == 0
    assert summary.distribution[1] == 0
