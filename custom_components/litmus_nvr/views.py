"""Alert snapshot and clip proxy for phone notifications (Frigate's NotificationsProxyView serves the same files).

Both views need a Home Assistant login. The event entity hands out their paths signed with `async_sign_path` (7 days),
so a phone notification still loads them without an authorization header. The NVR is called with the integration's token.
"""

from __future__ import annotations

import re

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import LitmusError
from .const import DOMAIN

_EVENT_ID = re.compile(r"[0-9]{1,16}-[0-9a-f]{8}")   # HomeHub: "<epochMs>-<8hex>"


class LitmusNotificationView(HomeAssistantView):
    """GET /api/litmus_nvr/notifications/{entry_id}/events/{event_id}/snapshot.jpg — HA login or a signed path."""

    url = "/api/litmus_nvr/notifications/{entry_id}/events/{event_id}/snapshot.jpg"
    name = "api:litmus_nvr:notification_snapshot"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, entry_id: str, event_id: str) -> web.Response:
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
            return web.Response(status=404)
        if not _EVENT_ID.fullmatch(event_id):
            return web.Response(status=404)
        try:
            status, body = await entry.runtime_data.client.event_snapshot(f"events/{event_id}/snapshot.jpg")
        except LitmusError:
            return web.Response(status=502)
        if status != 200:
            return web.Response(status=status if status in (403, 404) else 502)
        return web.Response(body=body, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})


async def _stream(request: web.Request, upstream, cache: str) -> web.StreamResponse:
    """Pass an mp4 through with its Range headers (the player seeks)."""
    headers = {
        k: upstream.headers[k]
        for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
        if k in upstream.headers
    }
    headers["Cache-Control"] = cache
    resp = web.StreamResponse(status=upstream.status, headers=headers)
    await resp.prepare(request)
    if request.method != "HEAD":
        async for chunk in upstream.content.iter_chunked(256 * 1024):
            await resp.write(chunk)
    await resp.write_eof()
    return resp


class LitmusNotificationClipView(HomeAssistantView):
    """GET /api/litmus_nvr/notifications/{entry_id}/events/{event_id}/clip.mp4 — the alert's recording (Frigate
    notifications/<id>/clip.mp4). HA login or a signed path, like the snapshot. 404 while the NVR has nothing recorded yet.
    """

    url = "/api/litmus_nvr/notifications/{entry_id}/events/{event_id}/clip.mp4"
    name = "api:litmus_nvr:notification_clip"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def head(self, request: web.Request, entry_id: str, event_id: str) -> web.StreamResponse:
        return await self.get(request, entry_id, event_id)

    async def get(self, request: web.Request, entry_id: str, event_id: str) -> web.StreamResponse:
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or getattr(entry, "runtime_data", None) is None:
            return web.Response(status=404)
        if not _EVENT_ID.fullmatch(event_id):
            return web.Response(status=404)
        try:
            upstream = await entry.runtime_data.client.open_event_clip(event_id, request.headers.get("Range"))
        except LitmusError:
            return web.Response(status=502)
        async with upstream:
            if upstream.status not in (200, 206):
                return web.Response(status=upstream.status if upstream.status in (403, 404, 416) else 502)
            return await _stream(request, upstream, "private, max-age=600")


_REC_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}~[A-Za-z0-9_-]{1,64}~[0-9]{1,16}(\.[0-9]{1,6})?")   # ExtApi recordingId


class LitmusRecordingView(HomeAssistantView):
    """GET /api/litmus_nvr/{entry_id}/recordings/{recording_id}/video.mp4 — HA login (or a signed path from media_source).

    Streams the NVR's mp4 with Range passed through, so the Media panel player can seek (Frigate proxies its clips the same way).
    """

    url = "/api/litmus_nvr/{entry_id}/recordings/{recording_id}/video.mp4"
    name = "api:litmus_nvr:recording"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    @staticmethod
    def path_for(entry_id: str, recording_id: str) -> str:
        return f"/api/litmus_nvr/{entry_id}/recordings/{recording_id}/video.mp4"

    async def get(self, request: web.Request, entry_id: str, recording_id: str) -> web.StreamResponse:
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or getattr(entry, "runtime_data", None) is None:
            return web.Response(status=404)
        if not _REC_ID.fullmatch(recording_id):
            return web.Response(status=404)
        try:
            upstream = await entry.runtime_data.client.open_recording(recording_id, request.headers.get("Range"))
        except LitmusError:
            return web.Response(status=502)
        async with upstream:
            if upstream.status not in (200, 206):
                return web.Response(status=upstream.status if upstream.status in (404, 416) else 502)
            return await _stream(request, upstream, "private, max-age=3600")


_THUMB_KEYS = {"kind", "var", "t", "off", "box"}
_THUMB_VAL = re.compile(r"[A-Za-z0-9_.,:-]{1,128}")


class LitmusThumbView(HomeAssistantView):
    """GET /api/litmus_nvr/{entry_id}/thumb.jpg?kind=&var=&t=&off=&box= — card photos in the Media panel (HA login or signed path,
    like Frigate's ThumbnailsProxyView). Only the five keys the NVR issues are forwarded."""

    url = "/api/litmus_nvr/{entry_id}/thumb.jpg"
    name = "api:litmus_nvr:thumb"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or getattr(entry, "runtime_data", None) is None:
            return web.Response(status=404)
        q = {k: v for k, v in request.query.items() if k in _THUMB_KEYS}
        if set(q) != _THUMB_KEYS or not all(_THUMB_VAL.fullmatch(v) for v in q.values()):
            return web.Response(status=404)
        from urllib.parse import urlencode
        try:
            body = await entry.runtime_data.client.card_thumb(urlencode(q))
        except LitmusError:
            return web.Response(status=502)
        if not body:
            return web.Response(status=404)
        return web.Response(body=body, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
