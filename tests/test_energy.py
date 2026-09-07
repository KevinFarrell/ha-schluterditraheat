"""Unit tests for energy statistics helpers."""
from __future__ import annotations

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
