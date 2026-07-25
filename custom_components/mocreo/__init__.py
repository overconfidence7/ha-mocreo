"""The MOCREO integration.

Connects MOCREO Smart Hubs (H3 / H5 / H5 Pro / H6) to Home Assistant over the
hub's local MQTT output, so LoRa sensors such as the LS1/LS2 temperature probe
and the LW1/SW2 water leak detector become native Home Assistant entities.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .bridge import MocreoBridge
from .const import (
    CONF_PREFIX,
    CONF_TEMP_HUNDREDTHS,
    DEFAULT_PREFIX,
    DEFAULT_TEMP_HUNDREDTHS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Typed config entry alias (plain assignment so it also parses on Python < 3.12).
MocreoConfigEntry = ConfigEntry[MocreoBridge]


async def async_setup_entry(hass: HomeAssistant, entry: MocreoConfigEntry) -> bool:
    """Set up MOCREO from a config entry."""
    from homeassistant.components import mqtt

    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT broker is not available yet")

    prefix = entry.options.get(
        CONF_PREFIX, entry.data.get(CONF_PREFIX, DEFAULT_PREFIX)
    ).strip("/")
    temp_hundredths = entry.options.get(
        CONF_TEMP_HUNDREDTHS,
        entry.data.get(CONF_TEMP_HUNDREDTHS, DEFAULT_TEMP_HUNDREDTHS),
    )

    bridge = MocreoBridge(hass, entry, prefix or DEFAULT_PREFIX, bool(temp_hundredths))
    entry.runtime_data = bridge

    # Restore entities from previous runs first, so the platforms pick them up
    # immediately instead of waiting for the next (slow) LoRa transmission.
    bridge.async_prepare()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await bridge.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MocreoConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    bridge: MocreoBridge | None = getattr(entry, "runtime_data", None)
    if bridge is not None:
        await bridge.async_stop()
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: MocreoConfigEntry) -> None:
    """Reload only when a user-facing setting actually changed.

    The bridge writes its discovered-entity cache back into ``entry.data``,
    which also fires this listener - reloading on that would loop forever.
    """
    bridge: MocreoBridge | None = getattr(entry, "runtime_data", None)
    if bridge is None:
        return
    prefix = str(
        entry.options.get(CONF_PREFIX, entry.data.get(CONF_PREFIX, DEFAULT_PREFIX))
    ).strip("/")
    temp_hundredths = bool(
        entry.options.get(
            CONF_TEMP_HUNDREDTHS,
            entry.data.get(CONF_TEMP_HUNDREDTHS, DEFAULT_TEMP_HUNDREDTHS),
        )
    )
    if prefix == bridge.prefix and temp_hundredths == bridge.temp_hundredths:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: MocreoConfigEntry, device_entry
) -> bool:
    """Allow removing a stale sensor from the UI."""
    bridge: MocreoBridge | None = getattr(entry, "runtime_data", None)
    if bridge is None:
        return True
    node_keys = {
        ident[1]
        for ident in device_entry.identifiers
        if ident[0] == DOMAIN
    }
    for key in node_keys:
        bridge.nodes.pop(key, None)
    return True
