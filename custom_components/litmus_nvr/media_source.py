"""Recordings in Home Assistant's Media panel — the place Frigate's integration puts its clips (media_source.py).

Tree: Litmus NVR (one per config entry) › {People, Faces, Vehicles, Animals, <cameras>}.
  · kind folders show Litmus cards (the same person / vehicle / animal grouped, with the card photo) › that card's appearances
    (photo + the recording that holds it, opened at the appearance) — the card list the Litmus app shows (user request 2026-11-13).
  · camera › day › recording. Days come from one query over the last 30 days; a day
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
KINDS = ("person", "face", "vehicle", "animal")
KIND_TITLES = {
    "ko": {"person": "사람", "face": "얼굴", "vehicle": "차량", "animal": "동물"},
    "en": {"person": "People", "face": "Faces", "vehicle": "Vehicles", "animal": "Animals"},
}
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
        if len(parts) not in (3, 4) or parts[1] != "rec":
            raise Unresolvable("Unknown recording")
        self._hub(parts[0])
        from .views import LitmusRecordingView
        url = LitmusRecordingView.path_for(parts[0], parts[2])
        if len(parts) == 4 and parts[3].isdigit():
            url += f"#t={int(parts[3]) / 1000:.1f}"   # open at the appearance (W3C media fragment; the player seeks there)
        return PlayMedia(url, "video/mp4")

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
        if len(parts) == 3 and parts[1] == "kind" and parts[2] in KINDS:
            return await self._kind_node(parts[0], parts[2])
        if len(parts) == 4 and parts[1] == "card" and parts[2] in KINDS and parts[3].isdigit():
            return await self._card_node(parts[0], parts[2], int(parts[3]))
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
        titles = KIND_TITLES["ko" if (self.hass.config.language or "").startswith("ko") else "en"]
        kinds = [self._folder(f"{entry_id}/kind/{k}", titles[k], None) for k in KINDS]
        cams = [
            self._folder(f"{entry_id}/cam/{cam['id']}", cam.get("name") or cam["id"], None)
            for cam in hub.cameras
        ]
        return self._folder(entry_id, title, kinds + cams)

    def _thumb(self, entry_id: str, query: str | None) -> str | None:
        return f"/api/litmus_nvr/{entry_id}/thumb.jpg?{query}" if query else None

    async def _kind_node(self, entry_id: str, kind: str) -> BrowseMediaSource:
        hub = self._hub(entry_id)
        try:
            cards = await hub.client.cards(kind, DAYS, 200)
        except LitmusError as err:
            raise Unresolvable(f"Litmus NVR is not reachable: {err}") from err
        ko = (self.hass.config.language or "").startswith("ko")
        children = []
        for c in cards:
            last = dt_util.as_local(dt_util.utc_from_timestamp(int(c.get("last") or 0) / 1000))
            label = c.get("name") or c.get("plate") or c.get("species") or ""
            when = last.strftime("%m/%d %H:%M")
            count = int(c.get("count") or 0)
            title = " · ".join(x for x in (label, when, (f"{count}회" if ko else f"{count}×") if count else "") if x)
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/card/{kind}/{int(c['id'])}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=title,
                    can_play=False,
                    can_expand=True,
                    thumbnail=self._thumb(entry_id, c.get("thumb")),
                )
            )
        titles = KIND_TITLES["ko" if ko else "en"]
        node = self._folder(f"{entry_id}/kind/{kind}", titles[kind], children)
        node.children_media_class = MediaClass.DIRECTORY
        return node

    async def _card_node(self, entry_id: str, kind: str, card_id: int) -> BrowseMediaSource:
        hub = self._hub(entry_id)
        try:
            apps = await hub.client.appearances(kind, card_id, DAYS, 200)
        except LitmusError as err:
            raise Unresolvable(f"Litmus NVR is not reachable: {err}") from err
        children = []
        for a in apps:
            t = dt_util.as_local(dt_util.utc_from_timestamp(int(a.get("first") or 0) / 1000))
            title = " · ".join(x for x in (t.strftime("%m/%d %H:%M:%S"), a.get("camera_name") or "", a.get("plate") or "") if x)
            rec = a.get("recording")
            ident = f"{entry_id}/rec/{rec}/{int(a.get('offset_ms') or 0)}" if rec else f"{entry_id}/card/{kind}/{card_id}"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=ident,
                    media_class=MediaClass.VIDEO,
                    media_content_type="video/mp4",
                    title=title,
                    can_play=bool(rec),
                    can_expand=False,
                    thumbnail=self._thumb(entry_id, a.get("thumb")),
                )
            )
        titles = KIND_TITLES["ko" if (self.hass.config.language or "").startswith("ko") else "en"]
        node = self._folder(f"{entry_id}/card/{kind}/{card_id}", titles[kind], children)
        node.children_media_class = MediaClass.VIDEO
        return node

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
