"""Unit tests for Schluter coordinator."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.schluterditraheat import SchluterDataUpdateCoordinator
from custom_components.schluterditraheat.api import (
    SchluterAuthenticationError,
    SchluterConnectionError,
    SchluterRateLimitError,
)
from custom_components.schluterditraheat.const import (
    RATE_LIMIT_INITIAL_BACKOFF,
    RATE_LIMIT_MAX_BACKOFF,
    SCAN_INTERVAL,
    STATIC_REFRESH_INTERVAL_POLLS,
)

# Import the stub exceptions wired in conftest.py.
from homeassistant.exceptions import ConfigEntryAuthFailed as _ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed as _UpdateFailed


MOCK_STATIC_DATA = {
    40001: {
        "device_id": 40001,
        "identifier": "aa11bb22cc33dd44",
        "name": "DITRA-HEAT-E-RS1",
        "location_id": 30001,
        "location_name": "Test Home",
        "group_id": 50001,
        "group_name": "Master Bath",
        "sku": "?",
        "vendor": "Schluter",
    },
}

MOCK_DYNAMIC_DATA = {
    40001: {
        "current_temperature": 23.33,
        "target_temperature": 23.33,
        "mode": "auto",
        "heating_percent": 0,
        "air_floor_mode": "floor",
        "gfci_status": "ok",
    },
}


@pytest.fixture
def mock_api():
    """Fixture for a mocked SchluterApi."""
    api = MagicMock()
    api.get_static_data = AsyncMock(return_value=MOCK_STATIC_DATA)
    api.get_device_attributes_bulk = AsyncMock(return_value=MOCK_DYNAMIC_DATA)
    # A real client has no rate-limit reading until the first response.
    api.rate_limit = None
    return api


@pytest.fixture
def coordinator(mock_api):
    """Fixture for a coordinator with mocked api and hass."""
    hass = MagicMock()
    return SchluterDataUpdateCoordinator(hass, mock_api)


class TestStaticDataCaching:
    """Test static data caching behavior."""

    async def test_first_poll_fetches_static_data(self, coordinator, mock_api):
        """Test that the first poll fetches static data."""
        assert coordinator._static_data is None

        await coordinator._async_update_data()

        mock_api.get_static_data.assert_called_once()
        assert coordinator._static_data == MOCK_STATIC_DATA

    async def test_normal_poll_skips_static_data(self, coordinator, mock_api):
        """Test that subsequent polls skip static data fetch."""
        # First poll — fetches static
        await coordinator._async_update_data()
        mock_api.get_static_data.reset_mock()
        mock_api.get_device_attributes_bulk.reset_mock()

        # Second poll — should NOT fetch static
        await coordinator._async_update_data()

        mock_api.get_static_data.assert_not_called()
        mock_api.get_device_attributes_bulk.assert_called_once_with([40001])

    async def test_static_refresh_after_interval(self, coordinator, mock_api):
        """Test that static data is refreshed after STATIC_REFRESH_INTERVAL_POLLS."""
        # First poll
        await coordinator._async_update_data()
        mock_api.get_static_data.reset_mock()

        # Simulate polls until refresh is needed
        coordinator._polls_since_static_refresh = STATIC_REFRESH_INTERVAL_POLLS

        await coordinator._async_update_data()

        mock_api.get_static_data.assert_called_once()
        assert coordinator._polls_since_static_refresh == 1

    async def test_static_refresh_failure_retries(self, coordinator, mock_api):
        """Test that a failed static refresh retries on the next poll."""
        mock_api.get_static_data.side_effect = SchluterConnectionError("timeout")

        with pytest.raises(_UpdateFailed):
            await coordinator._async_update_data()

        # Static data still None — next poll should retry
        assert coordinator._static_data is None

        # Fix the API and retry
        mock_api.get_static_data.side_effect = None
        mock_api.get_static_data.return_value = MOCK_STATIC_DATA

        result = await coordinator._async_update_data()

        assert coordinator._static_data == MOCK_STATIC_DATA
        assert 40001 in result


class TestRateLimitBackoff:
    """Test rate limit backoff behavior."""

    async def test_initial_backoff_on_429(self, coordinator, mock_api):
        """Test that first 429 sets interval to initial backoff."""
        # First poll succeeds (populates static data)
        await coordinator._async_update_data()

        # Next poll hits rate limit
        mock_api.get_device_attributes_bulk.side_effect = SchluterRateLimitError("429")

        with pytest.raises(_UpdateFailed):
            await coordinator._async_update_data()

        assert coordinator.update_interval == RATE_LIMIT_INITIAL_BACKOFF

    async def test_exponential_backoff(self, coordinator, mock_api):
        """Test that consecutive 429s double the interval."""
        await coordinator._async_update_data()
        mock_api.get_device_attributes_bulk.side_effect = SchluterRateLimitError("429")

        # First 429 → 2 min
        with pytest.raises(_UpdateFailed):
            await coordinator._async_update_data()
        assert coordinator.update_interval == timedelta(minutes=2)

        # Second 429 → 4 min
        with pytest.raises(_UpdateFailed):
            await coordinator._async_update_data()
        assert coordinator.update_interval == timedelta(minutes=4)

        # Third 429 → 8 min
        with pytest.raises(_UpdateFailed):
            await coordinator._async_update_data()
        assert coordinator.update_interval == timedelta(minutes=8)

    async def test_backoff_capped_at_max(self, coordinator, mock_api):
        """Test that backoff doesn't exceed RATE_LIMIT_MAX_BACKOFF."""
        await coordinator._async_update_data()
        mock_api.get_device_attributes_bulk.side_effect = SchluterRateLimitError("429")

        # Hit rate limit many times
        for _ in range(10):
            with pytest.raises(_UpdateFailed):
                await coordinator._async_update_data()

        assert coordinator.update_interval == RATE_LIMIT_MAX_BACKOFF

    async def test_backoff_reset_on_success(self, coordinator, mock_api):
        """Test that successful poll restores normal interval."""
        await coordinator._async_update_data()

        # Trigger backoff
        mock_api.get_device_attributes_bulk.side_effect = SchluterRateLimitError("429")
        with pytest.raises(_UpdateFailed):
            await coordinator._async_update_data()
        assert coordinator.update_interval == RATE_LIMIT_INITIAL_BACKOFF

        # Successful poll clears backoff
        mock_api.get_device_attributes_bulk.side_effect = None
        mock_api.get_device_attributes_bulk.return_value = MOCK_DYNAMIC_DATA
        await coordinator._async_update_data()

        assert coordinator.update_interval == SCAN_INTERVAL
        assert coordinator._backoff_interval is None


class TestDataMerge:
    """Test that merged data has the correct shape."""

    async def test_merged_data_shape(self, coordinator, mock_api):
        """Test that returned data has both static and dynamic keys."""
        result = await coordinator._async_update_data()

        assert 40001 in result
        t = result[40001]

        # Static fields
        assert t["device_id"] == 40001
        assert t["identifier"] == "aa11bb22cc33dd44"
        assert t["name"] == "DITRA-HEAT-E-RS1"
        assert t["location_name"] == "Test Home"
        assert t["group_name"] == "Master Bath"

        # Dynamic fields
        assert t["current_temperature"] == 23.33
        assert t["target_temperature"] == 23.33
        assert t["mode"] == "auto"
        assert t["heating_percent"] == 0
        assert t["air_floor_mode"] == "floor"
        assert t["gfci_status"] == "ok"

    async def test_device_missing_from_dynamic_excluded(self, coordinator, mock_api):
        """Test that a device missing from dynamic data is excluded."""
        mock_api.get_device_attributes_bulk.return_value = {}  # no dynamic data

        result = await coordinator._async_update_data()

        assert 40001 not in result

    async def test_auth_error_raises_config_entry_auth_failed(
        self, coordinator, mock_api
    ):
        """Test that auth errors trigger ConfigEntryAuthFailed."""
        mock_api.get_static_data.side_effect = SchluterAuthenticationError("bad creds")

        with pytest.raises(_ConfigEntryAuthFailed):
            await coordinator._async_update_data()


class TestBudgetThrottle:
    """Test the proactive rate-limit budget throttle."""

    async def test_defers_when_budget_low(self, coordinator, mock_api):
        """Test the poll interval is deferred toward reset when budget is low."""
        import time as _time
        from datetime import timedelta

        from custom_components.schluterditraheat.api import RateLimit

        # Remaining at the floor, with a reset further out than the normal poll.
        mock_api.rate_limit = RateLimit(
            limit=100, remaining=1, reset=600.0, captured_at=_time.time()
        )

        await coordinator._async_update_data()

        # Deferred toward the reset window (a hair under 600s due to elapsed time),
        # and well beyond the normal scan interval.
        deferred = coordinator.update_interval.total_seconds()
        assert 590 < deferred <= 600
        assert coordinator.update_interval > SCAN_INTERVAL

    async def test_normal_interval_when_budget_healthy(self, coordinator, mock_api):
        """Test a healthy budget keeps the normal scan interval."""
        import time as _time

        from custom_components.schluterditraheat.api import RateLimit

        mock_api.rate_limit = RateLimit(
            limit=100, remaining=90, reset=30.0, captured_at=_time.time()
        )

        await coordinator._async_update_data()

        assert coordinator.update_interval == SCAN_INTERVAL

    async def test_no_throttle_without_reading(self, coordinator, mock_api):
        """Test the normal interval is kept when there is no rate-limit reading."""
        # mock_api.rate_limit defaults to None (no response seen yet).
        await coordinator._async_update_data()

        assert coordinator.update_interval == SCAN_INTERVAL


class TestDailyLimit:
    """Test the daily request cap handling."""

    async def test_daily_limit_pauses_until_midnight(self, coordinator, mock_api):
        """Test ACCDAYREQMAX pauses polling until the next local midnight."""
        from datetime import timedelta

        from custom_components.schluterditraheat.api import SchluterDailyLimitError

        await coordinator._async_update_data()  # seed static data

        mock_api.get_device_attributes_bulk.side_effect = SchluterDailyLimitError(
            "daily cap"
        )

        with patch(
            "custom_components.schluterditraheat._seconds_until_local_midnight",
            return_value=3600.0,
        ):
            with pytest.raises(_UpdateFailed):
                await coordinator._async_update_data()

        assert coordinator.update_interval == timedelta(seconds=3600)


class TestDailyLimitRecovery:
    """Test the daily-cap pause is bounded and self-heals."""

    async def test_pause_capped_at_max(self, coordinator, mock_api):
        """Test the daily pause is capped at DAILY_LIMIT_MAX_PAUSE."""
        from custom_components.schluterditraheat.api import SchluterDailyLimitError
        from custom_components.schluterditraheat.const import DAILY_LIMIT_MAX_PAUSE

        await coordinator._async_update_data()
        mock_api.get_device_attributes_bulk.side_effect = SchluterDailyLimitError(
            "cap"
        )

        # Midnight is far away (10h); pause must be capped to the max.
        with patch(
            "custom_components.schluterditraheat._seconds_until_local_midnight",
            return_value=36000.0,
        ):
            with pytest.raises(_UpdateFailed):
                await coordinator._async_update_data()

        assert coordinator.update_interval == DAILY_LIMIT_MAX_PAUSE
        assert coordinator._backoff_interval == DAILY_LIMIT_MAX_PAUSE

    async def test_success_after_daily_pause_restores_interval(
        self, coordinator, mock_api
    ):
        """Test a successful poll after a daily pause restores the scan interval."""
        from custom_components.schluterditraheat.api import SchluterDailyLimitError

        await coordinator._async_update_data()
        mock_api.get_device_attributes_bulk.side_effect = SchluterDailyLimitError(
            "cap"
        )
        with patch(
            "custom_components.schluterditraheat._seconds_until_local_midnight",
            return_value=1800.0,
        ):
            with pytest.raises(_UpdateFailed):
                await coordinator._async_update_data()

        # Cap clears — next poll succeeds and restores the normal interval.
        mock_api.get_device_attributes_bulk.side_effect = None
        mock_api.get_device_attributes_bulk.return_value = MOCK_DYNAMIC_DATA
        await coordinator._async_update_data()

        assert coordinator.update_interval == SCAN_INTERVAL
        assert coordinator._backoff_interval is None


class TestDailyLimitSharedPause:
    """The daily cap must pause every API caller, not just the poll loop.

    The energy import runs on its own timer (async_track_time_interval), so
    without a shared flag it would keep calling the API hourly while the
    coordinator sits paused on ACCDAYREQMAX.
    """

    async def test_flag_set_on_daily_limit(self, coordinator, mock_api):
        """Test a daily-cap hit raises the shared flag."""
        from custom_components.schluterditraheat.api import SchluterDailyLimitError

        await coordinator._async_update_data()
        assert coordinator.daily_limit_reached is False

        mock_api.get_device_attributes_bulk.side_effect = SchluterDailyLimitError(
            "cap"
        )
        with patch(
            "custom_components.schluterditraheat._seconds_until_local_midnight",
            return_value=3600.0,
        ):
            with pytest.raises(_UpdateFailed):
                await coordinator._async_update_data()

        assert coordinator.daily_limit_reached is True

    async def test_flag_cleared_by_successful_poll(self, coordinator, mock_api):
        """Test a later successful poll clears the flag and resumes energy."""
        from custom_components.schluterditraheat.api import SchluterDailyLimitError

        await coordinator._async_update_data()
        mock_api.get_device_attributes_bulk.side_effect = SchluterDailyLimitError(
            "cap"
        )
        with patch(
            "custom_components.schluterditraheat._seconds_until_local_midnight",
            return_value=3600.0,
        ):
            with pytest.raises(_UpdateFailed):
                await coordinator._async_update_data()
        assert coordinator.daily_limit_reached is True

        # Cap lifted: next poll succeeds.
        mock_api.get_device_attributes_bulk.side_effect = None
        mock_api.get_device_attributes_bulk.return_value = MOCK_DYNAMIC_DATA
        await coordinator._async_update_data()

        assert coordinator.daily_limit_reached is False
        assert coordinator.update_interval == SCAN_INTERVAL

    async def test_note_daily_limit_pauses_polling(self, coordinator, mock_api):
        """Test the energy import can trip the pause for the whole entry."""
        await coordinator._async_update_data()
        assert coordinator.daily_limit_reached is False

        # Simulate the energy import hitting the cap first.
        with patch(
            "custom_components.schluterditraheat._seconds_until_local_midnight",
            return_value=1800.0,
        ):
            seconds = coordinator.note_daily_limit()

        assert seconds == 1800.0
        assert coordinator.daily_limit_reached is True
        assert coordinator.update_interval == timedelta(seconds=1800)

    async def test_note_daily_limit_capped(self, coordinator, mock_api):
        """Test the energy-triggered pause is capped like the poll path."""
        from custom_components.schluterditraheat.const import DAILY_LIMIT_MAX_PAUSE

        await coordinator._async_update_data()
        with patch(
            "custom_components.schluterditraheat._seconds_until_local_midnight",
            return_value=36000.0,
        ):
            coordinator.note_daily_limit()

        assert coordinator.update_interval == DAILY_LIMIT_MAX_PAUSE
