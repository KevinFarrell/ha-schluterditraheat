"""Long-term energy statistics for Schluter DITRA-HEAT.

Imports the cloud's hourly consumption history into Home Assistant's long-term
statistics so each thermostat's energy usage appears in the Energy dashboard,
including backfilled history. One external statistic is maintained per device,
sourced from this integration's domain.

Home Assistant imports are done lazily inside the coroutine so this module (and
its pure helpers) can be imported without the ``recorder`` component present.
"""
from __future__ import annotations

import logging
from typing import Any

from .api import SchluterApi, SchluterApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def statistic_id_for(identifier: str) -> str:
    """Return the external statistic id for a device identifier."""
    return f"{DOMAIN}:energy_{identifier.lower()}"


async def async_update_energy_statistics(
    hass: Any,
    api: SchluterApi,
    thermostats: list[dict[str, Any]],
) -> None:
    """Import hourly energy consumption into long-term statistics.

    Runs for every thermostat. A device whose history is unavailable is logged
    and skipped; it never raises, so a failure here cannot break the config
    entry or the climate poll loop.
    """
    # Lazy imports: keep module import free of the recorder dependency.
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
        get_last_statistics,
    )
    from homeassistant.const import UnitOfEnergy
    from homeassistant.util import dt as dt_util

    def _row_start(row: dict[str, Any]) -> Any:
        """Normalize a statistics row's start to a tz-aware datetime."""
        start = row.get("start")
        if isinstance(start, (int, float)):
            return dt_util.utc_from_timestamp(start)
        return start

    for thermostat in thermostats:
        device_id = thermostat.get("device_id")
        identifier = thermostat.get("identifier")
        if device_id is None or not identifier:
            continue

        name = (
            thermostat.get("group_name")
            or thermostat.get("name")
            or f"Thermostat {device_id}"
        )
        statistic_id = statistic_id_for(identifier)

        try:
            raw = await api.get_consumption_history(device_id, "hourly")
        except SchluterApiError as err:
            _LOGGER.warning("Energy history unavailable for %s: %s", name, err)
            continue

        points = api.parse_consumption_history(raw)
        if not points:
            continue

        last_stats = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id, True, {"sum", "state"}
        )

        last_start = None
        last_sum = 0.0
        last_state = 0.0
        if last_stats and last_stats.get(statistic_id):
            row = last_stats[statistic_id][0]
            last_sum = row.get("sum") or 0.0
            last_state = row.get("state") or 0.0
            last_start = _row_start(row)

        rows = api.build_energy_statistics(
            points,
            last_start=last_start,
            last_sum=last_sum,
            last_state=last_state,
        )
        if not rows:
            continue

        metadata = {
            "has_mean": False,
            "has_sum": True,
            "name": f"{name} Energy",
            "source": DOMAIN,
            "statistic_id": statistic_id,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        }
        async_add_external_statistics(hass, metadata, rows)
        _LOGGER.debug(
            "Imported %d energy statistics rows for %s (%s)",
            len(rows),
            name,
            statistic_id,
        )
