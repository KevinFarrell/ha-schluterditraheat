"""API client for Schluter DITRA-HEAT."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import async_timeout

from .const import API_BASE_URL, API_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class SchluterApiError(Exception):
    """Base exception for Schluter API errors."""


class SchluterConnectionError(SchluterApiError):
    """Cannot connect to API."""


class SchluterAuthenticationError(SchluterApiError):
    """Invalid credentials or session expired."""


class SchluterRateLimitError(SchluterApiError):
    """Rate limit exceeded."""


class SchluterApi:
    """Async API client for Schluter DITRA-HEAT."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._username = username
        self._password = password
        self._session_id: str | None = None
        self._refresh_token: str | None = None
        self._account_id: int | None = None
        self._user_format: dict[str, str] = {}

    async def authenticate(self) -> None:
        """Authenticate with the Schluter API."""
        url = f"{API_BASE_URL}/login"
        payload = {
            "username": self._username,
            "password": self._password,
            "interface": "schluter",
            "stayConnected": 1,
        }

        headers = {
            "Content-Type": "application/json",
            "SWS-Requester": '{"web-app":{"interface":"schluter","app-version":"1.13.2"}}',
        }

        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 401:
                        raise SchluterAuthenticationError("Invalid username or password")
                    if resp.status == 429:
                        raise SchluterRateLimitError("Rate limit exceeded")
                    if resp.status != 200:
                        text = await resp.text()
                        raise SchluterApiError(f"Authentication failed: {resp.status} - {text}")

                    data = await resp.json()

                    self._session_id = data.get("session")
                    self._refresh_token = data.get("refreshToken")
                    self._account_id = data.get("account", {}).get("id")
                    self._user_format = data.get("user", {}).get("format", {})

                    if not self._session_id or not self._account_id:
                        raise SchluterApiError("Missing session ID or account ID in response")

                    _LOGGER.debug(
                        "Authenticated successfully, account_id=%s, temp_unit=%s",
                        self._account_id,
                        self._user_format.get("temperature", "unknown"),
                    )

        except asyncio.TimeoutError as err:
            raise SchluterConnectionError("Connection timeout") from err
        except aiohttp.ClientError as err:
            raise SchluterConnectionError(f"Connection error: {err}") from err

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Make an authenticated API request."""
        if not self._session_id:
            raise SchluterAuthenticationError("Not authenticated")

        url = f"{API_BASE_URL}{endpoint}"

        # Add session to both Cookie header and session-id header
        headers = kwargs.pop("headers", {})
        headers["session-id"] = self._session_id
        headers["Content-Type"] = "application/json"

        # Add session to cookies
        cookies = {
            "session": self._session_id,
        }
        if self._refresh_token:
            cookies["refreshToken"] = self._refresh_token

        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._session.request(
                    method, url, headers=headers, cookies=cookies, **kwargs
                ) as resp:
                    if resp.status == 401 or resp.status == 403:
                        raise SchluterAuthenticationError("Session expired or invalid")
                    if resp.status == 429:
                        raise SchluterRateLimitError("Rate limit exceeded")
                    if resp.status != 200:
                        text = await resp.text()
                        raise SchluterApiError(f"Request failed: {resp.status} - {text}")

                    return await resp.json()

        except asyncio.TimeoutError as err:
            raise SchluterConnectionError("Connection timeout") from err
        except aiohttp.ClientError as err:
            raise SchluterConnectionError(f"Connection error: {err}") from err

    async def get_locations(self) -> list[dict[str, Any]]:
        """Get all locations for the account."""
        data = await self._request("GET", f"/locations?account$id={self._account_id}")
        if isinstance(data, list):
            return data
        return [data]

    async def get_devices(self, location_id: int) -> list[dict[str, Any]]:
        """Get all devices for a location."""
        data = await self._request("GET", f"/devices?location$id={location_id}")
        if isinstance(data, list):
            return data
        return [data]

    async def get_groups(self, location_id: int) -> list[dict[str, Any]]:
        """Get all groups (rooms) for a location."""
        data = await self._request("GET", f"/groups?location$id={location_id}&type=room")
        if isinstance(data, list):
            return data
        return [data]

    async def get_device_attributes(self, device_id: int) -> dict[str, Any]:
        """Get attributes for a specific device."""
        attributes = [
            "airFloorMode",
            "roomTemperatureDisplay",
            "setpointMode",
            "outputPercentDisplay",
            "roomSetpoint",
            "occupancyMode",
            "gfciStatus",
            "floorSetpointPwm",
        ]

        endpoint = f"/device/{device_id}/attribute?attributes={','.join(attributes)}"
        data = await self._request("GET", endpoint)

        if isinstance(data, dict):
            return data
        raise SchluterApiError(f"Unexpected response type: {type(data)}")

    async def set_device_attribute(
        self,
        device_id: int,
        attribute: str,
        value: Any,
    ) -> None:
        """Set a device attribute."""
        endpoint = f"/device/{device_id}/attribute"
        payload = {attribute: value}

        await self._request("PUT", endpoint, json=payload)
        _LOGGER.debug("Set device %s attribute %s to %s", device_id, attribute, value)

    async def set_temperature(self, device_id: int, temperature_c: float) -> None:
        """Set the target temperature for a device (in Celsius)."""
        await self.set_device_attribute(device_id, "roomSetpoint", temperature_c)

    async def set_mode(self, device_id: int, mode: str) -> None:
        """Set the operating mode for a device.

        Valid modes: 'auto', 'off'
        """
        await self.set_device_attribute(device_id, "setpointMode", mode)

    async def get_all_thermostats(self) -> list[dict[str, Any]]:
        """Get all thermostats with their current state.

        Returns a list of thermostat dictionaries with combined data from
        locations, devices, groups, and attributes.
        """
        thermostats = []

        # Get all locations
        locations = await self.get_locations()

        for location in locations:
            location_id = location["id"]
            location_name = location["name"]

            # Get devices for this location
            devices = await self.get_devices(location_id)

            # Get groups (rooms) for this location
            groups = await self.get_groups(location_id)
            groups_by_id = {g["id"]: g for g in groups}

            # Get attributes for each device
            for device in devices:
                device_id = device["id"]
                group_id = device.get("group$id")

                # Get current attributes
                try:
                    attributes = await self.get_device_attributes(device_id)
                except SchluterApiError as err:
                    _LOGGER.error("Failed to get attributes for device %s: %s", device_id, err)
                    continue

                # Find the room name
                group_name = None
                if group_id and group_id in groups_by_id:
                    group_name = groups_by_id[group_id]["name"]

                # Combine all data
                thermostat = {
                    "device_id": device_id,
                    "identifier": device["identifier"],
                    "name": device.get("name", f"Thermostat {device_id}"),
                    "location_id": location_id,
                    "location_name": location_name,
                    "group_id": group_id,
                    "group_name": group_name,
                    "sku": device.get("sku"),
                    "vendor": device.get("vendor", "Schluter"),
                    # Current state from attributes
                    "current_temperature": attributes.get("roomTemperatureDisplay", {}).get("value"),
                    "target_temperature": attributes.get("roomSetpoint"),
                    "mode": attributes.get("setpointMode"),
                    "heating_percent": attributes.get("outputPercentDisplay", {}).get("percent", 0),
                    "air_floor_mode": attributes.get("airFloorMode"),
                    "gfci_status": attributes.get("gfciStatus"),
                }

                thermostats.append(thermostat)

        return thermostats

    @property
    def is_authenticated(self) -> bool:
        """Check if the client is authenticated."""
        return self._session_id is not None

    @property
    def account_id(self) -> int | None:
        """Get the account ID."""
        return self._account_id

    @property
    def temperature_unit(self) -> str:
        """Get the user's preferred temperature unit (f or c)."""
        return self._user_format.get("temperature", "f")
