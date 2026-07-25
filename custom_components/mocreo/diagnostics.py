"""Diagnostics for MOCREO.

Downloading diagnostics from the device page dumps the last raw MQTT payload
seen for every node -- the fastest way to find out exactly which JSON keys a
new sensor model sends.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .bridge import MocreoBridge


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    bridge: MocreoBridge = entry.runtime_data
    return {
        "entry": {
            "data": {k: v for k, v in entry.data.items() if k != "discovered"},
            "options": dict(entry.options),
        },
        "bridge": bridge.diagnostics(),
    }
