"""Base entities — device layout follows the Frigate integration (one NVR device, one device per camera via_device)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, NAME, SIGNAL_STATE
from .hub import LitmusHub


def unique_id(hub: LitmusHub, type_name: str, name: str) -> str:
    """Frigate's `{id}:{type}:{name}` rule, keyed by the NVR device id (stable across re-adding the integration)."""
    return f"{hub.device_id}:{type_name}:{name}"


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
