"""Presence per camera and class — Frigate's occupancy/sound binary sensors (off 30 s after last seen, decided by the NVR)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitmusConfigEntry
from .const import CLASSES, SIGNAL_PRESENCE
from .entity import LitmusCameraEntity, unique_id
from .hub import LitmusHub


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    # Only what this place detects (Litmus sends the detections that are turned on; "none" when only faces are).
    # An NVR that sends no list at all (older 2.4 builds) gets all four, as before.
    enabled = [c for c in CLASSES if c in hub.classes] if hub.classes else list(CLASSES)
    entities: list[BinarySensorEntity] = []
    for cam in hub.cameras:
        entities.extend(LitmusPresenceSensor(hub, cam["id"], cls) for cls in enabled)
        entities.append(LitmusOnlineSensor(hub, cam["id"]))
    async_add_entities(entities)


class LitmusPresenceSensor(LitmusCameraEntity, BinarySensorEntity):
    """Person / vehicle / animal / sound present on a camera."""

    def __init__(self, hub: LitmusHub, camera_id: str, cls: str) -> None:
        super().__init__(hub, camera_id)
        self.cls = cls
        self._attr_unique_id = unique_id(hub, "presence", f"{camera_id}_{cls}")
        self._attr_translation_key = cls
        self._attr_device_class = BinarySensorDeviceClass.SOUND if cls == "sound" else BinarySensorDeviceClass.OCCUPANCY

    @property
    def _p(self) -> dict:
        return (self.cam.get("presence") or {}).get(self.cls) or {}

    @property
    def is_on(self) -> bool:
        return bool(self._p.get("on"))

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        # No count here — the count sensor carries it. As an attribute it rewrote this sensor (a recorder row) every time the
        # number of people changed while the state stayed "on"; Frigate's occupancy sensors carry no attributes either.
        if self.cls == "sound":
            return {"sound": self._p.get("name")}
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_PRESENCE.format(self.hub.entry.entry_id), self._presence)
        )

    @callback
    def _presence(self, camera_id: str, cls: str) -> None:
        if camera_id == self.camera_id and cls == self.cls:
            self.async_write_ha_state()


class LitmusOnlineSensor(LitmusCameraEntity, BinarySensorEntity):
    """Camera stream connected."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "online"

    def __init__(self, hub: LitmusHub, camera_id: str) -> None:
        super().__init__(hub, camera_id)
        self._attr_unique_id = unique_id(hub, "online", camera_id)

    @property
    def is_on(self) -> bool:
        return bool(self.cam.get("online"))
