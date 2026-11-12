"""Config flow — address + the Litmus local account; only a dedicated integration token is stored (never the password)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    LitmusAuthError,
    LitmusClient,
    LitmusConnectionError,
    LitmusNotSetUpError,
    LitmusThrottledError,
    LitmusTokenLimitError,
)
from .const import API_VERSION, CONF_DEVICE_ID, CONF_TOKEN, DEFAULT_PORT, DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


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
            except Exception:  # noqa: BLE001 — HA scaffold pattern: never crash the flow
                _LOGGER.exception("Unexpected error while probing Litmus NVR")
                errors["base"] = "unknown"
            else:
                if info.get("product") != "litmus":
                    errors["base"] = "not_litmus"
                elif str(info.get("api", "0")) != str(API_VERSION):
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
                if self.source == "reauth":
                    # The address may now point at a different Litmus device — do not store its token under this entry.
                    info = await client.info()
                    await self.async_set_unique_id(str(info.get("id")))
                    self._abort_if_unique_id_mismatch()
                token = await client.create_token(user_input[CONF_USERNAME], user_input[CONF_PASSWORD], "Home Assistant")
            except LitmusAuthError:
                errors["base"] = "invalid_auth"
            except LitmusThrottledError:
                errors["base"] = "too_many_attempts"
            except LitmusNotSetUpError:
                errors["base"] = "not_set_up"
            except LitmusTokenLimitError:
                errors["base"] = "token_limit"
            except LitmusConnectionError:
                errors["base"] = "cannot_connect"
            else:
                data = {CONF_HOST: self._host, CONF_PORT: self._port, CONF_TOKEN: token, CONF_DEVICE_ID: self._device_id}
                if self.source == "reauth":
                    entry = self._get_reauth_entry()
                    old = entry.data.get(CONF_TOKEN)
                    if old and old != token:
                        # Revoke the old token so tokens do not pile up to the NVR's limit (it is usually already invalid).
                        await LitmusClient(async_get_clientsession(self.hass), self._host, self._port, old).delete_own_token()
                    return self.async_update_reload_and_abort(entry, data_updates=data)
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
