"""Constants for the Litmus NVR integration."""

from __future__ import annotations

DOMAIN = "litmus_nvr"
NAME = "Litmus NVR"
MANUFACTURER = "Litmus"

DEFAULT_PORT = 8128
CONF_TOKEN = "token"
CONF_DEVICE_ID = "device_id"

API_VERSION = 1

# Presence classes reported by the NVR (HomeHub.h kClass*).
CLASSES = ("person", "vehicle", "animal", "sound")

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
]

# Dispatcher signals (formatted with the config entry id).
SIGNAL_STATE = DOMAIN + "_state_{}"
SIGNAL_PRESENCE = DOMAIN + "_presence_{}"
SIGNAL_IDENTITY = DOMAIN + "_identity_{}"
SIGNAL_EVENT = DOMAIN + "_event_{}"
