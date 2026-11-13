"""Per-camera sensors — last recognised face / plate (Frigate sensor_recognized_*, cleared after 60 s) and object counts (Frigate `<cam>_<obj>_count`)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitmusConfigEntry
from .const import CLASSES, SIGNAL_IDENTITY, SIGNAL_PRESENCE
from .entity import LitmusCameraEntity, unique_id
from .hub import LitmusHub


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    entities: list[SensorEntity] = [
        LitmusIdentitySensor(hub, cam["id"], kind) for cam in hub.cameras for kind in ("face", "plate")
    ]
    # Counts only for classes that have a count (sound has none) and that this place detects — same rule as the presence sensors.
    counted = [c for c in CLASSES if c != "sound" and (c in hub.classes or not hub.classes)]
    entities.extend(LitmusCountSensor(hub, cam["id"], cls) for cam in hub.cameras for cls in counted)
    async_add_entities(entities)


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


class LitmusCountSensor(LitmusCameraEntity, SensorEntity):
    """How many people / vehicles / animals the camera sees right now (0 when none, held for the NVR's off delay)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: LitmusHub, camera_id: str, cls: str) -> None:
        super().__init__(hub, camera_id)
        self.cls = cls
        self._attr_unique_id = unique_id(hub, "count", f"{camera_id}_{cls}")
        self._attr_translation_key = f"{cls}_count"

    @property
    def native_value(self) -> int:
        p = (self.cam.get("presence") or {}).get(self.cls) or {}
        return int(p.get("count") or 0) if p.get("on") else 0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_PRESENCE.format(self.hub.entry.entry_id), self._presence)
        )

    @callback
    def _presence(self, camera_id: str, cls: str) -> None:
        if camera_id == self.camera_id and cls == self.cls:
            self.async_write_ha_state()
