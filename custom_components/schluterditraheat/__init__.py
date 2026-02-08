"""The Schluter DITRA-HEAT integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    SchluterApi,
    SchluterAuthenticationError,
    SchluterConnectionError,
)
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Schluter DITRA-HEAT from a config entry."""
    # Create API client
    session = async_get_clientsession(hass)
    api = SchluterApi(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    # Authenticate
    try:
        await api.authenticate()
    except SchluterAuthenticationError as err:
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
    except SchluterConnectionError as err:
        _LOGGER.error("Failed to connect to Schluter API: %s", err)
        return False

    # Create coordinator
    coordinator = SchluterDataUpdateCoordinator(hass, api)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove coordinator
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class SchluterDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Schluter data from the API."""

    def __init__(self, hass: HomeAssistant, api: SchluterApi) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.api = api

    async def _async_update_data(self) -> dict[int, dict]:
        """Fetch data from API.

        Returns a dictionary mapping device_id to thermostat data.
        """
        try:
            thermostats = await self.api.get_all_thermostats()

            # Convert list to dict keyed by device_id
            return {t["device_id"]: t for t in thermostats}

        except SchluterAuthenticationError as err:
            # Trigger reauth flow
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except SchluterConnectionError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err
