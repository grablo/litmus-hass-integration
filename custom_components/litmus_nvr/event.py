"""Litmus alerts as HA event entities — one per camera, event_type = the alert recipe id."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitmusConfigEntry
from .const import EVENT_TYPES, SIGNAL_EVENT
from .entity import LitmusCameraEntity, unique_id
from .hub import LitmusHub

# How long a notification link opens without a Home Assistant login (the NVR's own alert links last as long).
SIGNED_PATH_EXPIRY = timedelta(days=7)


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities(LitmusAlertEvent(hub, cam["id"]) for cam in hub.cameras)


class LitmusAlertEvent(LitmusCameraEntity, EventEntity):
    """Fires when a Litmus alert rule on this camera goes off."""

    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "alert"

    def __init__(self, hub: LitmusHub, camera_id: str) -> None:
        super().__init__(hub, camera_id)
        self._attr_unique_id = unique_id(hub, "alert", camera_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_EVENT.format(self.hub.entry.entry_id), self._event)
        )

    @callback
    def _event(self, camera_id: str, ev: dict[str, Any]) -> None:
        if camera_id != self.camera_id:
            return
        kind = ev.get("kind") or "person"
        if kind not in EVENT_TYPES:
            return
        snapshot = self._signed(ev.get("snapshot"))
        clip = self._signed(ev.get("clip"))   # 2.4.0 — absent from older NVRs
        # Litmus alerts carry no detection confidence today (the NVR sends 0) — leave it out rather than show a false 0.0
        # that an automation could compare against (Frigate's score is a real confidence).
        score = ev.get("score")
        has_score = isinstance(score, (int, float)) and score > 0
        self._trigger_event(
            kind,
            {
                "event_id": ev.get("id"),
                "rule_id": ev.get("rule"),
                "title": ev.get("title"),
                "message": ev.get("body"),
                "names": ev.get("names") or [],
                "plate": ev.get("plate"),
                "label": ev.get("label"),
                **({"score": score} if has_score else {}),
                "sound": ev.get("sound"),
                # Signed with async_sign_path (7 days), so a phone notification opens it without a Home Assistant login.
                "snapshot": snapshot,
                # The recording of that event, signed the same way (Frigate notifications/<id>/clip.mp4). While the event is still
                # going on it plays what has been recorded so far (Frigate event_clip: end = now); after it ends, the whole event.
                "clip": clip,
            },
        )
        self.async_write_ha_state()

    def _signed(self, nvr_path: Any) -> str | None:
        """The NVR's 'events/<id>/snapshot.jpg?exp=..&sig=..' → our signed proxy path. The NVR's own exp/sig are dropped:
        the proxy calls the NVR with the integration token. Signed for the content user, not whoever set the integration up."""
        if not isinstance(nvr_path, str) or not nvr_path:
            return None
        path = f"/api/litmus_nvr/notifications/{self.hub.entry.entry_id}/{nvr_path.split('?', 1)[0]}"
        return async_sign_path(self.hass, path, SIGNED_PATH_EXPIRY, use_content_user=True)
