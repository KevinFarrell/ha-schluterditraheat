"""Regression tests for transferring a validated session into entry setup."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.schluterditraheat import config_flow


SESSION = {
    "session_id": "test-session",
    "account_id": 10001,
    "refresh_token": "test-refresh",
    "user_format": {"temperature": "f"},
}
INFO = {"account_id": 10001, "temperature_unit": "f", "session_data": SESSION}


async def test_validation_returns_the_authenticated_session():
    api = Mock(account_id=10001, temperature_unit="f", session_data=SESSION)
    api.authenticate = AsyncMock()
    with patch.object(config_flow, "SchluterApi", return_value=api):
        result = await config_flow.validate_credentials(Mock(), "test@example.com", "dummy")
    assert result == INFO
    api.authenticate.assert_awaited_once()


async def test_new_entry_keeps_validation_session():
    flow = config_flow.SchluterConfigFlow()
    flow.hass = Mock()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    flow.async_create_entry = Mock(side_effect=lambda **kwargs: kwargs)
    with patch.object(config_flow, "validate_credentials", new=AsyncMock(return_value=INFO)) as validate:
        result = await flow.async_step_user({"username": "test@example.com", "password": "dummy"})
    assert result["data"]["session_data"] == SESSION
    validate.assert_awaited_once()


async def test_duplicate_entry_does_not_create_a_cloud_session():
    flow = config_flow.SchluterConfigFlow()
    flow.hass = Mock()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock(side_effect=RuntimeError("already configured"))
    with patch.object(config_flow, "validate_credentials", new=AsyncMock()) as validate:
        with pytest.raises(RuntimeError, match="already configured"):
            await flow.async_step_user({"username": "test@example.com", "password": "dummy"})
    validate.assert_not_awaited()


async def test_reauthentication_keeps_new_session_and_other_entry_data():
    flow = config_flow.SchluterConfigFlow()
    flow.hass = Mock()
    flow.hass.config_entries.async_reload = AsyncMock()
    flow.reauth_entry = SimpleNamespace(
        data={"username": "test@example.com", "password": "old", "other": "keep"},
        entry_id="test-entry",
    )
    flow.async_abort = Mock(side_effect=lambda **kwargs: kwargs)
    with patch.object(config_flow, "validate_credentials", new=AsyncMock(return_value=INFO)):
        result = await flow.async_step_reauth_confirm({"password": "new"})
    saved = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert saved["session_data"] == SESSION
    assert saved["password"] == "new"
    assert saved["other"] == "keep"
    flow.hass.config_entries.async_reload.assert_awaited_once_with("test-entry")
    assert result["reason"] == "reauth_successful"
