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
