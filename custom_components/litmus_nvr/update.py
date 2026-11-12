"""NVR software version (install happens in the Litmus app — no install feature, like Frigate's container update)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.update import UpdateEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitmusConfigEntry
from .api import LitmusError
from .entity import LitmusEntity, unique_id
from .hub import LitmusHub

SCAN_INTERVAL = timedelta(hours=6)


async def async_setup_entry(hass: HomeAssistant, entry: LitmusConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([LitmusUpdate(entry.runtime_data)], update_before_add=True)


class LitmusUpdate(LitmusEntity, UpdateEntity):
    """Installed vs latest Litmus NVR version."""

    _attr_should_poll = True
    _attr_translation_key = "software"
    _attr_release_url = "https://litmusnvr.com/download"

    def __init__(self, hub: LitmusHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = unique_id(hub, "update", "controller")

    @callback
    def _state_changed(self) -> None:
        # The NVR reports its version in every full state (sent again after it restarts) — after an update,
        # show the new installed version now instead of at the next 6-hour poll.
        version = self.hub.device.get("version")
        if version and version != self._attr_installed_version:
            self.async_schedule_update_ha_state(force_refresh=True)
            return
        super()._state_changed()

    async def async_update(self) -> None:
        try:
            data = await self.hub.client.update_info()
        except LitmusError:
            return
        self._attr_installed_version = data.get("installed") or None
        latest = data.get("latest") or None
        # Unknown latest (offline) → report the installed version so HA does not claim an update.
        self._attr_latest_version = latest or self._attr_installed_version
