"""Diagnostics download (Settings › Devices & services › Litmus NVR › ⋮ › Download diagnostics) — Frigate, UniFi Protect and Reolink
all provide one. The token is redacted; the rest is what support needs: NVR version, cameras and their state, classes, connection."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import LitmusConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: LitmusConfigEntry) -> dict[str, Any]:
    hub = entry.runtime_data
    cams = []
    for cam in hub.cameras:
        cams.append(
            {
                "id": cam.get("id"),
                "name": cam.get("name"),
                "online": cam.get("online"),
                "detect": cam.get("detect"),
                "audio": cam.get("audio"),
                "size": [cam.get("width"), cam.get("height")],
                "presence": cam.get("presence"),
            }
        )
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "available": hub.available,
        "device": hub.device,
        "classes": hub.classes,
        "cameras": cams,
    }
