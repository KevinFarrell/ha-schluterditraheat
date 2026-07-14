"""Binary sensor platform for Schluter DITRA-HEAT."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SchluterDataUpdateCoordinator
from .const import DOMAIN
from .entity import SchluterEntity

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


class SchluterGfciBinarySensor(SchluterEntity, BinarySensorEntity):
    """Binary sensor for GFCI fault detection on a Schluter thermostat."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "GFCI Status"

    def __init__(
        self, coordinator: SchluterDataUpdateCoordinator, device_id: int
    ) -> None:
        """Initialize the GFCI binary sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{self._identifier}_gfci"

    @property
    def is_on(self) -> bool | None:
        """Return True if GFCI fault detected."""
        gfci_status = self._thermostat.get("gfci_status")
        if gfci_status is None:
            return None
        return gfci_status != "ok"
