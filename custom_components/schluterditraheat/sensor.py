"""Sensor platform for Schluter DITRA-HEAT."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
)
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
    """Set up Schluter sensor entities from a config entry."""
    coordinator: SchluterDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for device_id, thermostat in coordinator.data.items():
        entities.append(SchluterHeatingOutputSensor(coordinator, device_id))

        # Only create the Wi-Fi sensor for devices that actually report a
        # signal, rather than leaving everyone else with a permanently unknown
        # diagnostic entity.
        if "rssi" in thermostat:
            entities.append(SchluterWifiSignalSensor(coordinator, device_id))
        else:
            _LOGGER.debug(
                "Device %s reported no Wi-Fi signal; skipping the sensor", device_id
            )

    async_add_entities(entities)


class SchluterHeatingOutputSensor(SchluterEntity, SensorEntity):
    """Sensor for heating output percentage on a Schluter thermostat."""

    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_name = "Heating Output"

    def __init__(
        self, coordinator: SchluterDataUpdateCoordinator, device_id: int
    ) -> None:
        """Initialize the heating output sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{self._identifier}_heating_output"

    @property
    def native_value(self) -> int:
        """Return the current heating output percentage."""
        return self._thermostat.get("heating_percent", 0)


class SchluterWifiSignalSensor(SchluterEntity, SensorEntity):
    """Wi-Fi signal strength reported by a Schluter thermostat.

    The web app buckets this into a five-level scale (amazing, very good,
    okay, weak, very weak); the API itself returns the raw dBm value, which is
    what we expose.
    """

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Wi-Fi Signal"

    def __init__(
        self, coordinator: SchluterDataUpdateCoordinator, device_id: int
    ) -> None:
        """Initialize the Wi-Fi signal sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{self._identifier}_wifi_signal"

    @property
    def native_value(self) -> int | None:
        """Return the Wi-Fi signal strength in dBm."""
        return self._thermostat.get("rssi")
