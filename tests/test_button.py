"""Unit tests for the Schluter refresh button."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.schluterditraheat.button import SchluterRefreshButton


MOCK_THERMOSTAT = {
    "device_id": 40001,
    "identifier": "aa11bb22cc33dd44",
    "name": "DITRA-HEAT-E-RS1",
    "group_name": "Master Bath",
    "vendor": "Schluter",
    "sku": "?",
    "current_temperature": 23.33,
    "target_temperature": 23.33,
    "mode": "auto",
    "heating_percent": 42,
    "air_floor_mode": "floor",
    "gfci_status": "ok",
    "load_watt": 264,
}


@pytest.fixture
def coordinator():
    """Fixture for a mocked coordinator with one thermostat."""
    coord = MagicMock()
    coord.data = {40001: dict(MOCK_THERMOSTAT)}
    coord.async_request_refresh = AsyncMock()
    return coord


class TestRefreshButton:
    """Test the manual refresh button."""

    def test_unique_id_and_device_info(self, coordinator):
        """Test the button binds to the thermostat's device."""
        button = SchluterRefreshButton(coordinator, 40001)

        assert button._attr_unique_id == "aa11bb22cc33dd44_refresh"
        assert button._attr_device_info["identifiers"] == {
            ("schluterditraheat", "aa11bb22cc33dd44")
        }
        assert button._attr_name == "Refresh"

    async def test_press_requests_refresh(self, coordinator):
        """Test pressing the button triggers a coordinator refresh."""
        button = SchluterRefreshButton(coordinator, 40001)

        await button.async_press()

        coordinator.async_request_refresh.assert_awaited_once()

    async def test_press_uses_debounced_refresh(self, coordinator):
        """Test the button does not bypass the coordinator's debouncer.

        async_refresh() would fire an immediate API call on every press;
        async_request_refresh() coalesces rapid presses. Guard against a
        well-meaning swap to the un-debounced call.
        """
        button = SchluterRefreshButton(coordinator, 40001)

        await button.async_press()

        coordinator.async_refresh.assert_not_called()

    def test_unavailable_when_device_missing(self, coordinator):
        """Test the button reports unavailable if the device drops out."""
        button = SchluterRefreshButton(coordinator, 40001)
        assert button.available is True

        coordinator.data = {}
        assert button.available is False
