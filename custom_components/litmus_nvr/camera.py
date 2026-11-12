"""Cameras — native WebRTC straight to the NVR.

Contract: HA core camera/webrtc.py. HA sends the session id to the frontend *before* calling
async_handle_async_webrtc_offer, so browser candidates can arrive before the NVR has answered.
go2rtc (core components/go2rtc) handles this by registering the session first; here the candidates are held
per session until the answer is back, then forwarded. HA's ICE servers (STUN/TURN) are passed to the NVR, as go2rtc does.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    WebRTCAnswer,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from webrtc_models import RTCIceCandidateInit

from . import LitmusConfigEntry
from .api import LitmusError
from .entity import LitmusCameraEntity, unique_id
from .hub import LitmusHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities(LitmusCamera(hub, cam["id"]) for cam in hub.cameras)


def _ice_servers(config: Any) -> list[dict[str, Any]] | None:
    """HA's RTCIceServer list → the browser shape the NVR accepts ({urls, username, credential})."""
    try:
        servers = config.configuration.ice_servers
    except AttributeError:
        return None
    out: list[dict[str, Any]] = []
    for s in servers or []:
        urls = getattr(s, "urls", None)
        if not urls:
            continue
        item: dict[str, Any] = {"urls": urls if isinstance(urls, list) else [urls]}
        if getattr(s, "username", None):
            item["username"] = s.username
        if getattr(s, "credential", None):
            item["credential"] = s.credential
        out.append(item)
    return out


class LitmusCamera(LitmusCameraEntity, Camera):
    """One NVR camera."""

    _attr_name = None   # the entity takes the camera device's name (camera.front_door)
    # STREAM is required for HA to offer WebRTC at all (camera/entity.py camera_capabilities);
    # because async_handle_async_webrtc_offer is implemented, HA offers WebRTC only (no HLS).
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, hub: LitmusHub, camera_id: str) -> None:
        LitmusCameraEntity.__init__(self, hub, camera_id)
        Camera.__init__(self)
        self._attr_unique_id = unique_id(hub, "camera", camera_id)
        # session id -> candidates received before the NVR answered (None once answered)
        self._pending: dict[str, list[RTCIceCandidateInit] | None] = {}
        self._closed: set[str] = set()

    @property
    def is_streaming(self) -> bool:
        return bool(self.cam.get("online"))

    @property
    def available(self) -> bool:
        return super().available and bool(self.cam.get("online"))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {"camera_id": self.camera_id, "detect": bool(self.cam.get("detect", True))}

    async def stream_source(self) -> str | None:
        return None

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        try:
            return await self.hub.client.snapshot(self.camera_id, width, height)
        except LitmusError:
            return None

    async def async_handle_async_webrtc_offer(self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage) -> None:
        self._pending[session_id] = []
        try:
            answer = await self.hub.client.webrtc_offer(
                self.camera_id, offer_sdp, session_id, _ice_servers(self.async_get_webrtc_client_configuration())
            )
        except LitmusError as err:
            self._pending.pop(session_id, None)
            self._closed.discard(session_id)
            send_message(WebRTCError("litmus_webrtc_failed", str(err)))
            return
        except Exception as err:  # noqa: BLE001 — the frontend must always get an answer or an error
            self._pending.pop(session_id, None)
            self._closed.discard(session_id)
            _LOGGER.exception("Unexpected error while starting Litmus WebRTC")
            send_message(WebRTCError("litmus_webrtc_failed", str(err)))
            return
        if session_id in self._closed:
            # The frontend went away while the NVR was answering — do not leave the NVR session holding a slot.
            self._closed.discard(session_id)
            self._pending.pop(session_id, None)
            await self.hub.client.webrtc_close(session_id)
            return
        early = self._pending.get(session_id) or []
        self._pending[session_id] = None
        send_message(WebRTCAnswer(answer))
        for cand in early:
            await self._send_candidate(session_id, cand)

    async def async_on_webrtc_candidate(self, session_id: str, candidate: RTCIceCandidateInit) -> None:
        if not candidate.candidate:
            return
        held = self._pending.get(session_id)
        if held is not None:
            held.append(candidate)   # the NVR has not answered yet
            return
        await self._send_candidate(session_id, candidate)

    async def _send_candidate(self, session_id: str, candidate: RTCIceCandidateInit) -> None:
        try:
            await self.hub.client.webrtc_candidate(session_id, candidate.candidate, candidate.sdp_mid)
        except LitmusError:
            pass

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        if session_id in self._pending and self._pending[session_id] is not None:
            # Still answering: the NVR cancels a reserved session on DELETE; if the answer still comes back, close again.
            self._closed.add(session_id)
        else:
            self._pending.pop(session_id, None)
        self.hass.async_create_task(self.hub.client.webrtc_close(session_id))
