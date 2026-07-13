"""Unit tests for energy statistics helpers."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.schluterditraheat.energy import statistic_id_for


class TestStatisticId:
    """Test external statistic id construction."""

    def test_statistic_id_format(self):
        """Test the statistic id is domain-prefixed and lowercased."""
        assert (
            statistic_id_for("AA11BB22CC33DD44")
            == "schluterditraheat:energy_aa11bb22cc33dd44"
        )

    def test_statistic_id_is_external(self):
        """Test the id uses a ':' separator (required for external statistics)."""
        assert ":" in statistic_id_for("device123")


class TestEnergyImportRespectsDailyCap:
    """The hourly energy timer must pause when the daily cap is in force.

    It runs on its own async_track_time_interval, independent of the
    coordinator's poll schedule, so it needs its own check — otherwise it keeps
    spending requests while the coordinator is paused on ACCDAYREQMAX.
    """

    @pytest.fixture
    def coordinator(self):
        """A coordinator with data and no daily cap in force."""
        coord = MagicMock()
        coord.data = {40001: {"device_id": 40001, "identifier": "aa11"}}
        coord.daily_limit_reached = False
        coord.note_daily_limit = MagicMock(return_value=1800.0)
        return coord

    async def test_skips_import_while_daily_capped(self, coordinator):
        """Test no API call is made while the cap is in force."""
        from custom_components.schluterditraheat import async_import_energy

        coordinator.daily_limit_reached = True

        with patch(
            "custom_components.schluterditraheat.async_update_energy_statistics",
            new=AsyncMock(),
        ) as mock_stats:
            await async_import_energy(MagicMock(), MagicMock(), coordinator)

        mock_stats.assert_not_awaited()

    async def test_imports_normally_when_not_capped(self, coordinator):
        """Test the import runs when the cap is not in force."""
        from custom_components.schluterditraheat import async_import_energy

        with patch(
            "custom_components.schluterditraheat.async_update_energy_statistics",
            new=AsyncMock(),
        ) as mock_stats:
            await async_import_energy(MagicMock(), MagicMock(), coordinator)

        mock_stats.assert_awaited_once()

    async def test_import_hitting_cap_pauses_coordinator(self, coordinator):
        """Test the energy path can trip the pause for the whole entry."""
        from custom_components.schluterditraheat import async_import_energy
        from custom_components.schluterditraheat.api import SchluterDailyLimitError

        with patch(
            "custom_components.schluterditraheat.async_update_energy_statistics",
            new=AsyncMock(side_effect=SchluterDailyLimitError("cap")),
        ):
            await async_import_energy(MagicMock(), MagicMock(), coordinator)

        coordinator.note_daily_limit.assert_called_once()

    async def test_other_errors_still_swallowed(self, coordinator):
        """Test an unrelated failure never propagates out of the timer."""
        from custom_components.schluterditraheat import async_import_energy

        with patch(
            "custom_components.schluterditraheat.async_update_energy_statistics",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await async_import_energy(MagicMock(), MagicMock(), coordinator)

        coordinator.note_daily_limit.assert_not_called()
