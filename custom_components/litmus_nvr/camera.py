"""Cameras — native WebRTC straight to the NVR (Frigate's FrigateCameraWebRTC pattern, but candidates are forwarded)."""

from __future__ import annotations

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


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities(LitmusCamera(hub, cam["id"]) for cam in hub.cameras)


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
        try:
            answer = await self.hub.client.webrtc_offer(self.camera_id, offer_sdp, session_id)
        except LitmusError as err:
            send_message(WebRTCError("litmus_webrtc_failed", str(err)))
            return
        send_message(WebRTCAnswer(answer))

    async def async_on_webrtc_candidate(self, session_id: str, candidate: RTCIceCandidateInit) -> None:
        if not candidate.candidate:
            return
        try:
            await self.hub.client.webrtc_candidate(session_id, candidate.candidate, candidate.sdp_mid)
        except LitmusError:
            pass

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        self.hass.async_create_task(self.hub.client.webrtc_close(session_id))
