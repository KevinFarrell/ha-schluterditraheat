"""Unit tests for Schluter API client."""
import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses

from custom_components.schluterditraheat.api import (
    SchluterApi,
    SchluterApiError,
    SchluterAuthenticationError,
    SchluterConnectionError,
)
from custom_components.schluterditraheat.const import API_BASE_URL


@pytest.fixture
def mock_aiohttp():
    """Fixture to mock aiohttp responses."""
    with aioresponses() as m:
        yield m


@pytest.fixture
async def api_client():
    """Fixture to create an API client."""
    async with ClientSession() as session:
        api = SchluterApi(session, "test@example.com", "password123")
        yield api


class TestAuthentication:
    """Test authentication functionality."""

    async def test_successful_authentication(self, api_client, mock_aiohttp):
        """Test successful authentication."""
        mock_response = {
            "user": {
                "id": 20001,
                "email": "test@example.com",
                "format": {"temperature": "f"},
            },
            "account": {"id": 10001},
            "session": "test_session_id",
            "refreshToken": "test_refresh_token",
        }

        mock_aiohttp.post(
            f"{API_BASE_URL}/login",
            payload=mock_response,
            status=200,
        )

        await api_client.authenticate()

        assert api_client.is_authenticated
        assert api_client.account_id == 10001
        assert api_client.temperature_unit == "f"
        assert api_client._session_id == "test_session_id"

    async def test_authentication_invalid_credentials(self, api_client, mock_aiohttp):
        """Test authentication with invalid credentials."""
        mock_aiohttp.post(
            f"{API_BASE_URL}/login",
            status=401,
        )

        with pytest.raises(SchluterAuthenticationError):
            await api_client.authenticate()

    async def test_authentication_missing_session(self, api_client, mock_aiohttp):
        """Test authentication with missing session in response."""
        mock_response = {
            "user": {"id": 20001},
            "account": {"id": 10001},
            # Missing session and refreshToken
        }

        mock_aiohttp.post(
            f"{API_BASE_URL}/login",
            payload=mock_response,
            status=200,
        )

        with pytest.raises(SchluterApiError):
            await api_client.authenticate()


class TestGetLocations:
    """Test getting locations."""

    async def test_get_locations_success(self, api_client, mock_aiohttp):
        """Test successfully getting locations."""
        # Mock authentication
        mock_aiohttp.post(
            f"{API_BASE_URL}/login",
            payload={
                "session": "test_session",
                "account": {"id": 10001},
                "user": {"format": {"temperature": "f"}},
            },
        )

        # Mock get locations
        mock_locations = [
            {
                "id": 30001,
                "name": "Test Home",
                "postalCode": "12345",
            }
        ]

        mock_aiohttp.get(
            f"{API_BASE_URL}/locations?account$id=10001",
            payload=mock_locations,
            status=200,
        )

        await api_client.authenticate()
        locations = await api_client.get_locations()

        assert len(locations) == 1
        assert locations[0]["id"] == 30001
        assert locations[0]["name"] == "Test Home"


class TestGetDevices:
    """Test getting devices."""

    async def test_get_devices_success(self, api_client, mock_aiohttp):
        """Test successfully getting devices."""
        # Mock authentication
        api_client._session_id = "test_session"
        api_client._account_id = 10001

        # Mock get devices
        mock_devices = [
            {
                "id": 40001,
                "identifier": "aa11bb22cc33dd44",
                "name": "DITRA-HEAT-E-RS1",
                "location$id": 30001,
            }
        ]

        mock_aiohttp.get(
            f"{API_BASE_URL}/devices?location$id=30001",
            payload=mock_devices,
            status=200,
        )

        devices = await api_client.get_devices(30001)

        assert len(devices) == 1
        assert devices[0]["id"] == 40001
        assert devices[0]["identifier"] == "aa11bb22cc33dd44"


class TestGetDeviceAttributes:
    """Test getting device attributes."""

    async def test_get_device_attributes_success(self, api_client, mock_aiohttp):
        """Test successfully getting device attributes."""
        # Mock authentication
        api_client._session_id = "test_session"

        # Mock get attributes
        mock_attributes = {
            "airFloorMode": "floor",
            "roomTemperatureDisplay": {"status": "on", "value": 23.33},
            "setpointMode": "auto",
            "outputPercentDisplay": {"percent": 0, "sourceType": "heating"},
            "roomSetpoint": 23.33,
            "occupancyMode": "none",
            "gfciStatus": "ok",
        }

        import re as _re

        mock_aiohttp.get(
            _re.compile(r".*/device/40001/attribute\?attributes=.*"),
            payload=mock_attributes,
            status=200,
        )

        attributes = await api_client.get_device_attributes(40001)

        assert attributes["roomSetpoint"] == 23.33
        assert attributes["setpointMode"] == "auto"
        assert attributes["roomTemperatureDisplay"]["value"] == 23.33
        assert attributes["gfciStatus"] == "ok"


class TestSetTemperature:
    """Test setting temperature."""

    async def test_set_temperature_success(self, api_client, mock_aiohttp):
        """Test successfully setting temperature."""
        # Mock authentication
        api_client._session_id = "test_session"

        # Mock set temperature
        mock_aiohttp.put(
            f"{API_BASE_URL}/device/40001/attribute",
            payload={"roomSetpoint": 25.0},
            status=200,
        )

        await api_client.set_temperature(40001, 25.0)

        # Verify the request was made
        assert len(mock_aiohttp.requests) == 1


class TestSetMode:
    """Test setting mode."""

    async def test_set_mode_success(self, api_client, mock_aiohttp):
        """Test successfully setting mode."""
        # Mock authentication
        api_client._session_id = "test_session"

        # Mock set mode
        mock_aiohttp.put(
            f"{API_BASE_URL}/device/40001/attribute",
            payload={"setpointMode": "auto"},
            status=200,
        )

        await api_client.set_mode(40001, "auto")

        # Verify the request was made
        assert len(mock_aiohttp.requests) == 1


class TestErrorHandling:
    """Test error handling."""

    async def test_unauthenticated_request(self, api_client):
        """Test request without authentication."""
        # Don't set session_id
        with pytest.raises(SchluterAuthenticationError):
            await api_client.get_locations()

    async def test_session_expired_reauth_fails(self, api_client, mock_aiohttp):
        """Test handling of expired session when re-auth also fails."""
        api_client._session_id = "expired_session"
        api_client._account_id = 10001

        # First request returns 401
        mock_aiohttp.get(
            f"{API_BASE_URL}/locations?account$id=10001",
            status=401,
        )

        # Re-auth attempt fails
        mock_aiohttp.post(
            f"{API_BASE_URL}/login",
            status=401,
        )

        with pytest.raises(SchluterAuthenticationError):
            await api_client.get_locations()


class TestRetryOnAuth:
    """Test automatic retry on 401/403."""

    async def test_retry_on_401_succeeds(self, api_client, mock_aiohttp):
        """Test that a 401 triggers re-auth and retries the request."""
        api_client._session_id = "expired_session"
        api_client._account_id = 10001

        # First request returns 401
        mock_aiohttp.get(
            f"{API_BASE_URL}/locations?account$id=10001",
            status=401,
        )

        # Re-auth succeeds
        mock_aiohttp.post(
            f"{API_BASE_URL}/login",
            payload={
                "session": "new_session",
                "account": {"id": 10001},
                "user": {"format": {"temperature": "f"}},
            },
        )

        # Retry succeeds
        mock_aiohttp.get(
            f"{API_BASE_URL}/locations?account$id=10001",
            payload=[{"id": 30001, "name": "Home"}],
            status=200,
        )

        locations = await api_client.get_locations()

        assert len(locations) == 1
        assert locations[0]["id"] == 30001
        assert api_client._session_id == "new_session"

    async def test_retry_on_401_reauth_fails(self, api_client, mock_aiohttp):
        """Test that a 401 with failed re-auth raises auth error."""
        api_client._session_id = "expired_session"
        api_client._account_id = 10001

        # First request returns 401
        mock_aiohttp.get(
            f"{API_BASE_URL}/locations?account$id=10001",
            status=401,
        )

        # Re-auth fails
        mock_aiohttp.post(
            f"{API_BASE_URL}/login",
            status=401,
        )

        with pytest.raises(SchluterAuthenticationError, match="Re-authentication failed"):
            await api_client.get_locations()

    async def test_no_infinite_retry_loop(self, api_client, mock_aiohttp):
        """Test that retry only happens once — no infinite loop."""
        api_client._session_id = "expired_session"
        api_client._account_id = 10001

        # First request returns 401
        mock_aiohttp.get(
            f"{API_BASE_URL}/locations?account$id=10001",
            status=401,
        )

        # Re-auth succeeds
        mock_aiohttp.post(
            f"{API_BASE_URL}/login",
            payload={
                "session": "new_session",
                "account": {"id": 10001},
                "user": {"format": {"temperature": "f"}},
            },
        )

        # Retry also returns 401 — should NOT trigger another re-auth
        mock_aiohttp.get(
            f"{API_BASE_URL}/locations?account$id=10001",
            status=401,
        )

        with pytest.raises(SchluterAuthenticationError, match="Session expired"):
            await api_client.get_locations()

        # Verify exactly 2 GETs and 1 POST (login)
        all_calls = [
            (url_key, call)
            for url_key, calls in mock_aiohttp.requests.items()
            for call in calls
        ]
        get_count = sum(1 for url_key, _ in all_calls if url_key[0] == "GET")
        post_count = sum(1 for url_key, _ in all_calls if url_key[0] == "POST")
        assert get_count == 2
        assert post_count == 1


class TestResponseValidation:
    """Test response validation helpers."""

    async def test_locations_missing_required_field(self, api_client, mock_aiohttp):
        """Test that locations missing 'name' raises SchluterApiError."""
        api_client._session_id = "test_session"
        api_client._account_id = 10001

        mock_aiohttp.get(
            f"{API_BASE_URL}/locations?account$id=10001",
            payload=[{"id": 30001}],  # missing "name"
            status=200,
        )

        with pytest.raises(SchluterApiError, match="missing required fields.*name"):
            await api_client.get_locations()

    async def test_devices_non_list_response(self, api_client, mock_aiohttp):
        """Test that a non-dict/non-list device response raises SchluterApiError."""
        api_client._session_id = "test_session"
        api_client._account_id = 10001

        mock_aiohttp.get(
            f"{API_BASE_URL}/devices?location$id=30001",
            payload="not a list",
            status=200,
        )

        with pytest.raises(SchluterApiError, match="expected list or dict"):
            await api_client.get_devices(30001)

    async def test_attributes_missing_setpoint_mode(self, api_client, mock_aiohttp):
        """Test that attributes missing 'setpointMode' raises SchluterApiError."""
        import re as _re

        api_client._session_id = "test_session"

        mock_aiohttp.get(
            _re.compile(r".*/device/40001/attribute\?attributes=.*"),
            payload={"roomTemperatureDisplay": {"value": 23.0}},  # missing setpointMode
            status=200,
        )

        with pytest.raises(SchluterApiError, match="missing required fields.*setpointMode"):
            await api_client.get_device_attributes(40001)

    async def test_single_dict_coerced_to_list(self, api_client, mock_aiohttp):
        """Test that a single dict response is coerced to a list."""
        api_client._session_id = "test_session"
        api_client._account_id = 10001

        mock_aiohttp.get(
            f"{API_BASE_URL}/locations?account$id=10001",
            payload={"id": 30001, "name": "Home"},
            status=200,
        )

        locations = await api_client.get_locations()
        assert len(locations) == 1
        assert locations[0]["id"] == 30001


def _mock_static_endpoints(mock_aiohttp):
    """Set up mock responses for locations, devices, and groups."""
    import re as _re

    mock_aiohttp.get(
        f"{API_BASE_URL}/locations?account$id=10001",
        payload=[{"id": 30001, "name": "Test Home"}],
        status=200,
    )
    mock_aiohttp.get(
        f"{API_BASE_URL}/devices?location$id=30001",
        payload=[
            {
                "id": 40001,
                "identifier": "aa11bb22cc33dd44",
                "name": "DITRA-HEAT-E-RS1",
                "location$id": 30001,
                "group$id": 50001,
                "sku": "?"
            }
        ],
        status=200,
    )
    mock_aiohttp.get(
        _re.compile(r".*/groups\?location.*id=30001.*"),
        payload=[{"id": 50001, "name": "Master Bath"}],
        status=200,
    )


def _mock_attributes(mock_aiohttp, device_id=40001, **overrides):
    """Set up mock response for device attributes."""
    import re as _re

    attrs = {
        "airFloorMode": "floor",
        "roomTemperatureDisplay": {"status": "on", "value": 23.33},
        "setpointMode": "auto",
        "outputPercentDisplay": {"percent": 0, "sourceType": "heating"},
        "roomSetpoint": 23.33,
        "occupancyMode": "none",
        "gfciStatus": "ok",
        **overrides,
    }
    mock_aiohttp.get(
        _re.compile(rf".*/device/{device_id}/attribute\?attributes=.*"),
        payload=attrs,
        status=200,
    )


class TestSplitFetching:
    """Test get_static_data, get_device_attributes_bulk, and backward compat."""

    async def test_get_static_data(self, api_client, mock_aiohttp):
        """Test get_static_data returns correct shape keyed by device_id."""
        api_client._session_id = "test_session"
        api_client._account_id = 10001

        _mock_static_endpoints(mock_aiohttp)

        static = await api_client.get_static_data()

        assert 40001 in static
        entry = static[40001]
        assert entry["device_id"] == 40001
        assert entry["identifier"] == "aa11bb22cc33dd44"
        assert entry["name"] == "DITRA-HEAT-E-RS1"
        assert entry["location_name"] == "Test Home"
        assert entry["group_name"] == "Master Bath"
        assert entry["vendor"] == "Schluter"

    async def test_get_device_attributes_bulk(self, api_client, mock_aiohttp):
        """Test get_device_attributes_bulk parses attribute values correctly."""
        api_client._session_id = "test_session"

        _mock_attributes(mock_aiohttp, device_id=40001)

        result = await api_client.get_device_attributes_bulk([40001])

        assert 40001 in result
        attrs = result[40001]
        assert attrs["current_temperature"] == 23.33
        assert attrs["target_temperature"] == 23.33
        assert attrs["mode"] == "auto"
        assert attrs["heating_percent"] == 0
        assert attrs["air_floor_mode"] == "floor"
        assert attrs["gfci_status"] == "ok"

    async def test_get_device_attributes_bulk_partial_failure(
        self, api_client, mock_aiohttp
    ):
        """Test that one device failing doesn't prevent others from succeeding."""
        import re as _re

        api_client._session_id = "test_session"

        # First device returns 500
        mock_aiohttp.get(
            _re.compile(r".*/device/111/attribute\?attributes=.*"),
            status=500,
            body="Internal Server Error",
        )

        # Second device succeeds
        _mock_attributes(mock_aiohttp, device_id=222)

        result = await api_client.get_device_attributes_bulk([111, 222])

        assert 111 not in result
        assert 222 in result
        assert result[222]["mode"] == "auto"

    async def test_get_all_thermostats_backward_compat(
        self, api_client, mock_aiohttp
    ):
        """Test get_all_thermostats returns same shape as before refactor."""
        api_client._session_id = "test_session"
        api_client._account_id = 10001

        _mock_static_endpoints(mock_aiohttp)
        _mock_attributes(mock_aiohttp, device_id=40001)

        thermostats = await api_client.get_all_thermostats()

        assert len(thermostats) == 1
        t = thermostats[0]

        # Static fields
        assert t["device_id"] == 40001
        assert t["identifier"] == "aa11bb22cc33dd44"
        assert t["name"] == "DITRA-HEAT-E-RS1"
        assert t["location_name"] == "Test Home"
        assert t["group_name"] == "Master Bath"

        # Dynamic fields
        assert t["current_temperature"] == 23.33
        assert t["target_temperature"] == 23.33
        assert t["mode"] == "auto"
        assert t["heating_percent"] == 0
        assert t["air_floor_mode"] == "floor"
        assert t["gfci_status"] == "ok"
