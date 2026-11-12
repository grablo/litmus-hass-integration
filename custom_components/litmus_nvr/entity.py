"""Base entities — device layout follows the Frigate integration (one NVR device, one device per camera via_device)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
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
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.hub.device_id}:{self.camera_id}")},
            via_device=(DOMAIN, self.hub.device_id),
            name=self.cam.get("name") or self.camera_id,
            manufacturer=MANUFACTURER,
            model=f"{NAME} camera",
        )
