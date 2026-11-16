"""Config flow — address + the Litmus local account; only a dedicated integration token is stored (never the password)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from urllib.parse import urlsplit
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    LitmusAuthError,
    LitmusClient,
    LitmusConnectionError,
    LitmusError,
    LitmusNotSetUpError,
    LitmusThrottledError,
    LitmusNotEnabledError,
    LitmusUpdateRequiredError,
    LitmusTokenLimitError,
)
from .const import API_VERSION, CONF_ADDON_SLUG, CONF_DEVICE_ID, CONF_TOKEN, DEFAULT_PORT, DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


def split_address(raw: str, port: int) -> tuple[str, int]:
    """Accept what people paste: `192.168.0.6`, `192.168.0.6:8128`, `http://192.168.0.6:8128/`, `[fe80::1]:8128`.

    The Litmus screen shows the address as `IP:port` with a copy button, so the host field must take it as is.
    A port inside the address wins over the port field (it is what the user copied).
    """
    text = raw.strip()
    if not text:
        return text, port
    if "://" not in text:
        if text.count(":") > 1 and not text.startswith("["):
            return text, port  # bare IPv6 without brackets — no port can be in it
        text = "http://" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname or raw.strip()
        found = parts.port
    except ValueError:
        return raw.strip(), port
    return host, (found if found else port)


class LitmusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Litmus NVR."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._device_id: str | None = None
        self._addon_slug: str = ""

    async def _probe(self, host: str, port: int) -> dict[str, Any]:
        client = LitmusClient(async_get_clientsession(self.hass), host, port)
        return await client.info()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = split_address(user_input[CONF_HOST], int(user_input[CONF_PORT]))
            try:
                info = await self._probe(host, port)
            except LitmusNotEnabledError:
                errors["base"] = "not_enabled"
            except LitmusUpdateRequiredError:
                # 2.3.x and a 2.4+ NVR that never turned the integration on answer alike (off = same as 2.3.x), so one message covers both.
                errors["base"] = "turn_on_or_update"
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

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Litmus NVR announced itself on the network (_litmus-nvr._tcp, only when Home Assistant is turned on in Litmus).
        A device already added just gets its address refreshed (DHCP changes) — UniFi Protect / Reolink do the same."""
        props = discovery_info.properties or {}
        device_id = str(props.get("id") or "")
        if not device_id:
            return self.async_abort(reason="not_litmus")
        host = str(discovery_info.host)
        port = int(discovery_info.port or DEFAULT_PORT)
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
        self._host, self._port, self._device_id = host, port, device_id
        self.context["title_placeholders"] = {"name": f"Litmus NVR ({host})"}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_credentials()
        return self.async_show_form(
            step_id="zeroconf_confirm", description_placeholders={"host": f"{self._host}:{self._port}"}
        )

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """Litmus NVR running as a Home Assistant add-on announced itself through the Supervisor (add-on `discovery: [litmus_nvr]`).
        The add-on image has no mDNS responder, so this is how it shows up under Discovered — the way Mosquitto or Z-Wave JS add-ons do.
        Sent only while Home Assistant is turned on in Litmus. config = {host: add-on hostname, port, id: Litmus device id}."""
        cfg = discovery_info.config or {}
        device_id = str(cfg.get("id") or "")
        host = str(cfg.get("host") or "")
        if not device_id or not host:
            return self.async_abort(reason="not_litmus")
        port = int(cfg.get("port") or DEFAULT_PORT)
        # The entry's unique id is the Litmus device id, not the discovery uuid — removing the discovery (Home Assistant turned off in
        # Litmus) must not remove the entry the user added (homeassistant/components/hassio/discovery.py async_process_del).
        # The add-on's slug — the sidebar opens its screen through ingress inside Home Assistant (the add-on hostname resolves only
        # inside HAOS, so the browser cannot open http://<hostname>:8128 — 2026-11-14 HAOS 18.4 test). An entry added by address
        # before gets it too (same device id).
        self._addon_slug = str(discovery_info.slug or "")
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port, CONF_ADDON_SLUG: self._addon_slug})
        self._host, self._port, self._device_id = host, port, device_id
        self.context["title_placeholders"] = {"name": NAME}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_credentials()
        return self.async_show_form(step_id="hassio_confirm")

    def _token_name(self) -> str:
        """Name shown in Litmus › Settings › Integrations — the HA home name tells several Home Assistants apart."""
        home = (self.hass.config.location_name or "").strip()
        return f"Home Assistant ({home[:40]})" if home and home != "Home" else "Home Assistant"

    async def _place_name(self, token: str) -> str:
        """The entry is titled with the NVR's place name (like ESPHome and Shelly title theirs with the device name) so several NVRs
        tell apart in Settings › Devices & services. Best effort — the name is not needed to set the entry up."""
        try:
            state = await LitmusClient(async_get_clientsession(self.hass), self._host, self._port, token).state()
        except LitmusError:
            return ""
        return str((state.get("device") or {}).get("name") or "").strip()

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
                token = await client.create_token(user_input[CONF_USERNAME], user_input[CONF_PASSWORD], self._token_name())
            except LitmusAuthError:
                errors["base"] = "invalid_auth"
            except LitmusThrottledError:
                errors["base"] = "too_many_attempts"
            except LitmusNotSetUpError:
                errors["base"] = "not_set_up"
            except LitmusTokenLimitError:
                errors["base"] = "token_limit"
            except LitmusNotEnabledError:
                errors["base"] = "not_enabled"
            except LitmusUpdateRequiredError:
                errors["base"] = "turn_on_or_update"
            except LitmusConnectionError:
                errors["base"] = "cannot_connect"
            else:
                data = {CONF_HOST: self._host, CONF_PORT: self._port, CONF_TOKEN: token, CONF_DEVICE_ID: self._device_id}
                if self._addon_slug:
                    data[CONF_ADDON_SLUG] = self._addon_slug
                if self.source == "reauth":
                    entry = self._get_reauth_entry()
                    old = entry.data.get(CONF_TOKEN)
                    if old and old != token:
                        # Revoke the old token so tokens do not pile up to the NVR's limit (it is usually already invalid).
                        await LitmusClient(async_get_clientsession(self.hass), self._host, self._port, old).delete_own_token()
                    return self.async_update_reload_and_abort(entry, data_updates=data)
                title = await self._place_name(token) or NAME
                if any(e.title == title for e in self._async_current_entries(include_ignore=False)):
                    # Two NVRs with the default place name (「My CCTV」) look the same in Devices & services — add the address,
                    # the same rule as the sidebar title (__init__._refresh_panels).
                    title = f"{title} ({self._host})"
                return self.async_create_entry(title=title, data=data)
        schema = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": f"{self._host}:{self._port}"},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the address (the PC running Litmus got a new IP) without removing the integration — Frigate and Reolink do the same.
        The new address must be the same Litmus device; the token stays."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = split_address(user_input[CONF_HOST], int(user_input[CONF_PORT]))
            try:
                info = await self._probe(host, port)
            except LitmusNotEnabledError:
                errors["base"] = "not_enabled"
            except LitmusUpdateRequiredError:
                # 2.3.x and a 2.4+ NVR that never turned the integration on answer alike (off = same as 2.3.x), so one message covers both.
                errors["base"] = "turn_on_or_update"
            except LitmusConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while probing Litmus NVR")
                errors["base"] = "unknown"
            else:
                if info.get("product") != "litmus":
                    errors["base"] = "not_litmus"
                else:
                    await self.async_set_unique_id(str(info.get("id")))
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host, CONF_PORT: port})
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)): int,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._host = entry_data[CONF_HOST]
        self._port = entry_data[CONF_PORT]
        self._device_id = entry_data.get(CONF_DEVICE_ID)
        return await self.async_step_credentials()
