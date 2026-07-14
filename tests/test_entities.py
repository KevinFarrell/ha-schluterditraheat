"""Unit tests for Schluter entity classes."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.schluterditraheat.binary_sensor import (
    SchluterGfciBinarySensor,
)
from custom_components.schluterditraheat.climate import SchluterThermostat
from custom_components.schluterditraheat.const import DEFAULT_MODEL, DOMAIN
from custom_components.schluterditraheat.sensor import (
    SchluterHeatingOutputSensor,
    SchluterWifiSignalSensor,
)


MOCK_THERMOSTAT = {
    "device_id": 40001,
    "identifier": "aa11bb22cc33dd44",
    "name": "DITRA-HEAT-E-RS1",
    "group_name": "Master Bath",
    "vendor": "Schluter",
    "sku": "?",
    "signature_model": "737",
    "sw_version": "2.1.0",
    "hw_version": "2",
    "current_temperature": 23.33,
    "target_temperature": 23.33,
    "mode": "auto",
    "heating_percent": 42,
    "air_floor_mode": "floor",
    "gfci_status": "ok",
    "rssi": -58,
}

ALL_ENTITY_CLASSES = [
    SchluterThermostat,
    SchluterGfciBinarySensor,
    SchluterHeatingOutputSensor,
    SchluterWifiSignalSensor,
]


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


class TestWifiSignalSensor:
    """Test Wi-Fi signal strength sensor entity."""

    def test_native_value(self, coordinator):
        """Test native_value returns the raw dBm reading."""
        sensor = SchluterWifiSignalSensor(coordinator, 40001)
        assert sensor.native_value == -58

    def test_native_value_none_when_missing(self, coordinator):
        """Test native_value is None when the device reported no signal."""
        del coordinator.data[40001]["rssi"]
        sensor = SchluterWifiSignalSensor(coordinator, 40001)
        assert sensor.native_value is None

    def test_native_value_none_when_device_gone(self, coordinator):
        """Test native_value is None when the device left coordinator data."""
        sensor = SchluterWifiSignalSensor(coordinator, 40001)
        coordinator.data = {}
        assert sensor.native_value is None

    def test_unique_id(self, coordinator):
        """Test unique_id is based on identifier."""
        sensor = SchluterWifiSignalSensor(coordinator, 40001)
        assert sensor._attr_unique_id == "aa11bb22cc33dd44_wifi_signal"


class TestDeviceInfo:
    """Test the device registry entry built by the shared entity base."""

    def test_device_info(self, coordinator):
        """Test the Info-panel fields are exposed as device metadata."""
        info = SchluterHeatingOutputSensor(coordinator, 40001).device_info

        assert info["identifiers"] == {(DOMAIN, "aa11bb22cc33dd44")}
        assert info["name"] == "Master Bath"
        assert info["manufacturer"] == "Schluter"
        assert info["serial_number"] == "aa11bb22cc33dd44"
        assert info["sw_version"] == "2.1.0"
        assert info["hw_version"] == "2"

    def test_model_falls_back_to_signature(self, coordinator):
        """Test the literal "?" sku falls back to the signature model code.

        The API really does return "?" here, which is why a dict.get() default
        is not enough.
        """
        info = SchluterHeatingOutputSensor(coordinator, 40001).device_info
        assert info["model"] == "737"

    def test_model_prefers_real_sku(self, coordinator):
        """Test a meaningful sku wins over the signature model code."""
        coordinator.data[40001]["sku"] = "DH-RS1"
        info = SchluterHeatingOutputSensor(coordinator, 40001).device_info
        assert info["model"] == "DH-RS1"

    def test_model_default_when_nothing_known(self, coordinator):
        """Test the product name is used when neither source is usable."""
        coordinator.data[40001]["sku"] = "?"
        del coordinator.data[40001]["signature_model"]
        info = SchluterHeatingOutputSensor(coordinator, 40001).device_info
        assert info["model"] == DEFAULT_MODEL

    def test_versions_omitted_when_unknown(self, coordinator):
        """Test absent versions are omitted, not written as empty strings.

        A missing key is hidden in the UI; a blank one renders as a stray row.
        """
        del coordinator.data[40001]["sw_version"]
        del coordinator.data[40001]["hw_version"]
        info = SchluterHeatingOutputSensor(coordinator, 40001).device_info

        assert "sw_version" not in info
        assert "hw_version" not in info
        assert info["identifiers"] == {(DOMAIN, "aa11bb22cc33dd44")}

    @pytest.mark.parametrize("entity_class", ALL_ENTITY_CLASSES)
    def test_all_platforms_agree(self, coordinator, entity_class):
        """Test every platform registers the same device.

        Each platform calls async_get_or_create with whatever it declares, so
        the last one to set up wins. They must not drift apart.
        """
        info = entity_class(coordinator, 40001).device_info

        assert info["identifiers"] == {(DOMAIN, "aa11bb22cc33dd44")}
        assert info["name"] == "Master Bath"
        assert info["model"] == "737"
        assert info["sw_version"] == "2.1.0"


class TestClimateNaming:
    """Guard the entity naming the base class could silently change."""

    def test_climate_names_itself(self, coordinator):
        """Test the climate entity keeps its own name, not the device's."""
        thermostat = SchluterThermostat(coordinator, 40001)
        assert thermostat.name == "Master Bath Floor Heat"
        assert thermostat._attr_has_entity_name is False

    def test_climate_unique_id_is_bare_identifier(self, coordinator):
        """Test the climate unique_id did not gain a suffix in the refactor."""
        thermostat = SchluterThermostat(coordinator, 40001)
        assert thermostat._attr_unique_id == "aa11bb22cc33dd44"
