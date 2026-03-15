"""Climate platform for Schluter DITRA-HEAT."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SchluterDataUpdateCoordinator
from .const import (
    ATTR_DEVICE_ID,
    ATTR_GROUP_NAME,
    ATTR_IDENTIFIER,
    ATTR_LOCATION_NAME,
    DOMAIN,
    MAX_TEMP_C,
    MIN_TEMP_C,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_OFF,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Schluter climate entities from a config entry."""
    coordinator: SchluterDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Create a climate entity for each thermostat
    entities = [
        SchluterThermostat(coordinator, device_id)
        for device_id in coordinator.data
    ]

    async_add_entities(entities)


class SchluterThermostat(CoordinatorEntity[SchluterDataUpdateCoordinator], ClimateEntity):
    """Representation of a Schluter DITRA-HEAT thermostat."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF, HVACMode.AUTO]

    def __init__(
        self, coordinator: SchluterDataUpdateCoordinator, device_id: int
    ) -> None:
        """Initialize the thermostat."""
        super().__init__(coordinator)
        self._device_id = device_id

        # Set unique ID based on device identifier
        thermostat = coordinator.data[device_id]
        self._attr_unique_id = thermostat["identifier"]

        # Set device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, thermostat["identifier"])},
            "name": self._get_display_name(thermostat),
            "manufacturer": thermostat.get("vendor", "Schluter"),
            "model": thermostat.get("sku", "DITRA-HEAT-E-WiFi"),
        }

    def _get_display_name(self, thermostat: dict[str, Any]) -> str:
        """Get a user-friendly display name for the thermostat."""
        # Prefer group (room) name, fallback to device name
        if thermostat.get("group_name"):
            return f"{thermostat['group_name']} Floor Heat"
        return thermostat.get("name", f"Thermostat {self._device_id}")

    @property
    def name(self) -> str:
        """Return the name of the thermostat."""
        thermostat = self.coordinator.data.get(self._device_id, {})
        return self._get_display_name(thermostat)

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        thermostat = self.coordinator.data.get(self._device_id, {})
        return thermostat.get("current_temperature")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        thermostat = self.coordinator.data.get(self._device_id, {})
        return thermostat.get("target_temperature")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        thermostat = self.coordinator.data.get(self._device_id, {})
        mode = thermostat.get("mode")

        if mode == MODE_OFF:
            return HVACMode.OFF
        if mode == MODE_AUTO:
            return HVACMode.AUTO
        # Default to HEAT for any other mode (manual/bypass)
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action."""
        thermostat = self.coordinator.data.get(self._device_id, {})
        heating_percent = thermostat.get("heating_percent", 0)

        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        # If heating output > 0, we're actively heating
        if heating_percent > 0:
            return HVACAction.HEATING

        return HVACAction.IDLE

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return MIN_TEMP_C

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return MAX_TEMP_C

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        thermostat = self.coordinator.data.get(self._device_id, {})
        return {
            ATTR_DEVICE_ID: self._device_id,
            ATTR_IDENTIFIER: thermostat.get("identifier"),
            ATTR_LOCATION_NAME: thermostat.get("location_name"),
            ATTR_GROUP_NAME: thermostat.get("group_name"),
            "air_floor_mode": thermostat.get("air_floor_mode"),
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        await self.coordinator.api.set_temperature(self._device_id, temperature)

        # Optimistic update — push new value to UI immediately
        if self._device_id in self.coordinator.data:
            self.coordinator.data[self._device_id]["target_temperature"] = temperature
            self.async_write_ha_state()

        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            mode = MODE_OFF
        elif hvac_mode == HVACMode.AUTO:
            mode = MODE_AUTO
        elif hvac_mode == HVACMode.HEAT:
            mode = MODE_MANUAL
        else:
            _LOGGER.error("Unsupported HVAC mode: %s", hvac_mode)
            return

        await self.coordinator.api.set_mode(self._device_id, mode)

        # Optimistic update — push new value to UI immediately
        if self._device_id in self.coordinator.data:
            self.coordinator.data[self._device_id]["mode"] = mode
            self.async_write_ha_state()

        await self.coordinator.async_request_refresh()
