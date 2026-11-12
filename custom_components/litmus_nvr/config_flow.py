"""Config flow — address + the Litmus local account; only a dedicated integration token is stored (never the password)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LitmusAuthError, LitmusClient, LitmusConnectionError, LitmusThrottledError
from .const import API_VERSION, CONF_DEVICE_ID, CONF_TOKEN, DEFAULT_PORT, DOMAIN, NAME


class LitmusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Litmus NVR."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._device_id: str | None = None

    async def _probe(self, host: str, port: int) -> dict[str, Any]:
        client = LitmusClient(async_get_clientsession(self.hass), host, port)
        return await client.info()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            try:
                info = await self._probe(host, port)
            except LitmusConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if info.get("product") != "litmus":
                    errors["base"] = "not_litmus"
                elif int(info.get("api", 0)) != API_VERSION:
                    errors["base"] = "update_required"
                else:
                    self._device_id = str(info["id"])
                    await self.async_set_unique_id(self._device_id)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
                    self._host, self._port = host, port
                    return await self.async_step_credentials()
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=(user_input or {}).get(CONF_PORT, DEFAULT_PORT)): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_credentials(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = LitmusClient(async_get_clientsession(self.hass), self._host, self._port)
            try:
                token = await client.create_token(user_input[CONF_USERNAME], user_input[CONF_PASSWORD], "Home Assistant")
            except LitmusAuthError:
                errors["base"] = "invalid_auth"
            except LitmusThrottledError:
                errors["base"] = "too_many_attempts"
            except LitmusConnectionError:
                errors["base"] = "cannot_connect"
            else:
                data = {CONF_HOST: self._host, CONF_PORT: self._port, CONF_TOKEN: token, CONF_DEVICE_ID: self._device_id}
                if self.source == "reauth":
                    return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
                return self.async_create_entry(title=NAME, data=data)
        schema = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": f"{self._host}:{self._port}"},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._host = entry_data[CONF_HOST]
        self._port = entry_data[CONF_PORT]
        self._device_id = entry_data.get(CONF_DEVICE_ID)
        return await self.async_step_credentials()
