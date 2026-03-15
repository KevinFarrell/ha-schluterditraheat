"""Binary sensor platform for Schluter DITRA-HEAT."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
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
    """Set up Schluter binary sensor entities from a config entry."""
    coordinator: SchluterDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        SchluterGfciBinarySensor(coordinator, device_id)
        for device_id in coordinator.data
    ])


class SchluterGfciBinarySensor(
    CoordinatorEntity[SchluterDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor for GFCI fault detection on a Schluter thermostat."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_has_entity_name = True
    _attr_name = "GFCI Status"

    def __init__(
        self, coordinator: SchluterDataUpdateCoordinator, device_id: int
    ) -> None:
        """Initialize the GFCI binary sensor."""
        super().__init__(coordinator)
        self._device_id = device_id

        thermostat = coordinator.data[device_id]
        self._attr_unique_id = f"{thermostat['identifier']}_gfci"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, thermostat["identifier"])},
            "name": thermostat.get("group_name") or thermostat.get("name"),
            "manufacturer": thermostat.get("vendor", "Schluter"),
            "model": thermostat.get("sku", "DITRA-HEAT-E-WiFi"),
        }

    @property
    def is_on(self) -> bool | None:
        """Return True if GFCI fault detected."""
        thermostat = self.coordinator.data.get(self._device_id, {})
        gfci_status = thermostat.get("gfci_status")
        if gfci_status is None:
            return None
        return gfci_status != "ok"

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device_id in self.coordinator.data
