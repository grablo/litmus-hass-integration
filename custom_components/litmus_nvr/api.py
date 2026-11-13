"""Client for the Litmus NVR local integration API (/api/v1/ext/*)."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import aiohttp

# Errors that can surface while reading or decoding a response body (not only while sending the request).
_BODY_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError)


class LitmusError(Exception):
    """Base error."""


class LitmusAuthError(LitmusError):
    """Token rejected (401) or bad credentials."""


class LitmusConnectionError(LitmusError):
    """NVR unreachable, returned a server error or an unreadable body."""


class LitmusNotRunningError(LitmusError):
    """The NVR is stopped or starting — cameras cannot be changed now."""


class LitmusThrottledError(LitmusError):
    """Too many login attempts."""


class LitmusNotSetUpError(LitmusError):
    """The NVR has no local account yet (HTTP 409)."""


class LitmusTokenLimitError(LitmusError):
    """The NVR could not issue another integration token (HTTP 500 TOKEN_FAILED — limit of 20 reached)."""


def _seg(value: str) -> str:
    """One URL path segment."""
    return quote(str(value), safe="")


class LitmusClient:
    """Small async client. One instance per config entry."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, token: str | None = None) -> None:
        self._session = session
        netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host  # IPv6 literal
        self._base = f"http://{netloc}:{port}/api/v1/ext"
        self._token = token

    @property
    def base(self) -> str:
        return self._base

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _request(self, method: str, path: str, *, timeout: float = 10, **kwargs: Any) -> aiohttp.ClientResponse:
        try:
            resp = await self._session.request(
                method,
                f"{self._base}/{path}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=timeout),
                **kwargs,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise LitmusConnectionError(str(err)) from err
        if resp.status == 401:
            resp.release()
            raise LitmusAuthError("unauthorized")
        if resp.status == 429:
            resp.release()
            raise LitmusThrottledError("throttled")
        return resp

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = await self._request(method, path, **kwargs)
        async with resp:
            if resp.status >= 400:
                raise LitmusConnectionError(f"HTTP {resp.status}")
            try:
                return await resp.json(content_type=None)
            except _BODY_ERRORS as err:
                raise LitmusConnectionError(f"bad response: {err}") from err

    async def info(self) -> dict[str, Any]:
        data = await self._json("GET", "info")
        if not isinstance(data, dict):
            raise LitmusConnectionError("unexpected response")
        return data

    async def create_token(self, username: str, password: str, name: str = "Home Assistant") -> str:
        resp = await self._request("POST", "tokens", json={"username": username, "password": password, "name": name})
        async with resp:
            if resp.status in (401, 403):
                raise LitmusAuthError("bad credentials")
            if resp.status == 409:
                raise LitmusNotSetUpError("not set up")
            if resp.status == 500:
                raise LitmusTokenLimitError("token limit")
            if resp.status >= 400:
                raise LitmusConnectionError(f"HTTP {resp.status}")
            try:
                data = await resp.json(content_type=None)
                return str(data["token"])
            except _BODY_ERRORS as err:
                raise LitmusConnectionError(f"bad response: {err}") from err

    async def delete_own_token(self) -> None:
        """Revoke the token this client uses (after reauth, so old tokens do not pile up)."""
        try:
            resp = await self._request("DELETE", "tokens/self")
            resp.release()
        except LitmusError:
            pass

    async def state(self) -> dict[str, Any]:
        data = await self._json("GET", "state")
        if not isinstance(data, dict):
            raise LitmusConnectionError("unexpected response")
        return data

    async def update_info(self) -> dict[str, Any]:
        data = await self._json("GET", "update")
        return data if isinstance(data, dict) else {}

    async def recordings(self, camera_id: str, from_ms: int, to_ms: int, limit: int = 200) -> list[dict[str, Any]]:
        """Finished recordings of one camera, newest first: [{id, start (epoch ms), duration_ms (-1 = unknown)}]."""
        data = await self._json(
            "GET", "recordings", params={"camera": camera_id, "from": str(from_ms), "to": str(to_ms), "limit": str(limit)}
        )
        items = data.get("recordings") if isinstance(data, dict) else None
        return [i for i in items if isinstance(i, dict) and i.get("id")] if isinstance(items, list) else []

    async def open_recording(self, recording_id: str, range_header: str | None) -> aiohttp.ClientResponse:
        """Open the mp4 of a recording (Range passed through). The caller streams and releases the response."""
        headers = {"Range": range_header} if range_header else {}
        try:
            resp = await self._session.get(
                f"{self._base}/recordings/{_seg(recording_id)}/video.mp4",
                headers={**self._headers(), **headers},
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60),
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise LitmusConnectionError(str(err)) from err
        return resp

    async def cards(self, kind: str, days: int = 30, limit: int = 100, from_ms: int | None = None, to_ms: int | None = None) -> list[dict[str, Any]]:
        """Litmus cards (same person / vehicle / animal grouped), newest first: [{id, name?, plate?, species?, count, first, last, thumb}].
        from_ms/to_ms (epoch ms) narrow it to a window — the Media panel's day folders."""
        params = {"kind": kind, "days": str(days), "limit": str(limit)}
        if from_ms is not None and to_ms is not None:
            params.update({"from": str(from_ms), "to": str(to_ms)})
        data = await self._json("GET", "cards", params=params)
        items = data.get("cards") if isinstance(data, dict) else None
        return [i for i in items if isinstance(i, dict) and i.get("id")] if isinstance(items, list) else []

    async def appearances(self, kind: str, card_id: int, days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
        """One card's appearances, newest first: [{camera, camera_name, first, last, thumb, recording?, offset_ms?}]."""
        data = await self._json("GET", f"cards/{_seg(kind)}/{int(card_id)}/appearances", params={"days": str(days), "limit": str(limit)})
        items = data.get("appearances") if isinstance(data, dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    async def card_thumb(self, query: str) -> bytes | None:
        """A card / appearance photo (the crop Litmus shows on the card). None when the NVR has no prepared crop for it."""
        resp = await self._request("GET", f"cards/thumb.jpg?{query}")
        async with resp:
            if resp.status != 200:
                return None
            try:
                return await resp.read()
            except _BODY_ERRORS:
                return None

    async def set_push(self, on: bool) -> None:
        """Litmus phone notifications on/off (kept by the NVR across restarts; turning the HA integration off in Litmus re-enables them)."""
        resp = await self._request("POST", "notifications", json={"on": on})
        async with resp:
            if resp.status >= 400:
                raise LitmusConnectionError(f"HTTP {resp.status}")

    async def set_detect(self, camera_id: str, on: bool) -> None:
        """Turn detection on or off for one camera (runtime only — the NVR's place settings win after it restarts)."""
        resp = await self._request("POST", f"cameras/{_seg(camera_id)}/detect", json={"on": on})
        async with resp:
            if resp.status == 409:
                raise LitmusNotRunningError("not running")
            if resp.status >= 400:
                raise LitmusConnectionError(f"HTTP {resp.status}")

    async def snapshot(self, camera_id: str, width: int | None = None, height: int | None = None) -> bytes | None:
        params = {}
        if width:
            params["w"] = str(width)
        if height:
            params["h"] = str(height)
        resp = await self._request("GET", f"cameras/{_seg(camera_id)}/snapshot.jpg", params=params)
        async with resp:
            if resp.status != 200:
                return None
            try:
                return await resp.read()
            except _BODY_ERRORS as err:
                raise LitmusConnectionError(str(err)) from err

    async def event_snapshot(self, path: str, *, with_token: bool = False) -> tuple[int, bytes]:
        """path = 'events/<id>/snapshot.jpg?exp=..&sig=..' as sent by the NVR.

        The notification proxy forwards it without our token (the signature is the credential);
        the image entity sends the token as well so the picture still loads after the 7-day signature expires.
        """
        try:
            resp = await self._session.get(
                f"{self._base}/{path}",
                headers=self._headers() if with_token else None,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise LitmusConnectionError(str(err)) from err
        async with resp:
            try:
                return resp.status, await resp.read()
            except _BODY_ERRORS as err:
                raise LitmusConnectionError(str(err)) from err

    async def webrtc_offer(
        self, camera_id: str, offer_sdp: str, session_id: str, ice_servers: list[dict[str, Any]] | None = None
    ) -> str:
        body: dict[str, Any] = {"sdp": offer_sdp, "session_id": session_id}
        if ice_servers is not None:
            body["ice_servers"] = ice_servers
        data = await self._json("POST", f"cameras/{_seg(camera_id)}/webrtc", json=body, timeout=12)
        try:
            return str(data["sdp"])
        except _BODY_ERRORS as err:
            raise LitmusConnectionError(f"bad answer: {err}") from err

    async def webrtc_candidate(self, session_id: str, candidate: str, sdp_mid: str | None) -> None:
        resp = await self._request(
            "POST", f"webrtc/{_seg(session_id)}/candidate", json={"candidate": candidate, "sdpMid": sdp_mid or ""}
        )
        resp.release()

    async def webrtc_close(self, session_id: str) -> None:
        try:
            resp = await self._request("DELETE", f"webrtc/{_seg(session_id)}")
            resp.release()
        except LitmusError:
            pass

    async def ws_connect(self) -> aiohttp.ClientWebSocketResponse:
        try:
            async with asyncio.timeout(10):
                return await self._session.ws_connect(f"{self._base}/ws", headers=self._headers(), heartbeat=30)
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise LitmusAuthError("unauthorized") from err
            raise LitmusConnectionError(str(err)) from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise LitmusConnectionError(str(err)) from err
