"""Button platform for Schluter DITRA-HEAT."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
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
    """Set up Schluter button entities from a config entry."""
    coordinator: SchluterDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        SchluterRefreshButton(coordinator, device_id)
        for device_id in coordinator.data
    ])


class SchluterRefreshButton(
    CoordinatorEntity[SchluterDataUpdateCoordinator], ButtonEntity
):
    """Button that forces an immediate poll of the Schluter cloud.

    The scheduled poll runs at SCAN_INTERVAL (300s, the minimum cadence Sinope
    asks integrators to respect), so a change made in the Schluter app can take
    that long to reach Home Assistant. This button fetches on demand instead of
    making everyone poll faster.
    """

    _attr_has_entity_name = True
    _attr_name = "Refresh"

    def __init__(
        self, coordinator: SchluterDataUpdateCoordinator, device_id: int
    ) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator)
        self._device_id = device_id

        thermostat = coordinator.data[device_id]
        self._attr_unique_id = f"{thermostat['identifier']}_refresh"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, thermostat["identifier"])},
            "name": thermostat.get("group_name") or thermostat.get("name"),
            "manufacturer": thermostat.get("vendor", "Schluter"),
            "model": thermostat.get("sku", "DITRA-HEAT-E-WiFi"),
        }

    async def async_press(self) -> None:
        """Refresh coordinator data now.

        Deliberately async_request_refresh() rather than async_refresh():
        the coordinator's debouncer runs the first press immediately but
        coalesces rapid repeat presses, so holding down the button cannot turn
        into a burst of API calls. The coordinator refreshes every device in
        one pass, so pressing this on any thermostat updates them all.
        """
        _LOGGER.debug("Manual refresh requested for device %s", self._device_id)
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device_id in self.coordinator.data
