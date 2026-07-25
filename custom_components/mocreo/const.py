"""Constants for the MOCREO integration."""

from __future__ import annotations

DOMAIN = "mocreo"

CONF_PREFIX = "prefix"
CONF_TEMP_HUNDREDTHS = "temp_hundredths"
CONF_DISCOVERED = "discovered"

DEFAULT_PREFIX = "mocreo"
DEFAULT_TEMP_HUNDREDTHS = True

MANUFACTURER = "MOCREO"

# Topic actions published by the hub.
ACTION_DATA = "data"
ACTION_EVENT = "event"
ACTION_STATE = "state"
ACTION_CONFIG = "config"

SIGNAL_NEW_READINGS = f"{DOMAIN}_new_readings"
SIGNAL_NODE_UPDATED = f"{DOMAIN}_node_updated_{{}}"

SAVE_DELAY = 15.0
