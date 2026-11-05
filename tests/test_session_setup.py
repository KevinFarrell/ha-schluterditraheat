"""Regression tests for saved sessions and temporary setup failures."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
import custom_components.schluterditraheat as integration
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.schluterditraheat.api import SchluterSessionLimitError

async def test_restart_uses_saved_session_and_persists_rotated_session():
    entry = SimpleNamespace(data={integration.CONF_USERNAME: 'test@example.com',
                                  integration.CONF_PASSWORD: 'dummy',
                                  'session_data': {'session_id': 'saved'}}, entry_id='entry')
    hass = Mock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    api = Mock()
    api.is_authenticated = True
    api.authenticate = AsyncMock()
    coordinator = Mock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    with patch.object(integration, 'SchluterApi', return_value=api) as factory, \
         patch.object(integration, 'SchluterDataUpdateCoordinator', return_value=coordinator):
        assert await integration.async_setup_entry(hass, entry)
        api.authenticate.assert_not_called()
        assert factory.call_args.kwargs['session_data'] == {'session_id': 'saved'}
        factory.call_args.kwargs['session_updated']({'session_id': 'rotated'})
        assert hass.config_entries.async_update_entry.call_args.kwargs['data']['session_data'] == {'session_id': 'rotated'}

async def test_session_limit_at_setup_is_retried_without_reauth():
    entry = SimpleNamespace(data={integration.CONF_USERNAME: "test", integration.CONF_PASSWORD: "dummy"})
    api = Mock(is_authenticated=False)
    api.authenticate = AsyncMock(side_effect=SchluterSessionLimitError('full'))
    with patch.object(integration, 'SchluterApi', return_value=api):
        with pytest.raises(ConfigEntryNotReady):
            await integration.async_setup_entry(Mock(), entry)

async def test_session_limit_during_poll_is_update_failure():
    api = Mock()
    api.get_static_data = AsyncMock(side_effect=SchluterSessionLimitError('full'))
    coordinator = integration.SchluterDataUpdateCoordinator(Mock(), api)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
