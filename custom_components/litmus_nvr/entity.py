"""Base entities — device layout follows the Frigate integration (one NVR device, one device per camera via_device)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, NAME, SIGNAL_STATE
from .hub import LitmusHub


def unique_id(hub: LitmusHub, type_name: str, name: str) -> str:
    """Frigate's `{id}:{type}:{name}` rule, keyed by the NVR device id (stable across re-adding the integration)."""
    return f"{hub.device_id}:{type_name}:{name}"


# unique_id types that exist only while a camera detects that class (person / vehicle / animal / sound, face / plate).
PRUNABLE_TYPES = frozenset({"presence", "zone_presence", "count", "zone_count", "recognized_face", "recognized_plate"})


@callback
def async_prune_stale(hass, entry, platform: str, entities) -> None:
    """Remove this platform's entities that the NVR no longer provides.

    Litmus 2.8 turns person / vehicle / animal on per camera; turning one off reloads the entry and its sensors are not
    created again. Left in the registry they stay "unavailable" forever (QA D-400). Removed cameras and zones already go
    the same way as devices (`async_setup_entry`). Only while the camera list is authoritative — a stopped or starting NVR
    reports an incomplete list and must not wipe entities.
    """
    if not entry.runtime_data.cameras_authoritative:
        return
    keep = {e.unique_id for e in entities if e.unique_id}
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.domain != platform or ent.platform != DOMAIN or ent.unique_id in keep:
            continue
        # Only the kinds that follow per-camera detection — never e.g. stats sensors, which a failed /stats fetch leaves out
        # of `entities` for one setup and must survive it.
        parts = str(ent.unique_id or "").split(":", 2)
        if len(parts) == 3 and parts[1] in PRUNABLE_TYPES:
            reg.async_remove(ent.entity_id)


class LitmusEntity(Entity):
    """Entity bound to the NVR device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: LitmusHub) -> None:
        self.hub = hub

    @property
    def available(self) -> bool:
        return self.hub.available

    @property
    def device_info(self) -> DeviceInfo:
        dev = self.hub.device
        return DeviceInfo(
            identifiers={(DOMAIN, self.hub.device_id)},
            name=dev.get("name") or NAME,
            manufacturer=MANUFACTURER,
            model=NAME,
            sw_version=dev.get("version"),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_STATE.format(self.hub.entry.entry_id), self._state_changed)
        )

    @callback
    def _state_changed(self) -> None:
        self.async_write_ha_state()


class LitmusCameraEntity(LitmusEntity):
    """Entity bound to one camera device."""

    def __init__(self, hub: LitmusHub, camera_id: str) -> None:
        super().__init__(hub)
        self.camera_id = camera_id

    @property
    def cam(self) -> dict[str, Any]:
        return self.hub.camera(self.camera_id) or {}

    @property
    def available(self) -> bool:
        return self.hub.available and self.hub.camera(self.camera_id) is not None

    @property
    def device_info(self) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self.hub.device_id}:{self.camera_id}")},
            name=self.cam.get("name") or self.camera_id,
            manufacturer=MANUFACTURER,
            model=f"{NAME} camera",
        )
        # HA 2026.x deprecates `via_device` (removed in 2027.8) in favour of `via_device_id`, resolved the way core
        # integrations do (reolink entity.py: dr.async_get_device_id_by_identifier). Older HA keeps `via_device`.
        lookup = getattr(dr, "async_get_device_id_by_identifier", None)
        if lookup is not None:
            try:
                info["via_device_id"] = lookup(self.hass, (DOMAIN, self.hub.device_id), config_entry_id=self.hub.entry.entry_id)
            except ValueError:
                pass   # NVR device not registered (should not happen: __init__ creates it first)
        else:
            info["via_device"] = (DOMAIN, self.hub.device_id)
        return info


def zone_identifier(hub: LitmusHub, camera_id: str, zone: str) -> str:
    return f"{hub.device_id}:{camera_id}:zone:{zone}"


class LitmusZoneEntity(LitmusEntity):
    """Entity bound to one detection zone — a device of its own, like Frigate's zone devices (via the NVR device)."""

    def __init__(self, hub: LitmusHub, camera_id: str, zone: str) -> None:
        super().__init__(hub)
        self.camera_id = camera_id
        self.zone = zone

    @property
    def z(self) -> dict[str, Any]:
        return self.hub.zone(self.camera_id, self.zone) or {}

    @property
    def available(self) -> bool:
        return self.hub.available and self.hub.zone(self.camera_id, self.zone) is not None

    @property
    def device_info(self) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, zone_identifier(self.hub, self.camera_id, self.zone))},
            name=self.zone,
            manufacturer=MANUFACTURER,
            model=f"{NAME} zone",
        )
        lookup = getattr(dr, "async_get_device_id_by_identifier", None)
        if lookup is not None:
            try:
                info["via_device_id"] = lookup(self.hass, (DOMAIN, self.hub.device_id), config_entry_id=self.hub.entry.entry_id)
            except ValueError:
                pass
        else:
            info["via_device"] = (DOMAIN, self.hub.device_id)
        return info
