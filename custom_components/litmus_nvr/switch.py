"""Per-camera detection switch — Frigate's `switch.<camera>_detect`.

Turns the NVR's analysis for that camera off or on. Live video and continuous recording keep going; detection, detection-based
recording and alerts stop while it is off. Like Frigate, the value is not written into the NVR's settings: when the NVR restarts
(including after the user applies settings in Litmus) the camera goes back to detecting.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitmusConfigEntry
from .api import LitmusError, LitmusNotRunningError
from .const import DOMAIN
from .entity import LitmusCameraEntity, unique_id
from .hub import LitmusHub


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities(LitmusDetectSwitch(hub, cam["id"]) for cam in hub.cameras)


class LitmusDetectSwitch(LitmusCameraEntity, SwitchEntity):
    """Detection on/off for one camera."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "detect"

    def __init__(self, hub: LitmusHub, camera_id: str) -> None:
        super().__init__(hub, camera_id)
        self._attr_unique_id = unique_id(hub, "switch", f"{camera_id}_detect")
        self._pending: bool | None = None   # value we just asked for, until the NVR's state confirms it

    @property
    def available(self) -> bool:
        # A stopped NVR reports its last camera list with live=false — nothing can be switched then.
        return super().available and self.hub.device.get("live", True) is not False

    @property
    def is_on(self) -> bool:
        actual = bool(self.cam.get("detect", True))
        if self._pending is not None and self._pending != actual:
            return self._pending
        self._pending = None
        return actual

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        try:
            await self.hub.client.set_detect(self.camera_id, on)
        except LitmusNotRunningError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_running") from err
        except LitmusError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="cannot_connect") from err
        # The NVR pushes its state within a few seconds; show the new value now (optimistic, Frigate does the same).
        self._pending = on
        self.async_write_ha_state()
