"""The Litmus NVR integration.

Design: litmus-web docs/LITMUS_HOME_INTEGRATION.md §6 (modelled on the Frigate integration).
"""

from __future__ import annotations

import ipaddress

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LitmusAuthError, LitmusClient, LitmusConnectionError, LitmusNotEnabledError, LitmusUpdateRequiredError
from .const import CONF_ADDON_SLUG, CONF_TITLE_HOST, CONF_TOKEN, DOMAIN, MANUFACTURER, NAME
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
    # ★ HA 가 「Failed setup, will retry: …」로 보이는 문장 — 번역 키로 낸다(HA exception-translations). 종전에는 aiohttp 원문
    #   (「Cannot connect to host … ssl:default [DNS server returned answer with no data]」)이 그대로 보였다(2026-11-16 실화면).
    #   원인 원문은 `from err` 로 로그에 남는다. 꺼짐·옛 판은 연결 실패와 다른 할 일이라 따로 말한다(설정 흐름 오류와 같은 문장).
    except LitmusNotEnabledError as err:
        raise ConfigEntryNotReady(translation_domain=DOMAIN, translation_key="setup_not_enabled") from err
    except LitmusUpdateRequiredError as err:
        raise ConfigEntryNotReady(translation_domain=DOMAIN, translation_key="setup_turn_on_or_update") from err
    except LitmusConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="setup_unreachable",
            translation_placeholders={"address": f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}"},
        ) from err
    entry.runtime_data = hub
    _follow_address(hass, entry, hub)

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
    # Sidebar entry that opens the NVR's own screen (Frigate's add-on puts its UI in the sidebar the same way).
    await _refresh_panels(hass, setting_up=entry)
    return True


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _follow_address(hass: HomeAssistant, entry: ConfigEntry, hub: LitmusHub) -> None:
    """An entry added next to another NVR with the same place name is titled `<place> (<address>)` (config_flow credentials).
    Every way the address changes — zeroconf/DHCP, reconfigure, the add-on announcing itself, adding the same NVR again — updates
    entry.data and reloads, so setup is the one place to swap the old address in the title for the new one; the sidebar title is
    rebuilt from entry.data every time already (_refresh_panels). Only the title the integration made is touched — a title the user
    renamed in Home Assistant no longer ends with ` (<old address>)` and is kept."""
    host = str(entry.data.get(CONF_HOST) or "")
    old = entry.data.get(CONF_TITLE_HOST)
    if old is None:
        # Entries made by 1.0.2 and before do not record the address. Recognise their title by the NVR's place name followed by
        # an IP address in brackets — exactly what config_flow made; anything else is left alone.
        name = str(hub.device.get("name") or "").strip()
        prefix = f"{name} ("
        if name and entry.title.startswith(prefix) and entry.title.endswith(")") and _is_ip(entry.title[len(prefix) : -1]):
            old = entry.title[len(prefix) : -1]
    if not old or not host or not entry.title.endswith(f" ({old})"):
        return
    if old == host:
        if entry.data.get(CONF_TITLE_HOST) != host:
            # An old entry recognised while the address is unchanged — record it, so a later place rename does not hide it.
            hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TITLE_HOST: host})
        return
    hass.config_entries.async_update_entry(
        entry,
        title=f"{entry.title[: -len(f' ({old})')]} ({host})",
        data={**entry.data, CONF_TITLE_HOST: host},
    )


async def _refresh_panels(hass: HomeAssistant, setting_up: ConfigEntry | None = None, leaving: ConfigEntry | None = None) -> None:
    """(Re)title every loaded NVR's sidebar entry. With several NVRs the place name tells them apart — so adding or removing one
    re-titles the others too (the first NVR's entry was titled while it was alone)."""
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e is not leaving]
    # NVRs set up together at startup are all still in progress — count those too, or each would think it is alone.
    several = sum(e.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS) for e in entries) > 1
    hubs = [
        e.runtime_data
        for e in entries
        # Only NVRs that are set up — a failed setup (NotReady, auth) leaves runtime_data behind but has no panel.
        if (e is setting_up or e.state is ConfigEntryState.LOADED) and isinstance(getattr(e, "runtime_data", None), LitmusHub)
    ]
    # Every new NVR starts with the same place name ("내 CCTV" / "My CCTV") — when two share one, the address tells them apart.
    names = [hub.device.get("name") for hub in hubs]
    for hub in hubs:
        name = hub.device.get("name")
        data = hub.entry.data
        title = f"{NAME} · {name}" if several and name else NAME
        if several and name and names.count(name) > 1:
            title = f"{title} ({data[CONF_HOST]})"
        await async_add_panel(hass, hub.device_id, data[CONF_HOST], data[CONF_PORT], title, data.get(CONF_ADDON_SLUG, ""))


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
        await _refresh_panels(hass, leaving=entry)
    return unloaded
