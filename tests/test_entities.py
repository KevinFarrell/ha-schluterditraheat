"""Unit tests for Schluter entity classes."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.schluterditraheat.binary_sensor import (
    SchluterGfciBinarySensor,
)
from custom_components.schluterditraheat.sensor import (
    SchluterHeatingOutputSensor,
    SchluterPowerSensor,
)


MOCK_THERMOSTAT = {
    "device_id": 40001,
    "identifier": "aa11bb22cc33dd44",
    "name": "DITRA-HEAT-E-RS1",
    "group_name": "Master Bath",
    "vendor": "Schluter",
    "sku": "?",
    "current_temperature": 23.33,
    "target_temperature": 23.33,
    "mode": "auto",
    "heating_percent": 42,
    "air_floor_mode": "floor",
    "gfci_status": "ok",
    "load_watt": 264,
}


@pytest.fixture
def coordinator():
    """Fixture for a mocked coordinator with one thermostat."""
    coord = MagicMock()
    coord.data = {40001: dict(MOCK_THERMOSTAT)}
    return coord


class TestGfciBinarySensor:
    """Test GFCI binary sensor entity."""

    def test_is_on_false_when_ok(self, coordinator):
        """Test that is_on is False when gfci_status is 'ok'."""
        sensor = SchluterGfciBinarySensor(coordinator, 40001)
        assert sensor.is_on is False

    def test_is_on_true_when_fault(self, coordinator):
        """Test that is_on is True when gfci_status is not 'ok'."""
        coordinator.data[40001]["gfci_status"] = "fault"
        sensor = SchluterGfciBinarySensor(coordinator, 40001)
        assert sensor.is_on is True

    def test_is_on_none_when_missing(self, coordinator):
        """Test that is_on is None when gfci_status is absent."""
        del coordinator.data[40001]["gfci_status"]
        sensor = SchluterGfciBinarySensor(coordinator, 40001)
        assert sensor.is_on is None

    def test_available_true(self, coordinator):
        """Test available when device exists in coordinator data."""
        sensor = SchluterGfciBinarySensor(coordinator, 40001)
        assert sensor.available is True

    def test_available_false(self, coordinator):
        """Test available when device removed from coordinator data."""
        sensor = SchluterGfciBinarySensor(coordinator, 40001)
        coordinator.data = {}
        assert sensor.available is False

    def test_unique_id(self, coordinator):
        """Test unique_id is based on identifier."""
        sensor = SchluterGfciBinarySensor(coordinator, 40001)
        assert sensor._attr_unique_id == "aa11bb22cc33dd44_gfci"


class TestHeatingOutputSensor:
    """Test heating output sensor entity."""

    def test_native_value(self, coordinator):
        """Test native_value returns heating_percent."""
        sensor = SchluterHeatingOutputSensor(coordinator, 40001)
        assert sensor.native_value == 42

    def test_native_value_default_zero(self, coordinator):
        """Test native_value defaults to 0 when heating_percent missing."""
        del coordinator.data[40001]["heating_percent"]
        sensor = SchluterHeatingOutputSensor(coordinator, 40001)
        assert sensor.native_value == 0

    def test_available_true(self, coordinator):
        """Test available when device exists in coordinator data."""
        sensor = SchluterHeatingOutputSensor(coordinator, 40001)
        assert sensor.available is True

    def test_available_false(self, coordinator):
        """Test available when device removed from coordinator data."""
        sensor = SchluterHeatingOutputSensor(coordinator, 40001)
        coordinator.data = {}
        assert sensor.available is False

    def test_unique_id(self, coordinator):
        """Test unique_id is based on identifier."""
        sensor = SchluterHeatingOutputSensor(coordinator, 40001)
        assert sensor._attr_unique_id == "aa11bb22cc33dd44_heating_output"


class TestPowerSensor:
    """Test instantaneous power sensor entity."""

    def test_native_value_is_load_times_output(self, coordinator):
        """Test power is connected load scaled by heating output percent."""
        sensor = SchluterPowerSensor(coordinator, 40001)
        # 264 W * 42% = 110.88 W
        assert sensor.native_value == 110.9

    def test_native_value_zero_when_idle(self, coordinator):
        """Test power is 0 when the thermostat is not calling for heat."""
        coordinator.data[40001]["heating_percent"] = 0
        sensor = SchluterPowerSensor(coordinator, 40001)
        assert sensor.native_value == 0

    def test_native_value_zero_when_load_unknown(self, coordinator):
        """Test power is 0 when the connected load is missing."""
        del coordinator.data[40001]["load_watt"]
        sensor = SchluterPowerSensor(coordinator, 40001)
        assert sensor.native_value == 0

    def test_native_value_none_when_device_gone(self, coordinator):
        """Test power is None when the device is absent from coordinator data."""
        sensor = SchluterPowerSensor(coordinator, 40001)
        coordinator.data = {}
        assert sensor.native_value is None

    def test_unique_id(self, coordinator):
        """Test unique_id is based on identifier."""
        sensor = SchluterPowerSensor(coordinator, 40001)
        assert sensor._attr_unique_id == "aa11bb22cc33dd44_power"
