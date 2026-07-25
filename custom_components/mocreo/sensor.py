"""Sensor platform for MOCREO."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import MocreoBridge
from .entity import MocreoEntity
from .parser import KIND_SENSOR, FieldSpec, severity_sort_key

_DEVICE_CLASSES = {dc.value for dc in SensorDeviceClass}
_STATE_CLASSES = {sc.value for sc in SensorStateClass}

# Keys whose value is a word ("Low"/"Medium"/"High") rather than a number.
_SEVERITY_KEYS = {"severity", "level", "leaklevel", "leak_level"}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up MOCREO sensors."""
    bridge: MocreoBridge = entry.runtime_data

    @callback
    def _add(items: list[tuple[str, FieldSpec]]) -> None:
        async_add_entities(
            MocreoSensor(bridge, node_key, spec) for node_key, spec in items
        )

    bridge.register_platform(KIND_SENSOR, _add)


class MocreoSensor(MocreoEntity, RestoreSensor):
    """A numeric or textual reading from a MOCREO node."""

    def __init__(self, bridge: MocreoBridge, node_key: str, spec: FieldSpec) -> None:
        super().__init__(bridge, node_key, spec)
        self._restored: Any = None

        if spec.device_class and spec.device_class in _DEVICE_CLASSES:
            self._attr_device_class = SensorDeviceClass(spec.device_class)
        if spec.unit:
            self._attr_native_unit_of_measurement = spec.unit
        if (
            spec.state_class
            and spec.state_class in _STATE_CLASSES
            and spec.key not in _SEVERITY_KEYS
        ):
            self._attr_state_class = SensorStateClass(spec.state_class)
        if spec.key in _SEVERITY_KEYS:
            # Severity can arrive as a word, so no state class / unit.
            self._attr_state_class = None
            self._attr_native_unit_of_measurement = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._restored = last.native_value

    @property
    def native_value(self) -> Any:
        value = self.raw_value
        if value is None:
            return self._restored
        return value

    @property
    def available(self) -> bool:
        if self.raw_value is None and self._restored is not None:
            return True
        return super().available

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs = super().extra_state_attributes or {}
        if self.spec.key in _SEVERITY_KEYS:
            rank = severity_sort_key(self.raw_value)
            if rank >= 0:
                attrs["severity_rank"] = rank
        return attrs
