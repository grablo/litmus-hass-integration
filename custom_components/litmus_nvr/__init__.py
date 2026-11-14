"""The Litmus NVR integration.

Design: litmus-web docs/LITMUS_HOME_INTEGRATION.md §6 (modelled on the Frigate integration).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LitmusAuthError, LitmusClient, LitmusConnectionError
from .const import CONF_ADDON_SLUG, CONF_TOKEN, DOMAIN, MANUFACTURER, NAME
from .entity import zone_identifier
from .hub import LitmusHub
from .panel import async_add_panel, async_remove_panel
from .views import LitmusNotificationClipView, LitmusNotificationView, LitmusRecordingView, LitmusThumbView

PLATFORMS: list[Platform] = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
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

    dev_reg = dr.async_get(hass)
    # The NVR device first — camera devices link to it by device id (via_device_id), like reolink creates its host device first.
    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub.device_id)},
        name=hub.device.get("name") or NAME,
        manufacturer=MANUFACTURER,
        model=NAME,
        sw_version=hub.device.get("version"),
    )
    # Cameras removed in Litmus leave their devices behind otherwise (Frigate removes devices of cameras no longer in the config).
    # `async_remove_device` like Frigate — `async_update_device(remove_config_entry_id=…)` is deprecated (breaks in HA 2027.8).
    if hub.cameras_authoritative:
        keep = {(DOMAIN, hub.device_id)} | {(DOMAIN, f"{hub.device_id}:{cam.get('id')}") for cam in hub.cameras}
        # Detection zones are devices too (Frigate) — a zone removed in Litmus goes the same way.
        keep |= {
            (DOMAIN, zone_identifier(hub, str(cam.get("id")), str(z.get("name"))))
            for cam in hub.cameras
            for z in cam.get("zones") or []
            if isinstance(z, dict) and z.get("name")
        }
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            if not device.identifiers & keep:
                dev_reg.async_remove_device(device.id)

    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("view_registered"):
        hass.http.register_view(LitmusNotificationView(hass))
        hass.http.register_view(LitmusNotificationClipView(hass))
        hass.http.register_view(LitmusRecordingView(hass))
        hass.http.register_view(LitmusThumbView(hass))
        domain_data["view_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Sidebar entry that opens the NVR's own screen (Frigate's add-on puts its UI in the sidebar the same way). With several NVRs
    # the place name tells them apart.
    several = len(hass.config_entries.async_entries(DOMAIN)) > 1
    title = f"{NAME} · {hub.device.get('name')}" if several and hub.device.get("name") else NAME
    await async_add_panel(hass, hub.device_id, entry.data[CONF_HOST], entry.data[CONF_PORT], title, entry.data.get(CONF_ADDON_SLUG, ""))
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The integration was deleted — revoke its token on the NVR so it disappears from Litmus › Integrations
    and re-adding does not pile tokens up to the NVR's limit. Best effort: the NVR may be off."""
    client = LitmusClient(async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data.get(CONF_TOKEN))
    await client.delete_own_token()


async def async_unload_entry(hass: HomeAssistant, entry: LitmusConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_remove_panel(hass, entry.runtime_data.device_id)
        await entry.runtime_data.async_stop()
    return unloaded
