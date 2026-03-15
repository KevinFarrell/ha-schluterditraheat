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
        self._auth_lock = asyncio.Lock()

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

    async def _reauthenticate(self) -> None:
        """Re-authenticate after session expiry.

        Acquires an auth lock to prevent concurrent re-authentication from
        multiple simultaneous 401 responses.
        """
        async with self._auth_lock:
            _LOGGER.debug("Session expired, re-authenticating")
            try:
                await self.authenticate()
            except SchluterApiError as err:
                raise SchluterAuthenticationError(
                    "Re-authentication failed after session expiry"
                ) from err

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        _retry_auth: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Make an authenticated API request.

        On 401/403 responses, automatically re-authenticates and retries once.
        Set _retry_auth=False to disable retry (used internally to prevent loops).
        """
        if not self._session_id:
            raise SchluterAuthenticationError("Not authenticated")

        # Defensive copy to avoid mutation on retry (headers are popped)
        kwargs = dict(kwargs)

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
                    if resp.status in (401, 403):
                        if _retry_auth:
                            await self._reauthenticate()
                            return await self._request(
                                method, endpoint, _retry_auth=False, **kwargs
                            )
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

    @staticmethod
    def _validate_response(
        data: Any,
        required_fields: list[str],
        context: str,
    ) -> dict[str, Any]:
        """Validate that a response is a dict with required fields."""
        if not isinstance(data, dict):
            raise SchluterApiError(
                f"{context}: expected dict, got {type(data).__name__}"
            )
        missing = [f for f in required_fields if f not in data]
        if missing:
            raise SchluterApiError(
                f"{context}: missing required fields: {', '.join(missing)}"
            )
        return data

    @staticmethod
    def _validate_response_list(
        data: Any,
        required_fields: list[str],
        context: str,
    ) -> list[dict[str, Any]]:
        """Validate that a response is a list of dicts with required fields.

        Handles single-dict-to-list coercion for API endpoints that return
        a single object instead of a list when there is only one result.
        """
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise SchluterApiError(
                f"{context}: expected list or dict, got {type(data).__name__}"
            )
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise SchluterApiError(
                    f"{context}[{i}]: expected dict, got {type(item).__name__}"
                )
            missing = [f for f in required_fields if f not in item]
            if missing:
                raise SchluterApiError(
                    f"{context}[{i}]: missing required fields: {', '.join(missing)}"
                )
        return data

    async def get_locations(self) -> list[dict[str, Any]]:
        """Get all locations for the account."""
        data = await self._request("GET", f"/locations?account$id={self._account_id}")
        return self._validate_response_list(data, ["id", "name"], "get_locations")

    async def get_devices(self, location_id: int) -> list[dict[str, Any]]:
        """Get all devices for a location."""
        data = await self._request("GET", f"/devices?location$id={location_id}")
        return self._validate_response_list(data, ["id", "identifier"], "get_devices")

    async def get_groups(self, location_id: int) -> list[dict[str, Any]]:
        """Get all groups (rooms) for a location."""
        data = await self._request("GET", f"/groups?location$id={location_id}&type=room")
        return self._validate_response_list(data, ["id", "name"], "get_groups")

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
        return self._validate_response(
            data,
            ["roomTemperatureDisplay", "setpointMode"],
            f"get_device_attributes({device_id})",
        )

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

    async def get_static_data(self) -> dict[int, dict[str, Any]]:
        """Get static metadata for all devices.

        Fetches locations, devices, and groups, returning a dict keyed by
        device_id with static fields that rarely change (identifier, name,
        location, group, sku, vendor).
        """
        result: dict[int, dict[str, Any]] = {}

        locations = await self.get_locations()

        for location in locations:
            location_id = location["id"]
            location_name = location["name"]

            devices = await self.get_devices(location_id)
            groups = await self.get_groups(location_id)
            groups_by_id = {g["id"]: g for g in groups}

            for device in devices:
                device_id = device["id"]
                group_id = device.get("group$id")

                group_name = None
                if group_id and group_id in groups_by_id:
                    group_name = groups_by_id[group_id]["name"]

                result[device_id] = {
                    "device_id": device_id,
                    "identifier": device["identifier"],
                    "name": device.get("name", f"Thermostat {device_id}"),
                    "location_id": location_id,
                    "location_name": location_name,
                    "group_id": group_id,
                    "group_name": group_name,
                    "sku": device.get("sku"),
                    "vendor": device.get("vendor", "Schluter"),
                }

        return result

    async def get_device_attributes_bulk(
        self, device_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        """Fetch and parse attributes for multiple devices.

        Fetches attributes for each device sequentially (rate-limit safe).
        Devices that fail are logged and skipped — does not raise.

        Returns a dict keyed by device_id with parsed attribute values matching
        the keys that climate.py expects.
        """
        result: dict[int, dict[str, Any]] = {}

        for device_id in device_ids:
            try:
                raw = await self.get_device_attributes(device_id)
            except SchluterApiError as err:
                _LOGGER.error(
                    "Failed to get attributes for device %s: %s", device_id, err
                )
                continue

            result[device_id] = {
                "current_temperature": raw.get(
                    "roomTemperatureDisplay", {}
                ).get("value"),
                "target_temperature": raw.get("roomSetpoint"),
                "mode": raw.get("setpointMode"),
                "heating_percent": raw.get(
                    "outputPercentDisplay", {}
                ).get("percent", 0),
                "air_floor_mode": raw.get("airFloorMode"),
                "gfci_status": raw.get("gfciStatus"),
            }

        return result

    async def get_all_thermostats(self) -> list[dict[str, Any]]:
        """Get all thermostats with their current state.

        Returns a list of thermostat dictionaries with combined data from
        locations, devices, groups, and attributes. Same return shape as
        before — delegates to get_static_data() + get_device_attributes_bulk().
        """
        static_data = await self.get_static_data()
        dynamic_data = await self.get_device_attributes_bulk(list(static_data.keys()))

        thermostats = []
        for device_id, static in static_data.items():
            if device_id not in dynamic_data:
                continue
            thermostat = {**static, **dynamic_data[device_id]}
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
