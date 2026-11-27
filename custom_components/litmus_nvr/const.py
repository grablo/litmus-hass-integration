"""Constants for the Litmus NVR integration."""

from __future__ import annotations

DOMAIN = "litmus_nvr"
NAME = "Litmus NVR"
MANUFACTURER = "Litmus"

DEFAULT_PORT = 8128
CONF_TOKEN = "token"
CONF_DEVICE_ID = "device_id"
CONF_ADDON_SLUG = "addon_slug"   # set when found through the Supervisor (HA add-on) — the sidebar opens its ingress page
CONF_TITLE_HOST = "title_host"   # the address the entry title ends with, `<place> (<address>)` — kept in step when the address changes

API_VERSION = 1

# Presence classes reported by the NVR (HomeHub.h kClass*).
CLASSES = ("person", "vehicle", "animal", "sound")
# Classes a detection zone reports (no sound — zones are areas of the picture).
ZONE_CLASSES = ("person", "vehicle", "animal")
# Frigate's artificial "all" object — every tracked object on a camera or in a zone (get_cameras_and_objects include_all).
ALL = "all"

# Performance stats poll (Frigate's stats coordinator: api/stats every 5 s).
STATS_INTERVAL_SECONDS = 5

# Alert kinds — the same ids as litmus-web spot/alert/recipeCatalog.ts.
EVENT_TYPES = [
    "person",
    "face_known",
    "face_unknown",
    "face_specific",
    "vehicle_any",
    "vehicle_known",
    "vehicle_unknown",
    "vehicle_specific",
    "animal_any",
    "animal_specific",
    "text_person",
    "text_vehicle",
    "text_animal",
    "sound",
    # Litmus 2.8.0 events — the NVR sends these recipe ids too (litmus-web recipeCatalog `event_*`).
    "event_fire",
    "event_fall",
    "event_loiter",
]

# Dispatcher signals (formatted with the config entry id).
SIGNAL_STATE = DOMAIN + "_state_{}"
SIGNAL_PRESENCE = DOMAIN + "_presence_{}"
SIGNAL_ZONE_PRESENCE = DOMAIN + "_zone_presence_{}"
SIGNAL_IDENTITY = DOMAIN + "_identity_{}"
SIGNAL_EVENT = DOMAIN + "_event_{}"
