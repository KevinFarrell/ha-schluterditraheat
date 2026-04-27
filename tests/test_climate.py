"""Unit tests for Schluter climate entity."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.schluterditraheat.climate import SchluterThermostat
from custom_components.schluterditraheat.const import (
    MODE_AUTO,
    MODE_FROST_SAFE,
    MODE_MANUAL,
    MODE_OFF,
    PRESET_FROST_PROTECTION,
    PRESET_NONE,
)


MOCK_THERMOSTAT = {
    "device_id": 40001,
    "identifier": "aa11bb22cc33dd44",
    "name": "DITRA-HEAT-E-RS1",
    "group_name": "Master Bath",
    "vendor": "Schluter",
    "sku": "?",
    "location_name": "Test Home",
    "current_temperature": 23.33,
    "target_temperature": 22.0,
    "mode": "auto",
    "heating_percent": 0,
    "air_floor_mode": "floor",
    "gfci_status": "ok",
}


@pytest.fixture
def coordinator():
    """Fixture for a mocked coordinator with one thermostat."""
    coord = MagicMock()
    coord.data = {40001: dict(MOCK_THERMOSTAT)}
    coord.api = MagicMock()
    coord.api.set_temperature = AsyncMock()
    coord.api.set_mode = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord


@pytest.fixture
def thermostat(coordinator):
    """Fixture for a SchluterThermostat entity."""
    entity = SchluterThermostat(coordinator, 40001)
    # Stub async_write_ha_state since we're not in a real HA context
    entity.async_write_ha_state = MagicMock()
    return entity


class TestClimateProperties:
    """Test climate entity read-only properties."""

    def test_name_uses_group_name(self, thermostat):
        """Test name prefers group_name."""
        assert thermostat.name == "Master Bath Floor Heat"

    def test_name_fallback_to_device_name(self, thermostat, coordinator):
        """Test name falls back to device name when no group."""
        coordinator.data[40001]["group_name"] = None
        assert thermostat.name == "DITRA-HEAT-E-RS1"

    def test_current_temperature(self, thermostat):
        """Test current_temperature reads from coordinator data."""
        assert thermostat.current_temperature == 23.33

    def test_target_temperature(self, thermostat):
        """Test target_temperature reads from coordinator data."""
        assert thermostat.target_temperature == 22.0

    def test_hvac_mode_auto(self, thermostat):
        """Test hvac_mode returns AUTO for 'auto'."""
        from homeassistant.components.climate import HVACMode

        assert thermostat.hvac_mode == HVACMode.AUTO

    def test_hvac_mode_off(self, thermostat, coordinator):
        """Test hvac_mode returns OFF for 'off'."""
        from homeassistant.components.climate import HVACMode

        coordinator.data[40001]["mode"] = MODE_OFF
        assert thermostat.hvac_mode == HVACMode.OFF

    def test_hvac_mode_manual_maps_to_heat(self, thermostat, coordinator):
        """Test hvac_mode returns HEAT for 'autoBypass' (manual)."""
        from homeassistant.components.climate import HVACMode

        coordinator.data[40001]["mode"] = MODE_MANUAL
        assert thermostat.hvac_mode == HVACMode.HEAT

    def test_hvac_action_idle(self, thermostat):
        """Test hvac_action returns IDLE when not heating."""
        from homeassistant.components.climate import HVACAction

        assert thermostat.hvac_action == HVACAction.IDLE

    def test_hvac_action_heating(self, thermostat, coordinator):
        """Test hvac_action returns HEATING when output > 0."""
        from homeassistant.components.climate import HVACAction

        coordinator.data[40001]["heating_percent"] = 50
        assert thermostat.hvac_action == HVACAction.HEATING

    def test_hvac_action_off(self, thermostat, coordinator):
        """Test hvac_action returns OFF when mode is off."""
        from homeassistant.components.climate import HVACAction

        coordinator.data[40001]["mode"] = MODE_OFF
        assert thermostat.hvac_action == HVACAction.OFF

    def test_preset_mode_none_when_manual(self, thermostat, coordinator):
        """Test preset_mode returns PRESET_NONE for manual mode."""
        coordinator.data[40001]["mode"] = MODE_MANUAL
        assert thermostat.preset_mode == PRESET_NONE

    def test_preset_mode_none_when_auto(self, thermostat, coordinator):
        """Test preset_mode returns PRESET_NONE for auto (schedule) mode."""
        coordinator.data[40001]["mode"] = MODE_AUTO
        assert thermostat.preset_mode == PRESET_NONE

    def test_preset_mode_none_when_off(self, thermostat, coordinator):
        """Test preset_mode returns PRESET_NONE when off."""
        coordinator.data[40001]["mode"] = MODE_OFF
        assert thermostat.preset_mode == PRESET_NONE

    def test_preset_mode_frost_protection(self, thermostat, coordinator):
        """Test preset_mode returns PRESET_FROST_PROTECTION for frostProtection mode."""
        coordinator.data[40001]["mode"] = MODE_FROST_SAFE
        assert thermostat.preset_mode == PRESET_FROST_PROTECTION

    def test_preset_mode_unknown_returns_none(self, thermostat, coordinator):
        """Test preset_mode returns None for an unrecognized API mode."""
        coordinator.data[40001]["mode"] = "someNewUnknownMode"
        assert thermostat.preset_mode is None

    def test_min_max_temp(self, thermostat):
        """Test min/max temperature limits."""
        assert thermostat.min_temp == 5.0
        assert thermostat.max_temp == 32.0

    def test_extra_state_attributes(self, thermostat):
        """Test extra_state_attributes contains expected keys."""
        attrs = thermostat.extra_state_attributes
        assert attrs["device_id"] == 40001
        assert attrs["identifier"] == "aa11bb22cc33dd44"
        assert attrs["location_name"] == "Test Home"
        assert attrs["group_name"] == "Master Bath"
        assert attrs["air_floor_mode"] == "floor"
        # heating_percent and gfci_status should NOT be in extra attrs
        assert "heating_percent" not in attrs
        assert "gfci_status" not in attrs

    def test_unique_id(self, thermostat):
        """Test unique_id is the device identifier."""
        assert thermostat._attr_unique_id == "aa11bb22cc33dd44"


class TestOptimisticUpdates:
    """Test optimistic state updates after API calls."""

    async def test_set_temperature_optimistic(self, thermostat, coordinator):
        """Test that set_temperature updates coordinator data immediately."""
        await thermostat.async_set_temperature(**{"temperature": 25.0})

        coordinator.api.set_temperature.assert_called_once_with(40001, 25.0)
        assert coordinator.data[40001]["target_temperature"] == 25.0
        thermostat.async_write_ha_state.assert_called_once()
        coordinator.async_request_refresh.assert_called_once()

    async def test_set_temperature_no_temp_is_noop(self, thermostat, coordinator):
        """Test that set_temperature without temperature kwarg does nothing."""
        await thermostat.async_set_temperature()

        coordinator.api.set_temperature.assert_not_called()

    async def test_set_hvac_mode_auto_optimistic(self, thermostat, coordinator):
        """Test that set_hvac_mode AUTO updates coordinator data."""
        from homeassistant.components.climate import HVACMode

        await thermostat.async_set_hvac_mode(HVACMode.AUTO)

        coordinator.api.set_mode.assert_called_once_with(40001, MODE_AUTO)
        assert coordinator.data[40001]["mode"] == MODE_AUTO
        thermostat.async_write_ha_state.assert_called_once()

    async def test_set_hvac_mode_heat_optimistic(self, thermostat, coordinator):
        """Test that set_hvac_mode HEAT maps to autoBypass."""
        from homeassistant.components.climate import HVACMode

        await thermostat.async_set_hvac_mode(HVACMode.HEAT)

        coordinator.api.set_mode.assert_called_once_with(40001, MODE_MANUAL)
        assert coordinator.data[40001]["mode"] == MODE_MANUAL

    async def test_set_hvac_mode_off_optimistic(self, thermostat, coordinator):
        """Test that set_hvac_mode OFF updates coordinator data."""
        from homeassistant.components.climate import HVACMode

        await thermostat.async_set_hvac_mode(HVACMode.OFF)

        coordinator.api.set_mode.assert_called_once_with(40001, MODE_OFF)
        assert coordinator.data[40001]["mode"] == MODE_OFF

    async def test_set_preset_mode_frost_protection(self, thermostat, coordinator):
        """Test that set_preset_mode frost_protection sends frostProtection to API."""
        await thermostat.async_set_preset_mode(PRESET_FROST_PROTECTION)

        coordinator.api.set_mode.assert_called_once_with(40001, MODE_FROST_SAFE)
        assert coordinator.data[40001]["mode"] == MODE_FROST_SAFE
        thermostat.async_write_ha_state.assert_called_once()
        coordinator.async_request_refresh.assert_called_once()

    async def test_set_preset_mode_none_maps_to_manual(self, thermostat, coordinator):
        """Test that set_preset_mode none sends manual to API."""
        coordinator.data[40001]["mode"] = MODE_FROST_SAFE  # start in frost protection

        await thermostat.async_set_preset_mode(PRESET_NONE)

        coordinator.api.set_mode.assert_called_once_with(40001, MODE_MANUAL)
        assert coordinator.data[40001]["mode"] == MODE_MANUAL
        thermostat.async_write_ha_state.assert_called_once()

    async def test_set_preset_mode_unsupported_is_noop(self, thermostat, coordinator):
        """Test that an unsupported preset mode does not call the API."""
        await thermostat.async_set_preset_mode("turbo_mode")

        coordinator.api.set_mode.assert_not_called()
