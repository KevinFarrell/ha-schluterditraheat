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
    SchluterApiError,
    SchluterAuthenticationError,
    SchluterConnectionError,
    SchluterRateLimitError,
)
from .const import (
    DOMAIN,
    RATE_LIMIT_BACKOFF_FACTOR,
    RATE_LIMIT_INITIAL_BACKOFF,
    RATE_LIMIT_MAX_BACKOFF,
    SCAN_INTERVAL,
    STATIC_REFRESH_INTERVAL_POLLS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CLIMATE]


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
    """Class to manage fetching Schluter data from the API.

    Caches static data (locations, devices, groups) and refreshes it hourly.
    Polls only device attributes on each 60-second cycle. Implements
    exponential backoff on rate-limit (429) responses.
    """

    def __init__(self, hass: HomeAssistant, api: SchluterApi) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.api = api
        self._static_data: dict[int, dict] | None = None
        self._polls_since_static_refresh: int = 0
        self._backoff_interval: timedelta | None = None

    def _needs_static_refresh(self) -> bool:
        """Determine if static data needs to be refreshed."""
        if self._static_data is None:
            return True
        return self._polls_since_static_refresh >= STATIC_REFRESH_INTERVAL_POLLS

    def _apply_rate_limit_backoff(self) -> None:
        """Increase the poll interval due to rate limiting."""
        if self._backoff_interval is None:
            self._backoff_interval = RATE_LIMIT_INITIAL_BACKOFF
        else:
            self._backoff_interval = min(
                self._backoff_interval * RATE_LIMIT_BACKOFF_FACTOR,
                RATE_LIMIT_MAX_BACKOFF,
            )
        self.update_interval = self._backoff_interval
        _LOGGER.warning(
            "Rate limited by Schluter API, backing off to %s",
            self._backoff_interval,
        )

    def _reset_backoff(self) -> None:
        """Reset poll interval to normal after a successful response."""
        if self._backoff_interval is not None:
            _LOGGER.info(
                "Rate limit backoff cleared, resuming normal %s poll interval",
                SCAN_INTERVAL,
            )
            self._backoff_interval = None
            self.update_interval = SCAN_INTERVAL

    async def _async_update_data(self) -> dict[int, dict]:
        """Fetch data from API.

        On first call and every STATIC_REFRESH_INTERVAL_POLLS polls, fetches
        full static data (locations, devices, groups). On every other poll,
        fetches only dynamic device attributes for known device_ids.

        Returns a dictionary mapping device_id to merged thermostat data,
        preserving the same dict shape that climate.py expects.
        """
        try:
            # Refresh static data if needed
            if self._needs_static_refresh():
                self._static_data = await self.api.get_static_data()
                self._polls_since_static_refresh = 0
                _LOGGER.debug(
                    "Refreshed static data, %d devices found",
                    len(self._static_data),
                )

            self._polls_since_static_refresh += 1

            # Fetch dynamic attributes for known devices
            device_ids = list(self._static_data.keys())
            dynamic_data = await self.api.get_device_attributes_bulk(device_ids)

            # Successful response — clear any backoff
            self._reset_backoff()

            # Merge static + dynamic, same shape as get_all_thermostats()
            result: dict[int, dict] = {}
            for device_id, static in self._static_data.items():
                if device_id not in dynamic_data:
                    continue
                result[device_id] = {**static, **dynamic_data[device_id]}

            return result

        except SchluterAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {err}"
            ) from err
        except SchluterRateLimitError as err:
            self._apply_rate_limit_backoff()
            raise UpdateFailed(
                f"Rate limited by API, next poll in {self._backoff_interval}: {err}"
            ) from err
        except SchluterConnectionError as err:
            raise UpdateFailed(
                f"Error communicating with API: {err}"
            ) from err
        except SchluterApiError as err:
            raise UpdateFailed(f"Unexpected API error: {err}") from err
