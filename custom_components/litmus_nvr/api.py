"""Client for the Litmus NVR local integration API (/api/v1/ext/*)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class LitmusError(Exception):
    """Base error."""


class LitmusAuthError(LitmusError):
    """Token rejected (401) or bad credentials."""


class LitmusConnectionError(LitmusError):
    """NVR unreachable or returned a server error."""


class LitmusThrottledError(LitmusError):
    """Too many login attempts."""


class LitmusClient:
    """Small async client. One instance per config entry."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, token: str | None = None) -> None:
        self._session = session
        self._base = f"http://{host}:{port}/api/v1/ext"
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
            return await resp.json(content_type=None)

    async def info(self) -> dict[str, Any]:
        return await self._json("GET", "info")

    async def create_token(self, username: str, password: str, name: str = "Home Assistant") -> str:
        resp = await self._request("POST", "tokens", json={"username": username, "password": password, "name": name})
        async with resp:
            if resp.status in (401, 403):
                raise LitmusAuthError("bad credentials")
            if resp.status >= 400:
                raise LitmusConnectionError(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)
        return str(data["token"])

    async def state(self) -> dict[str, Any]:
        return await self._json("GET", "state")

    async def update_info(self) -> dict[str, Any]:
        return await self._json("GET", "update")

    async def snapshot(self, camera_id: str, width: int | None = None, height: int | None = None) -> bytes | None:
        params = {}
        if width:
            params["w"] = str(width)
        if height:
            params["h"] = str(height)
        resp = await self._request("GET", f"cameras/{camera_id}/snapshot.jpg", params=params)
        async with resp:
            if resp.status != 200:
                return None
            return await resp.read()

    async def event_snapshot(self, path: str) -> tuple[int, bytes]:
        """path = 'events/<id>/snapshot.jpg?exp=..&sig=..' as sent by the NVR."""
        try:
            resp = await self._session.get(f"{self._base}/{path}", timeout=aiohttp.ClientTimeout(total=10))
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise LitmusConnectionError(str(err)) from err
        async with resp:
            return resp.status, await resp.read()

    async def webrtc_offer(self, camera_id: str, offer_sdp: str, session_id: str) -> str:
        data = await self._json(
            "POST",
            f"cameras/{camera_id}/webrtc",
            json={"sdp": offer_sdp, "session_id": session_id},
            timeout=12,
        )
        return str(data["sdp"])

    async def webrtc_candidate(self, session_id: str, candidate: str, sdp_mid: str | None) -> None:
        resp = await self._request(
            "POST", f"webrtc/{session_id}/candidate", json={"candidate": candidate, "sdpMid": sdp_mid or ""}
        )
        resp.release()

    async def webrtc_close(self, session_id: str) -> None:
        try:
            resp = await self._request("DELETE", f"webrtc/{session_id}")
            resp.release()
        except LitmusError:
            pass

    async def ws_connect(self) -> aiohttp.ClientWebSocketResponse:
        try:
            return await self._session.ws_connect(
                f"{self._base}/ws", headers=self._headers(), heartbeat=30
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise LitmusAuthError("unauthorized") from err
            raise LitmusConnectionError(str(err)) from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise LitmusConnectionError(str(err)) from err
