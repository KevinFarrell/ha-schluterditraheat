"""API client for Schluter DITRA-HEAT."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp
import async_timeout

from .const import API_BASE_URL, API_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# JSON error codes this backend (a white-labeled Sinope/Neviweb cloud) returns
# in an HTTP-200 body instead of using HTTP status codes.
#
# The codes are documented by the Neviweb community integration
# (https://github.com/claudegel/sinope-130), which describes the parent platform
# this backend white-labels. The codes themselves are confirmed present here
# (ACCDAYREQMAX appears in schluterditraheat.com's own web client), but the
# documented *limits* below come from Neviweb and are not independently verified
# against schluterditraheat.com — treat them as informative, not authoritative.
# Nothing in this module reads these values; handling is reactive.
ERROR_DAILY_LIMIT = "ACCDAYREQMAX"      # daily request cap; Neviweb documents
                                        # 30,000/day and the error body embeds
                                        # the cap as {"daily": 30000}
ERROR_RATE_LIMIT = "ACCRATELIMIT"       # logging in too frequently
ERROR_SESSION_LIMIT = "ACCSESSEXC"      # too many concurrent sessions
ERROR_SESSION_EXPIRED = "USRSESSEXP"    # session expired; re-authenticate


class SchluterApiError(Exception):
    """Base exception for Schluter API errors."""


class SchluterConnectionError(SchluterApiError):
    """Cannot connect to API."""


class SchluterAuthenticationError(SchluterApiError):
    """Invalid credentials or session expired."""


class SchluterSessionLimitError(SchluterAuthenticationError):
    """Too many active sessions on the account."""


class SchluterSessionExpiredError(SchluterAuthenticationError):
    """The server-side session expired (USRSESSEXP); re-authentication needed."""


class SchluterRateLimitError(SchluterApiError):
    """Rate limit exceeded (HTTP 429 or ACCRATELIMIT)."""


class SchluterDailyLimitError(SchluterRateLimitError):
    """Daily request cap reached (ACCDAYREQMAX); no more requests until reset."""


@dataclass
class RateLimit:
    """A snapshot of the API's rate-limit budget from response headers.

    The backend emits ``express-rate-limit`` headers on every response. On the
    authenticated polling routes ``reset`` has been observed as seconds remaining
    in a 10-second window (limit 120); ``seconds_until_reset`` still handles an
    epoch-timestamp form defensively in case other routes differ.
    """

    limit: int | None = None
    remaining: int | None = None
    reset: float | None = None
    captured_at: float = 0.0

    def is_low(self, floor: int) -> bool:
        """Return True when the remaining budget is at or below ``floor``."""
        return self.remaining is not None and self.remaining <= floor

    def seconds_until_reset(self, now: float | None = None) -> float | None:
        """Best-effort seconds until the window resets, or None if unknown."""
        if self.reset is None:
            return None
        now = time.time() if now is None else now
        # Values in epoch-seconds range are treated as absolute timestamps;
        # smaller values as a countdown captured at ``captured_at``.
        if self.reset > 1_000_000_000:
            return max(0.0, self.reset - now)
        elapsed = max(0.0, now - self.captured_at)
        return max(0.0, self.reset - elapsed)


def _coerce(value: Any, cast: Any) -> Any:
    """Cast a header value, returning None on missing/invalid input."""
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def parse_rate_limit_headers(headers: Any) -> RateLimit | None:
    """Parse ``X-RateLimit-*`` (or standard ``RateLimit-*``) headers.

    Returns a :class:`RateLimit` when at least one field is present, else None.
    Matching is case-insensitive and tolerates both header-name conventions.
    """
    lower = {str(k).lower(): v for k, v in dict(headers).items()}

    def _get(*names: str) -> Any:
        return next((lower[name] for name in names if name in lower), None)

    limit = _coerce(_get("x-ratelimit-limit", "ratelimit-limit"), int)
    remaining = _coerce(_get("x-ratelimit-remaining", "ratelimit-remaining"), int)
    reset = _coerce(_get("x-ratelimit-reset", "ratelimit-reset"), float)

    if limit is None and remaining is None and reset is None:
        return None
    return RateLimit(limit, remaining, reset, time.time())


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
        # Latest rate-limit budget seen on any response (None until first call).
        self.rate_limit: RateLimit | None = None

    def _capture_rate_limit(self, headers: Any) -> None:
        """Record the rate-limit budget from a response's headers, if present."""
        parsed = parse_rate_limit_headers(headers)
        if parsed is not None:
            self.rate_limit = parsed
            _LOGGER.debug(
                "Rate-limit budget: limit=%s remaining=%s reset=%s",
                parsed.limit,
                parsed.remaining,
                parsed.reset,
            )

    @staticmethod
    def _raise_for_error_code(code: str, data: dict[str, Any]) -> None:
        """Map a JSON ``error.code`` to a specific exception."""
        if code == ERROR_DAILY_LIMIT:
            raise SchluterDailyLimitError(f"Daily request limit reached: {data}")
        if code == ERROR_SESSION_LIMIT:
            raise SchluterSessionLimitError(
                "Too many active sessions. Log out of the "
                "Schluter app or web portal and try again."
            )
        if code == ERROR_SESSION_EXPIRED:
            raise SchluterSessionExpiredError("Server session expired")
        if code == ERROR_RATE_LIMIT:
            raise SchluterRateLimitError(
                "Login rate limited; wait a few minutes and try again."
            )
        raise SchluterApiError(f"API error: {code}")

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
                    self._capture_rate_limit(resp.headers)
                    if resp.status == 401:
                        raise SchluterAuthenticationError("Invalid username or password")
                    if resp.status == 429:
                        raise SchluterRateLimitError("Rate limit exceeded")
                    if resp.status != 200:
                        text = await resp.text()
                        raise SchluterApiError(f"Authentication failed: {resp.status} - {text}")

                    data = await resp.json()

                    if "error" in data:
                        error_code = (data["error"] or {}).get("code", "")
                        self._raise_for_error_code(error_code, data)

                    self._session_id = data.get("session")
                    self._refresh_token = data.get("refreshToken")
                    self._account_id = (data.get("account") or {}).get("id")
                    self._user_format = (data.get("user") or {}).get("format") or {}

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
            except SchluterRateLimitError:
                # A login rate/daily limit is transient — let the coordinator
                # back off rather than mislabeling it as an auth failure.
                raise
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

        # Copy kwargs before mutating (headers are popped) so retries see the original
        if _retry_auth:
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
                    self._capture_rate_limit(resp.headers)
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

                    data = await resp.json()

                    # This backend signals overload via an HTTP-200 JSON error
                    # body, not a status code. An expired session re-authenticates
                    # and retries once, mirroring the 401/403 path.
                    if isinstance(data, dict) and "error" in data:
                        code = (data["error"] or {}).get("code", "")
                        if code == ERROR_SESSION_EXPIRED and _retry_auth:
                            await self._reauthenticate()
                            return await self._request(
                                method, endpoint, _retry_auth=False, **kwargs
                            )
                        self._raise_for_error_code(code, data)

                    return data

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
            SchluterApi._validate_response(item, required_fields, f"{context}[{i}]")
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
            # Connected heating load per output, in watts. Combined with the
            # output percentage this gives instantaneous power draw.
            "loadWattOutput1",
            "loadWattOutput2",
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

            devices, groups = await asyncio.gather(
                self.get_devices(location_id),
                self.get_groups(location_id),
            )
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
        Per-device errors are logged and skipped, but account-wide failures
        (rate/daily limits, session/auth errors) are propagated to the caller.

        Returns a dict keyed by device_id with parsed attribute values matching
        the keys that climate.py expects.
        """
        result: dict[int, dict[str, Any]] = {}

        for device_id in device_ids:
            try:
                raw = await self.get_device_attributes(device_id)
            except (SchluterRateLimitError, SchluterAuthenticationError):
                # Rate/daily limits and session/auth failures affect every
                # device, not just this one — propagate so the coordinator can
                # back off or trigger re-authentication instead of silently
                # marking all entities unavailable.
                raise
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
                "load_watt": self._parse_load_watt(raw),
            }

        return result

    @staticmethod
    def _parse_load_watt(raw: dict[str, Any]) -> int:
        """Sum the connected load (watts) across both heating outputs.

        Outputs are returned as bare numbers, but tolerate the ``{"value": n}``
        wrapper some attributes use. Missing/None outputs count as zero.
        """
        def _watts(value: Any) -> float:
            if isinstance(value, dict):
                value = value.get("value")
            return value or 0

        return int(_watts(raw.get("loadWattOutput1")) + _watts(raw.get("loadWattOutput2")))

    async def get_consumption_history(
        self, device_id: int, granularity: str = "hourly"
    ) -> dict[str, Any]:
        """Fetch historical energy consumption for a device.

        ``granularity`` is one of "hourly", "daily", or "monthly". The API
        returns a rolling window (roughly the last day of hours, month of days,
        or half-year of months) as ``{"history": [{"date", "period"}, ...]}``.

        Note: the response labels ``unit`` as "watts", but each ``period`` value
        is the energy consumed during that bucket in *watt-hours*.
        """
        if granularity not in ("hourly", "daily", "monthly"):
            raise ValueError(f"Invalid granularity: {granularity}")

        endpoint = f"/device/{device_id}/consumption/{granularity}"
        data = await self._request("GET", endpoint)

        if isinstance(data, dict) and "error" in data:
            code = data["error"].get("code", "")
            raise SchluterApiError(
                f"get_consumption_history({device_id}, {granularity}): {code}"
            )

        return self._validate_response(
            data,
            ["history"],
            f"get_consumption_history({device_id}, {granularity})",
        )

    @staticmethod
    def parse_consumption_history(
        data: dict[str, Any],
    ) -> list[tuple[datetime, float]]:
        """Parse a consumption response into sorted (bucket_start, kWh) pairs.

        ``period`` values are watt-hours per bucket (despite the API's "watts"
        unit label) and are converted to kWh. Buckets missing a date or period
        are skipped. Timestamps are timezone-aware UTC on the bucket boundary.
        """
        history = data.get("history", []) if isinstance(data, dict) else []

        points: list[tuple[datetime, float]] = []
        for item in history:
            date_str = item.get("date")
            period = item.get("period")
            if date_str is None or period is None:
                continue
            start = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            points.append((start, period / 1000.0))

        points.sort(key=lambda point: point[0])
        return points

    @staticmethod
    def build_energy_statistics(
        points: list[tuple[datetime, float]],
        last_start: datetime | None = None,
        last_sum: float = 0.0,
        last_state: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Build cumulative statistics rows from per-bucket energy points.

        Returns a list of ``{"start", "state", "sum"}`` dicts suitable for
        Home Assistant's external statistics. ``sum`` is the running cumulative
        total across all buckets; ``state`` is the individual bucket's energy.

        When ``last_start`` is given (the most recently recorded bucket), rows
        from that bucket onward are re-emitted so a bucket that was still partial
        at the previous import is corrected, while cumulative continuity with the
        prior ``last_sum`` is preserved.
        """
        if last_start is not None:
            running = last_sum - last_state
            selected = [p for p in points if p[0] >= last_start]
        else:
            running = 0.0
            selected = points

        rows: list[dict[str, Any]] = []
        for start, kwh in selected:
            running += kwh
            rows.append({"start": start, "state": kwh, "sum": running})

        return rows

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

    async def logout(self) -> None:
        """Log out and invalidate the current session."""
        if not self._session_id:
            return

        try:
            await self._request("GET", "/logout", _retry_auth=False)
        except SchluterApiError as err:
            _LOGGER.debug("Logout failed: %s", err)
        finally:
            self._session_id = None
            self._refresh_token = None
            self._account_id = None

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
