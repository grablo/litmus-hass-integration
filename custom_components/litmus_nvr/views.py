"""Notification snapshot proxy — opens without a Home Assistant login, like Frigate's NotificationsProxyView.

Frigate trusts the event id (+ optional expiry). Litmus signs instead: the NVR adds `exp` and an HMAC `sig`
to every alert snapshot path and checks them itself; this view only forwards the request.
"""

from __future__ import annotations

import re

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import LitmusError
from .const import DOMAIN

_EVENT_ID = re.compile(r"[0-9]{1,16}-[0-9a-f]{8}")   # HomeHub: "<epochMs>-<8hex>"
_SIG = re.compile(r"[0-9a-f]{64}")                    # HMAC-SHA256 hex


class LitmusNotificationView(HomeAssistantView):
    """GET /api/litmus_nvr/notifications/{entry_id}/events/{event_id}/snapshot.jpg?exp=&sig="""

    url = "/api/litmus_nvr/notifications/{entry_id}/events/{event_id}/snapshot.jpg"
    name = "api:litmus_nvr:notification_snapshot"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, entry_id: str, event_id: str) -> web.Response:
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
            return web.Response(status=404)
        exp = request.query.get("exp", "")
        sig = request.query.get("sig", "")
        # Unauthenticated endpoint: accept only the exact shapes the NVR issues, so nothing else is forwarded.
        if not (_EVENT_ID.fullmatch(event_id) and exp.isdigit() and _SIG.fullmatch(sig)):
            return web.Response(status=401)
        try:
            status, body = await entry.runtime_data.client.event_snapshot(
                f"events/{event_id}/snapshot.jpg?exp={exp}&sig={sig}"
            )
        except LitmusError:
            return web.Response(status=502)
        if status != 200:
            return web.Response(status=status if status in (403, 404) else 502)
        return web.Response(body=body, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})


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
            headers = {
                k: upstream.headers[k]
                for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
                if k in upstream.headers
            }
            headers["Cache-Control"] = "private, max-age=3600"
            resp = web.StreamResponse(status=upstream.status, headers=headers)
            await resp.prepare(request)
            async for chunk in upstream.content.iter_chunked(256 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
            return resp
