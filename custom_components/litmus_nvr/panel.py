"""Sidebar entry that opens the NVR's own web UI (docs/LITMUS_HOME_INTEGRATION.md §17.13).

One panel per NVR, registered while the entry is loaded. The panel (frontend/litmus-panel.js) shows a button that opens the NVR's
page in a new tab — the NVR forbids framing (X-Frame-Options: DENY, clickjacking protection), so it is not shown inside.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_JS_URL = f"/{DOMAIN}/frontend/litmus-panel.js"
_JS_PATH = Path(__file__).parent / "frontend" / "litmus-panel.js"
_STATIC_KEY = f"{DOMAIN}_panel_static"
# The file is served with long cache headers; a content hash in the module URL makes browsers fetch a changed panel after an update.
_JS_VERSION = hashlib.sha256(_JS_PATH.read_bytes()).hexdigest()[:12]
# Sidebar logo — the HACS way (custom_components/hacs/frontend.py + iconset.js): a single-colour icon set registered in the
# browser as window.customIcons["litmus"], loaded on every page with add_extra_js_url, used as the panel icon "litmus:logo".
# Registered once per HA run: static paths cannot be registered again when the entry reloads (same note in HACS).
_ICONSET_URL = f"/{DOMAIN}/frontend/iconset.js"
_ICONSET_PATH = Path(__file__).parent / "frontend" / "iconset.js"
_ICONSET_VERSION = hashlib.sha256(_ICONSET_PATH.read_bytes()).hexdigest()[:12]
_ICONSET_KEY = f"{DOMAIN}_iconset_registered"
_ICON = "litmus:logo"


def panel_path(device_id: str) -> str:
    """Sidebar URL path for one NVR (stable per device)."""
    return f"litmus-nvr-{device_id[:6].lower()}"


async def async_add_panel(hass: HomeAssistant, device_id: str, host: str, port: int, title: str) -> None:
    if not hass.data.get(_STATIC_KEY):
        await hass.http.async_register_static_paths([StaticPathConfig(_JS_URL, str(_JS_PATH), True)])
        hass.data[_STATIC_KEY] = True
    if not hass.data.get(_ICONSET_KEY):
        await hass.http.async_register_static_paths([StaticPathConfig(_ICONSET_URL, str(_ICONSET_PATH), True)])
        frontend.add_extra_js_url(hass, f"{_ICONSET_URL}?v={_ICONSET_VERSION}")
        hass.data[_ICONSET_KEY] = True
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    path = panel_path(device_id)
    if path in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, path, warn_if_unknown=False)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=path,
        webcomponent_name="litmus-nvr-panel",
        sidebar_title=title,
        sidebar_icon=_ICON,
        module_url=f"{_JS_URL}?v={_JS_VERSION}",
        config={"url": f"http://{url_host}:{port}/"},
        require_admin=False,
    )


def async_remove_panel(hass: HomeAssistant, device_id: str) -> None:
    frontend.async_remove_panel(hass, panel_path(device_id), warn_if_unknown=False)
