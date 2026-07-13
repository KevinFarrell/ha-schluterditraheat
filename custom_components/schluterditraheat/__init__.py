"""The Schluter DITRA-HEAT integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import (
    SchluterApi,
    SchluterApiError,
    SchluterAuthenticationError,
    SchluterConnectionError,
    SchluterDailyLimitError,
    SchluterRateLimitError,
)
from .const import (
    DAILY_LIMIT_MAX_PAUSE,
    DOMAIN,
    ENERGY_UPDATE_INTERVAL,
    RATE_LIMIT_BACKOFF_FACTOR,
    RATE_LIMIT_INITIAL_BACKOFF,
    RATE_LIMIT_MAX_BACKOFF,
    RATE_LIMIT_REMAINING_FLOOR,
    SCAN_INTERVAL,
    STATIC_REFRESH_INTERVAL_POLLS,
)
from .energy import async_update_energy_statistics


def _seconds_until_local_midnight(hass: HomeAssistant) -> float:
    """Seconds from now until the next local midnight (for the daily cap)."""
    now = dt_util.now()
    midnight = dt_util.start_of_local_day() + timedelta(days=1)
    return max(SCAN_INTERVAL.total_seconds(), (midnight - now).total_seconds())


_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.SENSOR,
]


async def async_import_energy(
    hass: HomeAssistant,
    api: SchluterApi,
    coordinator: SchluterDataUpdateCoordinator,
) -> None:
    """Import energy consumption into long-term statistics for the Energy dashboard.

    Runs on its own hourly timer, independent of the coordinator's poll schedule
    (the cloud buckets consumption hourly, so there is nothing to gain from
    fetching it on every poll). Failures never affect the config entry.

    Because the timer is independent, it has to honour the daily cap itself:
    without the daily_limit_reached check it would keep calling the API every
    hour while the coordinator sits paused on ACCDAYREQMAX, swallowing the error
    each time and spending requests we do not have.
    """
    if not coordinator.data:
        return
    if coordinator.daily_limit_reached:
        _LOGGER.debug(
            "Skipping energy import: daily API request cap reached, "
            "waiting for the coordinator to recover"
        )
        return
    try:
        await async_update_energy_statistics(
            hass, api, list(coordinator.data.values())
        )
    except SchluterDailyLimitError as err:
        # The energy import can be the first caller to hit the cap. Pause the
        # coordinator too, so the whole integration backs off together rather
        # than each timer discovering the cap independently.
        seconds = coordinator.note_daily_limit()
        _LOGGER.warning(
            "Daily API request limit reached during energy import; "
            "pausing polling ~%ss: %s",
            round(seconds),
            err,
        )
    except Exception:  # noqa: BLE001 - energy import must never break setup
        _LOGGER.exception("Failed to update Schluter energy statistics")


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
    except SchluterRateLimitError as err:
        # Login rate/daily limited (also covers SchluterDailyLimitError) —
        # transient, so ask HA to retry setup later rather than failing hard.
        raise ConfigEntryNotReady(
            f"Schluter API rate limited during setup: {err}"
        ) from err
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

    async def _async_update_energy(_now: Any = None) -> None:
        await async_import_energy(hass, api, coordinator)

    entry.async_create_background_task(
        hass, _async_update_energy(), "schluter_energy_initial_import"
    )
    entry.async_on_unload(
        async_track_time_interval(hass, _async_update_energy, ENERGY_UPDATE_INTERVAL)
    )

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
    Polls only device attributes on each cycle (SCAN_INTERVAL). Throttles
    proactively from the API's reported rate-limit budget and backs off on
    rate-limit / daily-cap responses.
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
        # Two independent poll-interval overrides. The effective interval is
        # derived from them in one place (_recompute_interval); handlers only
        # set state, never assign update_interval directly.
        self._backoff_interval: timedelta | None = None  # explicit pause (backoff / daily cap)
        self._throttle_interval: timedelta | None = None  # budget-derived defer
        # True while the daily cap (ACCDAYREQMAX) is in force. Read by other
        # API callers on this config entry (the energy import) so they pause
        # too; cleared by the first successful poll.
        self.daily_limit_reached: bool = False

    def _needs_static_refresh(self) -> bool:
        """Determine if static data needs to be refreshed."""
        if self._static_data is None:
            return True
        return self._polls_since_static_refresh >= STATIC_REFRESH_INTERVAL_POLLS

    def _recompute_interval(self) -> None:
        """Derive the effective poll interval from the override state.

        Single source of truth: an explicit pause (rate-limit backoff or daily
        cap) wins, then a budget-derived defer, else the normal interval.
        """
        self.update_interval = (
            self._backoff_interval or self._throttle_interval or SCAN_INTERVAL
        )

    def _apply_rate_limit_backoff(self) -> None:
        """Grow the explicit backoff interval due to rate limiting."""
        if self._backoff_interval is None:
            self._backoff_interval = RATE_LIMIT_INITIAL_BACKOFF
        else:
            self._backoff_interval = min(
                self._backoff_interval * RATE_LIMIT_BACKOFF_FACTOR,
                RATE_LIMIT_MAX_BACKOFF,
            )
        _LOGGER.warning(
            "Rate limited by Schluter API, backing off to %s",
            self._backoff_interval,
        )

    def _clear_backoff(self) -> None:
        """Clear any explicit backoff after a successful response."""
        if self._backoff_interval is not None:
            _LOGGER.info("Rate limit backoff cleared, resuming normal poll interval")
            self._backoff_interval = None
        self.daily_limit_reached = False

    def note_daily_limit(self) -> float:
        """Pause polling after a daily-cap (ACCDAYREQMAX) hit.

        Callable by any API caller on this config entry, not just the poll loop,
        so whichever one hits the cap first pauses the rest. Returns the pause
        length in seconds.

        The pause targets the next local midnight but is capped at
        DAILY_LIMIT_MAX_PAUSE so polling re-checks periodically: the backend's
        true reset boundary (UTC vs. local) is not certain, and re-hitting the
        cap simply pauses again. A later successful poll clears it.
        """
        seconds = min(
            _seconds_until_local_midnight(self.hass),
            DAILY_LIMIT_MAX_PAUSE.total_seconds(),
        )
        self.daily_limit_reached = True
        self._backoff_interval = timedelta(seconds=seconds)
        self._recompute_interval()
        return seconds

    def _update_throttle_state(self) -> None:
        """Set the budget-derived defer from the latest rate-limit reading.

        When the server reports the remaining budget at/below the floor, defer
        the next poll until the window resets (clamped between the normal
        interval and the max backoff); otherwise clear the defer.
        """
        rate_limit = self.api.rate_limit
        if rate_limit is not None and rate_limit.is_low(RATE_LIMIT_REMAINING_FLOOR):
            seconds = rate_limit.seconds_until_reset()
            if seconds is None:
                self._throttle_interval = None
                return
            seconds = max(
                SCAN_INTERVAL.total_seconds(),
                min(seconds, RATE_LIMIT_MAX_BACKOFF.total_seconds()),
            )
            self._throttle_interval = timedelta(seconds=seconds)
            _LOGGER.warning(
                "Rate-limit budget low (remaining=%s), deferring next poll %ss",
                rate_limit.remaining,
                round(seconds),
            )
        else:
            self._throttle_interval = None

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

            # Success: clear any backoff, refresh the budget-derived defer from
            # the headers the server just returned, then derive the interval.
            self._clear_backoff()
            self._update_throttle_state()
            self._recompute_interval()

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
        except SchluterDailyLimitError as err:
            # Daily request cap hit. note_daily_limit() pauses polling and flags
            # the cap so the energy import pauses with us.
            seconds = self.note_daily_limit()
            raise UpdateFailed(
                f"Daily API request limit reached; pausing polling "
                f"~{round(seconds)}s: {err}"
            ) from err
        except SchluterRateLimitError as err:
            self._apply_rate_limit_backoff()
            self._recompute_interval()
            raise UpdateFailed(
                f"Rate limited by API, next poll in {self._backoff_interval}: {err}"
            ) from err
        except SchluterConnectionError as err:
            raise UpdateFailed(
                f"Error communicating with API: {err}"
            ) from err
        except SchluterApiError as err:
            raise UpdateFailed(f"Unexpected API error: {err}") from err
