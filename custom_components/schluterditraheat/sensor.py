"""Sensor platform for Schluter DITRA-HEAT."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SchluterDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Schluter sensor entities from a config entry."""
    coordinator: SchluterDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        SchluterHeatingOutputSensor(coordinator, device_id)
        for device_id in coordinator.data
    ])


class SchluterHeatingOutputSensor(
    CoordinatorEntity[SchluterDataUpdateCoordinator], SensorEntity
):
    """Sensor for heating output percentage on a Schluter thermostat."""

    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_has_entity_name = True
    _attr_name = "Heating Output"

    def __init__(
        self, coordinator: SchluterDataUpdateCoordinator, device_id: int
    ) -> None:
        """Initialize the heating output sensor."""
        super().__init__(coordinator)
        self._device_id = device_id

        thermostat = coordinator.data[device_id]
        self._attr_unique_id = f"{thermostat['identifier']}_heating_output"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, thermostat["identifier"])},
            "name": thermostat.get("group_name") or thermostat.get("name"),
            "manufacturer": thermostat.get("vendor", "Schluter"),
            "model": thermostat.get("sku", "DITRA-HEAT-E-WiFi"),
        }

    @property
    def native_value(self) -> int:
        """Return the current heating output percentage."""
        thermostat = self.coordinator.data.get(self._device_id, {})
        return thermostat.get("heating_percent", 0)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device_id in self.coordinator.data
