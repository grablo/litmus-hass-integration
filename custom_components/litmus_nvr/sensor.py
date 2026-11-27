"""Per-camera sensors — last recognised face / plate (Frigate sensor_recognized_*, cleared after 60 s) and object counts (Frigate `<cam>_<obj>_count`).

Also Frigate's zone counts, the "all" count, and its stats sensors (diagnostic, off by default, polled every 5 s).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed

from . import LitmusConfigEntry
from .api import LitmusError
from .binary_sensor import all_presence, allowed_classes, enabled_classes
from .const import (
    ALL,
    DOMAIN,
    SIGNAL_IDENTITY,
    SIGNAL_PRESENCE,
    SIGNAL_ZONE_PRESENCE,
    STATS_INTERVAL_SECONDS,
    ZONE_CLASSES,
)
from .entity import async_prune_stale, LitmusCameraEntity, LitmusEntity, LitmusZoneEntity, unique_id
from .hub import LitmusHub

_LOGGER = logging.getLogger(__name__)

# Frigate's count unit ("objects") and fps unit.
UNIT_OBJECTS = "objects"
UNIT_FPS = "fps"
# Litmus has one detector (the object model); Frigate names the sensor after the detector.
DETECTOR = "object"


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    # Recognised face / plate only when this place recognises faces / vehicles (plates are read by vehicle recognition) — same rule as
    # the presence sensors: no entity for a feature that is off. An empty class list (older Litmus) means everything.
    # Litmus 2.8+ decides this per camera (the camera's `classes`), older NVRs per place.
    entities: list[SensorEntity] = []
    for cam in hub.cameras:
        allowed = allowed_classes(hub, cam)
        kinds = [k for k, cls in (("face", "face"), ("plate", "vehicle")) if allowed is None or cls in allowed]
        entities.extend(LitmusIdentitySensor(hub, cam["id"], kind) for kind in kinds)
    # Counts only for classes that have a count (sound has none) and that this camera detects — same rule as the presence sensors.
    for cam in hub.cameras:
        counted = enabled_classes(hub, ZONE_CLASSES, cam)
        entities.extend(LitmusCountSensor(hub, cam["id"], cls) for cls in counted)
        if counted:
            entities.append(LitmusAllCountSensor(hub, cam["id"], counted))
        for z in cam.get("zones") or []:
            if not isinstance(z, dict) or not z.get("name") or not counted:
                continue
            entities.extend(LitmusZoneCountSensor(hub, cam["id"], str(z["name"]), cls) for cls in counted)
            entities.append(LitmusZoneAllCountSensor(hub, cam["id"], str(z["name"]), counted))
    entities.append(LitmusStatusSensor(hub))
    entities.extend(await _stats_entities(hass, entry, hub))
    async_add_entities(entities)
    async_prune_stale(hass, entry, "sensor", entities)   # QA D-400 — sensors of detection turned off on a camera


# ── counts ──


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


def _count(p: dict[str, Any]) -> int:
    return int(p.get("count") or 0) if p.get("on") else 0


class LitmusCountSensor(LitmusCameraEntity, SensorEntity):
    """How many people / vehicles / animals the camera sees right now (0 when none, held for the NVR's off delay)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_OBJECTS

    def __init__(self, hub: LitmusHub, camera_id: str, cls: str) -> None:
        super().__init__(hub, camera_id)
        self.cls = cls
        self._attr_unique_id = unique_id(hub, "count", f"{camera_id}_{cls}")
        self._attr_translation_key = f"{cls}_count"

    @property
    def native_value(self) -> int:
        return _count((self.cam.get("presence") or {}).get(self.cls) or {})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_PRESENCE.format(self.hub.entry.entry_id), self._presence)
        )

    @callback
    def _presence(self, camera_id: str, cls: str) -> None:
        if camera_id == self.camera_id and cls == self.cls:
            self.async_write_ha_state()


class LitmusAllCountSensor(LitmusCameraEntity, SensorEntity):
    """People + vehicles + animals on a camera — Frigate's "All count"."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_OBJECTS
    _attr_translation_key = f"{ALL}_count"

    def __init__(self, hub: LitmusHub, camera_id: str, classes: list[str]) -> None:
        super().__init__(hub, camera_id)
        self.classes = classes
        self._attr_unique_id = unique_id(hub, "count", f"{camera_id}_{ALL}")

    @property
    def native_value(self) -> int:
        return all_presence(self.cam.get("presence") or {}, self.classes)[1]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_PRESENCE.format(self.hub.entry.entry_id), self._presence)
        )

    @callback
    def _presence(self, camera_id: str, cls: str) -> None:
        if camera_id == self.camera_id and cls in self.classes:
            self.async_write_ha_state()


class LitmusZoneCountSensor(LitmusZoneEntity, SensorEntity):
    """How many of one class are inside a detection zone (Frigate zone count)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_OBJECTS

    def __init__(self, hub: LitmusHub, camera_id: str, zone: str, cls: str) -> None:
        super().__init__(hub, camera_id, zone)
        self.cls = cls
        self._attr_unique_id = unique_id(hub, "zone_count", f"{camera_id}_{zone}_{cls}")
        self._attr_translation_key = f"{cls}_count"

    @property
    def native_value(self) -> int:
        return _count((self.z.get("presence") or {}).get(self.cls) or {})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ZONE_PRESENCE.format(self.hub.entry.entry_id), self._presence)
        )

    @callback
    def _presence(self, camera_id: str, zone: str, cls: str) -> None:
        if camera_id == self.camera_id and zone == self.zone and cls == self.cls:
            self.async_write_ha_state()


class LitmusZoneAllCountSensor(LitmusZoneEntity, SensorEntity):
    """Everything inside a detection zone."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_OBJECTS
    _attr_translation_key = f"{ALL}_count"

    def __init__(self, hub: LitmusHub, camera_id: str, zone: str, classes: list[str]) -> None:
        super().__init__(hub, camera_id, zone)
        self.classes = classes
        self._attr_unique_id = unique_id(hub, "zone_count", f"{camera_id}_{zone}_{ALL}")

    @property
    def native_value(self) -> int:
        return all_presence(self.z.get("presence") or {}, self.classes)[1]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ZONE_PRESENCE.format(self.hub.entry.entry_id), self._presence)
        )

    @callback
    def _presence(self, camera_id: str, zone: str, cls: str) -> None:
        if camera_id == self.camera_id and zone == self.zone and cls in self.classes:
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


# ── stats (Frigate FrigateStatsSensor family: diagnostic, disabled by default) ──


class LitmusStatsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Reads /stats every 5 s — only while a stats sensor is enabled (a coordinator polls only with listeners)."""

    def __init__(self, hass: HomeAssistant, entry: LitmusConfigEntry, hub: LitmusHub) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} stats",
            update_interval=timedelta(seconds=STATS_INTERVAL_SECONDS),
        )
        self.hub = hub

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.hub.client.stats()
        except LitmusError as err:
            raise UpdateFailed(str(err)) from err


async def _stats_entities(hass: HomeAssistant, entry: LitmusConfigEntry, hub: LitmusHub) -> list[SensorEntity]:
    """No stats sensors for an NVR without /stats (Litmus before this 2.4.0 build answers 404) — nothing changes for it."""
    coordinator = LitmusStatsCoordinator(hass, entry, hub)
    await coordinator.async_refresh()
    if not coordinator.last_update_success or not isinstance(coordinator.data, dict):
        return []
    data = coordinator.data
    entities: list[SensorEntity] = [
        LitmusStatsSensor(coordinator, "uptime", ("service", "uptime"), UnitOfTime.SECONDS, SensorDeviceClass.DURATION),
        LitmusStatsSensor(coordinator, "cpu_usage", ("cpu_usages", "litmus", "cpu"), PERCENTAGE),
        LitmusStatsSensor(coordinator, "memory_usage", ("cpu_usages", "litmus", "mem"), PERCENTAGE),
        LitmusStatsSensor(
            coordinator,
            "inference_speed",
            ("detectors", DETECTOR, "inference_speed"),
            UnitOfTime.MILLISECONDS,
            SensorDeviceClass.DURATION,
        ),
    ]
    for gpu in (data.get("gpu_usages") or {}):
        entities.append(
            LitmusStatsSensor(
                coordinator, "gpu_load", ("gpu_usages", gpu, "gpu"), PERCENTAGE, key=gpu, placeholders={"gpu": gpu}
            )
        )
    for cam in hub.cameras:
        for field in ("camera_fps", "detection_fps"):
            entities.append(LitmusCameraStatsSensor(coordinator, cam["id"], field))
    return entities


def _number(raw: Any) -> float | None:
    """Frigate reads "12.3" and "35%" the same way (float(value.replace('%', '')))."""
    if raw is None:
        return None
    try:
        return round(float(str(raw).replace("%", "")), 2)
    except ValueError:
        return None


def _dig(data: Any, path: tuple[str, ...]) -> Any:
    for k in path:
        if not isinstance(data, dict):
            return None
        data = data.get(k)
    return data


class LitmusStatsSensor(CoordinatorEntity[LitmusStatsCoordinator], LitmusEntity, SensorEntity):
    """One number from /stats on the NVR device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: LitmusStatsCoordinator,
        name: str,
        path: tuple[str, ...],
        unit: str,
        device_class: SensorDeviceClass | None = None,
        *,
        key: str = "",
        placeholders: dict[str, str] | None = None,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        LitmusEntity.__init__(self, coordinator.hub)
        self.path = path
        self._attr_unique_id = unique_id(coordinator.hub, "stats", f"{name}_{key}" if key else name)
        self._attr_translation_key = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if placeholders:
            self._attr_translation_placeholders = placeholders

    @property
    def available(self) -> bool:
        return self.hub.available and self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        return _number(_dig(self.coordinator.data, self.path))


class LitmusCameraStatsSensor(CoordinatorEntity[LitmusStatsCoordinator], LitmusCameraEntity, SensorEntity):
    """Camera fps / detection fps on the camera device (Frigate CameraFpsSensor)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_FPS

    def __init__(self, coordinator: LitmusStatsCoordinator, camera_id: str, field: str) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        LitmusCameraEntity.__init__(self, coordinator.hub, camera_id)
        self.field = field
        self._attr_unique_id = unique_id(coordinator.hub, "stats", f"{camera_id}_{field}")
        self._attr_translation_key = field

    @property
    def available(self) -> bool:
        return LitmusCameraEntity.available.fget(self) and self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        # While the NVR is stopped the camera list in /stats is empty — no number rather than a false 0.
        return _number(_dig(self.coordinator.data, ("cameras", self.camera_id, self.field)))
