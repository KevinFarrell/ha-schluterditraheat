"""Config flow for Schluter DITRA-HEAT integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    SchluterApi,
    SchluterAuthenticationError,
    SchluterConnectionError,
    SchluterRateLimitError,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_credentials(
    hass: HomeAssistant, username: str, password: str
) -> dict[str, Any]:
    """Validate credentials by attempting authentication.

    Returns account information on success.
    Raises exceptions on failure.
    """
    session = async_get_clientsession(hass)
    api = SchluterApi(session, username, password)

    await api.authenticate()

    return {
        "account_id": api.account_id,
        "temperature_unit": api.temperature_unit,
    }


class SchluterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Schluter DITRA-HEAT."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self.reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                # Validate credentials
                info = await validate_credentials(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )

                # Check if already configured
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()

                # Create entry
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

            except SchluterConnectionError:
                errors["base"] = "cannot_connect"
            except SchluterAuthenticationError:
                errors["base"] = "invalid_auth"
            except SchluterRateLimitError:
                errors["base"] = "rate_limit"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during setup")
                errors["base"] = "unknown"

        # Show form
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauth when credentials expire."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauth confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            assert self.reauth_entry is not None

            username = self.reauth_entry.data[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            try:
                # Validate credentials
                await validate_credentials(self.hass, username, password)

                # Update entry
                self.hass.config_entries.async_update_entry(
                    self.reauth_entry,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                )

                await self.hass.config_entries.async_reload(self.reauth_entry.entry_id)

                return self.async_abort(reason="reauth_successful")

            except SchluterConnectionError:
                errors["base"] = "cannot_connect"
            except SchluterAuthenticationError:
                errors["base"] = "invalid_auth"
            except SchluterRateLimitError:
                errors["base"] = "rate_limit"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"

        # Show form
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={
                CONF_USERNAME: self.reauth_entry.data[CONF_USERNAME]
                if self.reauth_entry
                else ""
            },
        )
