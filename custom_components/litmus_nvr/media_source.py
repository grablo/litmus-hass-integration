"""Recordings in Home Assistant's Media panel — the place Frigate's integration puts its clips (media_source.py).

Tree: Litmus NVR (one per config entry) › camera › day › recording. Days come from one query over the last 30 days; a day
lists that day's recordings. Playing a recording goes through LitmusRecordingView (an authenticated HA proxy with Range
support), so the browser never needs the NVR's address or token.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import LitmusError
from .const import DOMAIN, NAME

DAYS = 30          # how far back the camera folder looks for days with recordings
DAY_LIMIT = 500    # recordings per day (the NVR caps one answer at 500)


async def async_get_media_source(hass: HomeAssistant) -> LitmusMediaSource:
    return LitmusMediaSource(hass)


def _entries(hass: HomeAssistant) -> list[Any]:
    return [e for e in hass.config_entries.async_entries(DOMAIN) if getattr(e, "runtime_data", None) is not None]


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class LitmusMediaSource(MediaSource):
    """Litmus NVR recordings."""

    name = NAME

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _hub(self, entry_id: str) -> Any:
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or getattr(entry, "runtime_data", None) is None:
            raise Unresolvable("Litmus NVR is not set up")
        return entry.runtime_data

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        parts = (item.identifier or "").split("/")
        if len(parts) != 3 or parts[1] != "rec":
            raise Unresolvable("Unknown recording")
        self._hub(parts[0])
        from .views import LitmusRecordingView
        return PlayMedia(LitmusRecordingView.path_for(parts[0], parts[2]), "video/mp4")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        ident = item.identifier or ""
        if not ident:
            entries = _entries(self.hass)
            if len(entries) == 1:
                return self._entry_node(entries[0].entry_id)
            return self._folder("", NAME, [self._entry_node(e.entry_id, children=False) for e in entries])
        parts = ident.split("/")
        if len(parts) == 1:
            return self._entry_node(parts[0])
        if len(parts) == 3 and parts[1] == "cam":
            return await self._camera_node(parts[0], parts[2])
        if len(parts) == 4 and parts[1] == "day":
            return await self._day_node(parts[0], parts[2], parts[3])
        raise Unresolvable("Unknown media item")

    # ── nodes ──
    def _folder(self, ident: str, title: str, children: list[BrowseMediaSource] | None, thumb: str | None = None) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=ident,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            children_media_class=MediaClass.DIRECTORY if children is None or any(c.can_expand for c in children) else MediaClass.VIDEO,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
            thumbnail=thumb,
        )

    def _entry_node(self, entry_id: str, children: bool = True) -> BrowseMediaSource:
        hub = self._hub(entry_id)
        title = hub.device.get("name") or NAME
        if not children:
            return self._folder(entry_id, title, None)
        cams = [
            self._folder(f"{entry_id}/cam/{cam['id']}", cam.get("name") or cam["id"], None)
            for cam in hub.cameras
        ]
        return self._folder(entry_id, title, cams)

    async def _camera_node(self, entry_id: str, camera_id: str) -> BrowseMediaSource:
        hub = self._hub(entry_id)
        cam = hub.camera(camera_id) or {}
        now = dt_util.now()
        try:
            recs = await hub.client.recordings(camera_id, _ms(now - timedelta(days=DAYS)), _ms(now), 500)
        except LitmusError as err:
            raise Unresolvable(f"Litmus NVR is not reachable: {err}") from err
        days: dict[str, int] = {}
        for r in recs:
            day = dt_util.as_local(dt_util.utc_from_timestamp(int(r["start"]) / 1000)).date().isoformat()
            days[day] = days.get(day, 0) + 1
        children = [
            self._folder(f"{entry_id}/day/{camera_id}/{day}", day, None) for day in sorted(days, reverse=True)
        ]
        return self._folder(f"{entry_id}/cam/{camera_id}", cam.get("name") or camera_id, children)

    async def _day_node(self, entry_id: str, camera_id: str, day: str) -> BrowseMediaSource:
        hub = self._hub(entry_id)
        try:
            start = dt_util.start_of_local_day(datetime.fromisoformat(day).date())
        except ValueError as err:
            raise Unresolvable("Bad day") from err
        end = start + timedelta(days=1)
        try:
            recs = await hub.client.recordings(camera_id, _ms(start), _ms(end), DAY_LIMIT)
        except LitmusError as err:
            raise Unresolvable(f"Litmus NVR is not reachable: {err}") from err
        children = []
        for r in recs:
            t = dt_util.as_local(dt_util.utc_from_timestamp(int(r["start"]) / 1000))
            dur = int(r.get("duration_ms") or -1)
            title = t.strftime("%H:%M:%S") + (f" · {max(1, round(dur / 1000))}s" if dur > 0 else "")
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/rec/{r['id']}",
                    media_class=MediaClass.VIDEO,
                    media_content_type="video/mp4",
                    title=title,
                    can_play=True,
                    can_expand=False,
                )
            )
        cam = hub.camera(camera_id) or {}
        return self._folder(f"{entry_id}/day/{camera_id}/{day}", f"{cam.get('name') or camera_id} · {day}", children)
