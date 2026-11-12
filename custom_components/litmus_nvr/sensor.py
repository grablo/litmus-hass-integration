"""Last recognised face / plate per camera — Frigate's sensor_recognized_face / sensor_recognized_plate (cleared after 60 s)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitmusConfigEntry
from .const import SIGNAL_IDENTITY
from .entity import LitmusCameraEntity, unique_id
from .hub import LitmusHub


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities(
        LitmusIdentitySensor(hub, cam["id"], kind) for cam in hub.cameras for kind in ("face", "plate")
    )


class LitmusIdentitySensor(LitmusCameraEntity, SensorEntity):
    """Recognised name (face) or plate (vehicle)."""

    def __init__(self, hub: LitmusHub, camera_id: str, kind: str) -> None:
        super().__init__(hub, camera_id)
        self.kind = kind
        self._attr_unique_id = unique_id(hub, f"recognized_{kind}", camera_id)
        self._attr_translation_key = f"recognized_{kind}"

    @property
    def native_value(self) -> str | None:
        return ((self.cam.get("identity") or {}).get(self.kind) or {}).get("value")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_IDENTITY.format(self.hub.entry.entry_id), self._identity)
        )

    @callback
    def _identity(self, camera_id: str, kind: str) -> None:
        if camera_id == self.camera_id and kind == self.kind:
            self.async_write_ha_state()
