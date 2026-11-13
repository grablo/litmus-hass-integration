"""Push hub: one WebSocket to the NVR, state kept here, changes sent to entities via the dispatcher.

Pattern: local_push like the UniFi Protect / Reolink integrations — no MQTT broker is required.
The NVR always sends a full {"type":"state"} first (and again whenever cameras change), then deltas.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import LitmusAuthError, LitmusClient, LitmusConnectionError
from .const import DOMAIN, SIGNAL_EVENT, SIGNAL_IDENTITY, SIGNAL_PRESENCE, SIGNAL_STATE

_LOGGER = logging.getLogger(__name__)


class LitmusHub:
    """Holds the NVR state for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: LitmusClient) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.state: dict[str, Any] = {}
        self.available = False
        self.last_event: dict[str, dict[str, Any]] = {}   # camera id -> last alert event
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    # ── state helpers ──
    @property
    def device(self) -> dict[str, Any]:
        return self.state.get("device", {})

    @property
    def device_id(self) -> str:
        return str(self.device.get("id") or self.entry.unique_id or self.entry.entry_id)

    @property
    def cameras(self) -> list[dict[str, Any]]:
        return list(self.state.get("cameras", []))

    @property
    def cameras_authoritative(self) -> bool:
        """Whether the camera list may add or remove devices.

        While the NVR is stopped or starting, it reports the cameras of its last run as offline (`device.live` false).
        An empty list in that state means "not known yet" (first start after an update, before any run), not "no cameras",
        so devices and entities must not be removed — they show as unavailable instead.
        """
        return bool(self.state.get("cameras")) or self.device.get("live", True) is not False

    @property
    def classes(self) -> list[str]:
        return list(self.state.get("classes", []))

    def camera(self, camera_id: str) -> dict[str, Any] | None:
        for cam in self.state.get("cameras", []):
            if cam.get("id") == camera_id:
                return cam
        return None

    # ── lifecycle ──
    async def async_start(self) -> None:
        """Fetch the first state (raises on auth/connection error) and start the push loop."""
        self.state = await self.client.state()
        self.available = True
        self._task = self.entry.async_create_background_task(self.hass, self._run(), "litmus_nvr websocket")

    async def async_stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()

    async def _run(self) -> None:
        backoff = 1
        while True:
            try:
                self._ws = await self.client.ws_connect()
                async for msg in self._ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        # Back off again only once the NVR actually talks — a socket that opens and drops at once
                        # would otherwise be retried every second.
                        backoff = 1
                        try:
                            data = msg.json()
                            if isinstance(data, dict):
                                self._handle(data)
                        except Exception:  # noqa: BLE001 — one bad message must not drop the connection
                            _LOGGER.debug("Ignored a malformed message from Litmus NVR", exc_info=True)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            except LitmusAuthError:
                _LOGGER.warning("Litmus NVR rejected the token — reauthentication required")
                self._set_available(False)
                self.entry.async_start_reauth(self.hass)
                return
            except LitmusConnectionError as err:
                _LOGGER.debug("Litmus NVR connection failed: %s", err)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — keep the loop alive whatever the NVR sends
                _LOGGER.exception("Unexpected error in the Litmus NVR push loop")
            self._set_available(False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    @callback
    def _set_available(self, available: bool) -> None:
        if self.available == available:
            return
        self.available = available
        async_dispatcher_send(self.hass, SIGNAL_STATE.format(self.entry.entry_id))

    @callback
    def _follow_renames(self) -> None:
        """A camera or the place renamed in Litmus renames its device here (as reolink does on a channel rename).

        Compared with the device registry, not the previous message, so a rename made while Home Assistant was down is picked up too.
        Only the device's own name changes: a name the user gave the device in Home Assistant (name_by_user) is kept, and entity ids
        stay the same.
        """
        reg = dr.async_get(self.hass)
        wanted: list[tuple[str, str]] = [
            (f"{self.device_id}:{cam.get('id')}", str(cam.get("name")))
            for cam in self.state.get("cameras", [])
            if cam.get("id") and cam.get("name")
        ]
        if self.device.get("name"):
            wanted.append((self.device_id, str(self.device.get("name"))))
        # HA 2026.11 deprecates async_get_device (identifiers are unique per config entry now); look up within our entry.
        by_identifier = getattr(reg, "async_get_device_by_identifier", None)
        for ident, name in wanted:
            if by_identifier is not None:
                device = by_identifier((DOMAIN, ident), self.entry.entry_id)
            else:  # pragma: no cover - HA before 2026.11
                device = reg.async_get_device(identifiers={(DOMAIN, ident)})
            if device is not None and device.name != name:
                reg.async_update_device(device.id, name=name)

    @callback
    def _handle(self, data: dict[str, Any]) -> None:
        kind = data.get("type")
        eid = self.entry.entry_id
        if kind == "state":
            old_ids = {c.get("id") for c in self.state.get("cameras", [])}
            self.state = data.get("data", {})
            self.available = True
            self._follow_renames()
            async_dispatcher_send(self.hass, SIGNAL_STATE.format(eid))
            new_ids = {c.get("id") for c in self.state.get("cameras", [])}
            if new_ids != old_ids and self.cameras_authoritative:
                # Camera added or removed — reload so entities and devices follow (Frigate does the same on config change).
                self.hass.config_entries.async_schedule_reload(eid)
            return
        if kind == "presence":
            cam = self.camera(str(data.get("camera")))
            if cam is not None:
                cls = str(data.get("class"))
                cam.setdefault("presence", {})[cls] = {
                    "on": bool(data.get("on")),
                    "count": int(data.get("count") or 0),
                    "name": data.get("name"),
                    "ts": data.get("ts"),
                }
                async_dispatcher_send(self.hass, SIGNAL_PRESENCE.format(eid), cam.get("id"), cls)
            return
        if kind == "identity":
            cam = self.camera(str(data.get("camera")))
            if cam is not None:
                k = str(data.get("kind"))
                cam.setdefault("identity", {})[k] = {"value": data.get("value"), "ts": data.get("ts")}
                async_dispatcher_send(self.hass, SIGNAL_IDENTITY.format(eid), cam.get("id"), k)
            return
        if kind == "event":
            event = data.get("event") or {}
            camera_id = str(event.get("camera"))
            self.last_event[camera_id] = event
            async_dispatcher_send(self.hass, SIGNAL_EVENT.format(eid), camera_id, event)
            return
