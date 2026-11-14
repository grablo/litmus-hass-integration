"""Per-camera sensors — last recognised face / plate (Frigate sensor_recognized_*, cleared after 60 s) and object counts (Frigate `<cam>_<obj>_count`)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitmusConfigEntry
from .const import CLASSES, SIGNAL_IDENTITY, SIGNAL_PRESENCE
from .entity import LitmusCameraEntity, LitmusEntity, unique_id
from .hub import LitmusHub


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    # Recognised face / plate only when this place recognises faces / vehicles (plates are read by vehicle recognition) — same rule as
    # the presence sensors: no entity for a feature that is off. An empty class list (older Litmus) means everything.
    kinds = [k for k, cls in (("face", "face"), ("plate", "vehicle")) if cls in hub.classes or not hub.classes]
    entities: list[SensorEntity] = [LitmusIdentitySensor(hub, cam["id"], kind) for cam in hub.cameras for kind in kinds]
    # Counts only for classes that have a count (sound has none) and that this place detects — same rule as the presence sensors.
    counted = [c for c in CLASSES if c != "sound" and (c in hub.classes or not hub.classes)]
    entities.extend(LitmusCountSensor(hub, cam["id"], cls) for cam in hub.cameras for cls in counted)
    entities.append(LitmusStatusSensor(hub))
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


# What the NVR is doing — so a restart to apply settings reads as "Applying settings", not as a broken device (cameras go unavailable while
# the detection pipeline restarts; the connection to the NVR stays up). NVR `device.status` (2.4.0, ExtApi deviceStatus).
STATUS_OPTIONS = ["running", "applying", "starting", "stopping", "stopped", "error"]


class LitmusStatusSensor(LitmusEntity, SensorEntity):
    """NVR status: running · applying settings · starting · stopping · stopped · error."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUS_OPTIONS
    _attr_translation_key = "status"

    def __init__(self, hub: LitmusHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = unique_id(hub, "status", "controller")

    @property
    def native_value(self) -> str | None:
        status = self.hub.device.get("status")
        if status in STATUS_OPTIONS:
            return status
        # An NVR without the field (older 2.4 builds): running while its cameras are live, otherwise unknown.
        return "running" if self.hub.device.get("live") is True else None
