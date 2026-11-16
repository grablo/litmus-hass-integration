"""Snapshot of the last alert per camera (Frigate's image_best_snapshot, but taken from the alert itself)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitmusConfigEntry
from .api import LitmusError
from .const import SIGNAL_EVENT
from .entity import LitmusCameraEntity, unique_id
from .hub import LitmusHub


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities(LitmusLastAlertImage(hass, hub, cam["id"]) for cam in hub.cameras)


class LitmusLastAlertImage(LitmusCameraEntity, ImageEntity):
    """Picture taken when the last alert on this camera went off."""

    _attr_translation_key = "last_alert"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, hub: LitmusHub, camera_id: str) -> None:
        LitmusCameraEntity.__init__(self, hub, camera_id)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = unique_id(hub, "last_alert", camera_id)
        self._path: str | None = None
        self._bytes: bytes | None = None
        # The last alert the NVR still holds — so a restart or reload does not leave the image empty until the next alert.
        last = hub.last_event.get(camera_id)
        if last:
            self._show(last)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_EVENT.format(self.hub.entry.entry_id), self._event)
        )

    def _show(self, ev: dict[str, Any]) -> None:
        self._path = str(ev["snapshot"])
        self._bytes = None
        ts = ev.get("ts")
        self._attr_image_last_updated = (
            datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if isinstance(ts, (int, float)) else datetime.now(timezone.utc)
        )

    @callback
    def _event(self, camera_id: str, ev: dict[str, Any]) -> None:
        if camera_id != self.camera_id or not ev.get("snapshot"):
            return
        self._show(ev)
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        if self._bytes is not None:
            return self._bytes
        if not self._path:
            return None
        try:
            # With the token too: the signature expires after 7 days, the token does not.
            status, body = await self.hub.client.event_snapshot(self._path, with_token=True)
        except LitmusError:
            return None
        if status == 200:
            self._bytes = body
            return body
        return None
