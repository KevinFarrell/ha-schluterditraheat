"""Test configuration — allow importing api/const without homeassistant."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock


# Real exception stubs so they can be raised/caught in tests.
class _UpdateFailed(Exception):
    """Stub for homeassistant.helpers.update_coordinator.UpdateFailed."""


class _ConfigEntryAuthFailed(Exception):
    """Stub for homeassistant.exceptions.ConfigEntryAuthFailed."""


# Minimal DataUpdateCoordinator stub — just enough for our coordinator to
# inherit from it and have __init__ set the attributes we rely on.
class _DataUpdateCoordinator:
    """Stub for homeassistant.helpers.update_coordinator.DataUpdateCoordinator."""

    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None


# Stub out homeassistant so __init__.py can be imported without the real package.
# Only the modules referenced by __init__.py at import time need stubs.
_HA_MODULES = [
    "homeassistant",
    "homeassistant.components",
    "homeassistant.components.binary_sensor",
    "homeassistant.components.climate",
    "homeassistant.components.sensor",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.update_coordinator",
]
for _mod in _HA_MODULES:
    sys.modules.setdefault(_mod, MagicMock())

# Minimal entity base class stubs — real classes so multiple inheritance works
# without metaclass conflicts (MagicMock bases cause TypeError).
class _CoordinatorEntity:
    """Stub for CoordinatorEntity."""

    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls


class _ClimateEntity:
    """Stub for ClimateEntity."""


class _BinarySensorEntity:
    """Stub for BinarySensorEntity."""


class _SensorEntity:
    """Stub for SensorEntity."""


# Wire real stubs into the mocked modules so imports resolve correctly.
sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = (
    _DataUpdateCoordinator
)
sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = (
    _CoordinatorEntity
)
sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed = _UpdateFailed
sys.modules["homeassistant.exceptions"].ConfigEntryAuthFailed = _ConfigEntryAuthFailed

# Entity base classes
sys.modules["homeassistant.components.binary_sensor"].BinarySensorEntity = (
    _BinarySensorEntity
)
sys.modules["homeassistant.components.climate"].ClimateEntity = _ClimateEntity
sys.modules["homeassistant.components.sensor"].SensorEntity = _SensorEntity

# Add the repo root so `custom_components.schluterditraheat` is importable.
_SRC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SRC_DIR))
