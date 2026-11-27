"""Presence per camera and class — Frigate's occupancy/sound binary sensors (off 30 s after last seen, decided by the NVR).

Also Frigate's zone occupancy (one device per detection zone) and its artificial "all" object (any class present).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitmusConfigEntry
from .const import ALL, CLASSES, SIGNAL_PRESENCE, SIGNAL_ZONE_PRESENCE, ZONE_CLASSES
from .entity import async_prune_stale, LitmusCameraEntity, LitmusZoneEntity, unique_id
from .hub import LitmusHub


def allowed_classes(hub: LitmusHub, cam: dict[str, Any] | None = None) -> list[str] | None:
    """What a camera detects. Litmus 2.8+ turns detections on per camera and sends that camera's list (`classes`, possibly empty);
    older NVRs send only the place list. None = no list at all (older 2.4 builds) = every class, as before."""
    if isinstance(cam, dict) and isinstance(cam.get("classes"), list):
        return [str(c) for c in cam["classes"]]
    return list(hub.classes) if hub.classes else None


def enabled_classes(hub: LitmusHub, classes: tuple[str, ...], cam: dict[str, Any] | None = None) -> list[str]:
    """Only what this camera (or, from older NVRs, this place) detects."""
    allowed = allowed_classes(hub, cam)
    return list(classes) if allowed is None else [c for c in classes if c in allowed]


def all_presence(presence: dict[str, Any], classes: list[str]) -> tuple[bool, int]:
    """Frigate's "all" object: on while any class is present, count = the sum (only classes that are on — a class off keeps
    its last count in the NVR's message)."""
    on = False
    count = 0
    for cls in classes:
        p = presence.get(cls) or {}
        if p.get("on"):
            on = True
            count += int(p.get("count") or 0)
    return on, count


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for cam in hub.cameras:
        enabled = enabled_classes(hub, CLASSES, cam)
        objects = enabled_classes(hub, ZONE_CLASSES, cam)
        entities.extend(LitmusPresenceSensor(hub, cam["id"], cls) for cls in enabled)
        if objects:   # Frigate adds "all" to every camera and zone (get_cameras_zones_and_objects include_all)
            entities.append(LitmusAllPresenceSensor(hub, cam["id"], objects))
        entities.append(LitmusOnlineSensor(hub, cam["id"]))
        for z in cam.get("zones") or []:
            if not isinstance(z, dict) or not z.get("name") or not objects:
                continue
            entities.extend(LitmusZonePresenceSensor(hub, cam["id"], str(z["name"]), cls) for cls in objects)
            entities.append(LitmusZoneAllPresenceSensor(hub, cam["id"], str(z["name"]), objects))
    async_add_entities(entities)
    async_prune_stale(hass, entry, "binary_sensor", entities)   # QA D-400 — sensors of detection turned off on a camera


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


class LitmusAllPresenceSensor(LitmusCameraEntity, BinarySensorEntity):
    """Anything (person, vehicle or animal) present on a camera — Frigate's "All occupancy"."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_translation_key = ALL

    def __init__(self, hub: LitmusHub, camera_id: str, classes: list[str]) -> None:
        super().__init__(hub, camera_id)
        self.classes = classes
        self._attr_unique_id = unique_id(hub, "presence", f"{camera_id}_{ALL}")

    @property
    def is_on(self) -> bool:
        return all_presence(self.cam.get("presence") or {}, self.classes)[0]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_PRESENCE.format(self.hub.entry.entry_id), self._presence)
        )

    @callback
    def _presence(self, camera_id: str, cls: str) -> None:
        if camera_id == self.camera_id and cls in self.classes:
            self.async_write_ha_state()


class LitmusZonePresenceSensor(LitmusZoneEntity, BinarySensorEntity):
    """Person / vehicle / animal inside a detection zone (Frigate zone occupancy)."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, hub: LitmusHub, camera_id: str, zone: str, cls: str) -> None:
        super().__init__(hub, camera_id, zone)
        self.cls = cls
        self._attr_unique_id = unique_id(hub, "zone_presence", f"{camera_id}_{zone}_{cls}")
        self._attr_translation_key = cls

    @property
    def is_on(self) -> bool:
        return bool(((self.z.get("presence") or {}).get(self.cls) or {}).get("on"))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ZONE_PRESENCE.format(self.hub.entry.entry_id), self._presence)
        )

    @callback
    def _presence(self, camera_id: str, zone: str, cls: str) -> None:
        if camera_id == self.camera_id and zone == self.zone and cls == self.cls:
            self.async_write_ha_state()


class LitmusZoneAllPresenceSensor(LitmusZoneEntity, BinarySensorEntity):
    """Anything inside a detection zone."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_translation_key = ALL

    def __init__(self, hub: LitmusHub, camera_id: str, zone: str, classes: list[str]) -> None:
        super().__init__(hub, camera_id, zone)
        self.classes = classes
        self._attr_unique_id = unique_id(hub, "zone_presence", f"{camera_id}_{zone}_{ALL}")

    @property
    def is_on(self) -> bool:
        return all_presence(self.z.get("presence") or {}, self.classes)[0]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ZONE_PRESENCE.format(self.hub.entry.entry_id), self._presence)
        )

    @callback
    def _presence(self, camera_id: str, zone: str, cls: str) -> None:
        if camera_id == self.camera_id and zone == self.zone and cls in self.classes:
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
