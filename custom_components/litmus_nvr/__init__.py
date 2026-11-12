"""The Litmus NVR integration.

Design: litmus-web docs/LITMUS_HOME_INTEGRATION.md §6 (modelled on the Frigate integration).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LitmusAuthError, LitmusClient, LitmusConnectionError
from .const import CONF_TOKEN, DOMAIN
from .hub import LitmusHub
from .views import LitmusNotificationView

PLATFORMS: list[Platform] = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.UPDATE,
]

type LitmusConfigEntry = ConfigEntry[LitmusHub]


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry) -> bool:
    client = LitmusClient(async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_TOKEN])
    hub = LitmusHub(hass, entry, client)
    try:
        await hub.async_start()
    except LitmusAuthError as err:
        raise ConfigEntryAuthFailed("Litmus NVR rejected the token") from err
    except LitmusConnectionError as err:
        raise ConfigEntryNotReady(f"Litmus NVR is not reachable: {err}") from err
    entry.runtime_data = hub

    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("view_registered"):
        hass.http.register_view(LitmusNotificationView(hass))
        domain_data["view_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LitmusConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
