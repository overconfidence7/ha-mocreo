"""Binary sensor platform for MOCREO."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .bridge import MocreoBridge
from .entity import MocreoEntity
from .parser import KIND_BINARY, FieldSpec

_DEVICE_CLASSES = {dc.value for dc in BinarySensorDeviceClass}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up MOCREO binary sensors."""
    bridge: MocreoBridge = entry.runtime_data

    @callback
    def _add(items: list[tuple[str, FieldSpec]]) -> None:
        async_add_entities(
            MocreoBinarySensor(bridge, node_key, spec) for node_key, spec in items
        )

    bridge.register_platform(KIND_BINARY, _add)


class MocreoBinarySensor(MocreoEntity, BinarySensorEntity, RestoreEntity):
    """A boolean reading (leak, probe, connectivity, rule alarm)."""

    def __init__(self, bridge: MocreoBridge, node_key: str, spec: FieldSpec) -> None:
        super().__init__(bridge, node_key, spec)
        self._restored: bool | None = None
        if spec.device_class and spec.device_class in _DEVICE_CLASSES:
            self._attr_device_class = BinarySensorDeviceClass(spec.device_class)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state in ("on", "off"):
                self._restored = last.state == "on"

    @property
    def is_on(self) -> bool | None:
        value = self.raw_value
        if value is not None:
            return bool(value)
        if self._restored is not None:
            return self._restored
        # Nothing has ever been seen for this field.  For a derived leak sensor
        # that means the node has only sent scheduled reports, which omit
        # `water_level` entirely -- so "no leak has been reported" is the
        # honest reading, and it is the one automations can use.  Reporting
        # unknown here is what left these sensors unavailable indefinitely,
        # and it was self-perpetuating: the restored state was itself
        # unavailable, so the next restart started from the same place.
        if self.spec.assume_off:
            return False
        return None

    @property
    def available(self) -> bool:
        if self.raw_value is None:
            if self._restored is not None:
                return True
            if self.spec.assume_off:
                node = self.node
                return node is not None and node.online
        return super().available

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs = super().extra_state_attributes or {}
        if self.spec.assume_off and self.raw_value is None and self._restored is None:
            # Distinguishes "assumed dry" from "reported dry" without changing
            # the state an automation triggers on.
            attrs["assumed_state"] = True
        return attrs
